"""호스트 시험용 Docker/프로세스 경계. 실제 네트워크·DB·비밀에 접근하지 않는다."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def load_host():
    controller = Path(__file__).resolve().parents[2] / "controller"
    path = controller / "compose_host.py"
    assert path.is_file(), (
        "missing capability: common-host deploy/query/render_compose/CLI 구현 대기; "
        "이 실패는 동작 RED 또는 검증 coverage로 인정하지 않는다"
    )
    import importlib.util
    spec = importlib.util.spec_from_file_location("compose_host", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(controller))
    try:
        exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
    finally:
        sys.path.pop(0)
    for name in ("deploy", "query", "render_compose", "main"):
        assert callable(getattr(module, name, None)), f"missing capability: compose_host.{name}"
    return module


class HostRuntime:
    """Docker JSON 관측을 합성한다. SQLite/flock/query는 production 코드가 수행한다.

    image config ID는 manifest와 의도적으로 다르다. inspect는 Go template 없이
    Docker의 표준 JSON을 반환한다. 구현은 config ID로 image inspect해야 한다.
    """

    def __init__(self, request, root):
        self.request = copy.deepcopy(request)
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.active = None
        self.calls = []
        self.events = []
        self.fail_pull = None
        self.wrong_digest = False
        self.fail_rollback = False
        self.unknown_on_up = False
        self.db_change = {}
        self.health_change = {}
        self.saw_new = False
        self.gated = False

    def record(self, event):
        self.events.append(event)
        with (self.root / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(event) + "\n")

    def is_new(self, doc):
        return doc["services"]["backend"]["image"] == self.request["bundle"]["images"]["backend"]

    def runner(self, argv, **kwargs):
        assert not isinstance(argv, str), "runner은 shell 문자열 대신 argv 배열을 받는다"
        argv = [str(value) for value in argv]
        self.calls.append((argv, kwargs))
        assert not kwargs.get("shell"), "합성 비밀을 shell 명령으로 조합하지 않는다"
        if "compose" not in argv and "docker" not in Path(argv[0]).name:
            if "run" in argv and any("compose_host" in part for part in argv):
                detached_service = Path(argv[0]).name == "systemd-run" and "--wait" not in argv
                detached_session = Path(argv[0]).name == "setsid" and bool({"-f", "--fork"} & set(argv))
                assert detached_service or detached_session or kwargs.get("start_new_session") is True, (
                    "start가 분리 실행을 요청하지 않았다", argv, kwargs
                )
                # 분리 명령의 실제 run 인자를 보존하고, 외부 도구만 이 fixture로 주입한다.
                run_args = argv[argv.index("run"):]
                with open(os.devnull, "wb") as output:
                    child = subprocess.Popen(
                        [sys.executable, str(Path(__file__).resolve()), *run_args],
                        stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                        start_new_session=True, close_fds=True,
                    )
                return subprocess.CompletedProcess(argv, 0, json.dumps({"pid": child.pid}), "")
            raise AssertionError(f"외부 명령 fake 미정의: {argv!r}")
        if "compose" in argv:
            assert "build" not in argv, "운영 호스트에서 build 실행"
            doc = self.active
            for flag in ("-f", "--file"):
                if flag in argv:
                    doc = json.loads(Path(argv[argv.index(flag) + 1]).read_text())
                    break
            if "config" in argv:
                return self.completed(argv, doc or {})
            if "pull" in argv:
                selected = [name for name in ("frontend", "backend") if name in argv]
                for service in selected or ("frontend", "backend"):
                    self.record(["pull", service])
                    if service == self.fail_pull:
                        return self.completed(argv, {}, code=1)
                return self.completed(argv, {})
            if "up" in argv:
                assert {"-d", "--no-build", "--wait"} <= set(argv), argv
                assert doc and {"frontend", "backend"} <= set(doc["services"])
                self.record(["up", doc])
                new = self.is_new(doc)
                if self.fail_rollback and self.saw_new and not new:
                    return self.completed(argv, {}, code=1)
                self.active = copy.deepcopy(doc)
                self.saw_new |= new
                if self.gated:
                    (self.root / "entered.json").write_text(json.dumps({"pid": os.getpid(), "sid": os.getsid(0)}))
                    deadline = time.monotonic() + 20
                    while not (self.root / "release").exists():
                        assert time.monotonic() < deadline, "시험 gate 해제 시간 초과"
                        time.sleep(0.02)
                if self.unknown_on_up:
                    raise subprocess.TimeoutExpired(argv, 1)
                return self.completed(argv, {})
            if "stop" in argv or "down" in argv:
                self.record(["stop"])
                self.active = None
                return self.completed(argv, {})
            if "ps" in argv:
                rows = [] if self.active is None else [
                    {"ID": "container-" + name, "Name": "fixture-" + name, "Service": name,
                     "Image": self.active["services"][name]["image"], "State": "running", "Health": "healthy"}
                    for name in ("frontend", "backend")]
                if "-q" in argv or "--quiet" in argv:
                    names = [name for name in ("frontend", "backend") if name in argv]
                    ids = [row["ID"] for row in rows if not names or row["Service"] in names]
                    return subprocess.CompletedProcess(argv, 0, "\n".join(ids), "")
                return self.completed(argv, rows)
        if "inspect" in argv:
            assert "--format" not in argv and "-f" not in argv, "시험 계약: inspect의 표준 JSON 사용"
            name = argv[-1]
            if "image" in argv or name.startswith("sha256:") or "@sha256:" in name:
                refs = self.active["services"] if self.active else {
                    service: {"image": ref} for service, ref in self.request["bundle"]["images"].items()
                }
                for service, entry in refs.items():
                    if name in (self.config_id(service), entry["image"]):
                        ref = entry["image"]
                        if self.wrong_digest and self.active and self.is_new(self.active) and service == "backend":
                            ref = ref.split("@", 1)[0] + "@sha256:" + "f" * 64
                        self.record(["image-inspect", name, service])
                        return self.completed(argv, [{"Id": self.config_id(service), "RepoDigests": [ref]}])
                raise AssertionError(f"알 수 없는 image inspect: {name}")
            service = "backend" if "backend" in name else "frontend"
            self.record(["container-inspect", service])
            return self.completed(argv, [self.container(service)])
        if "version" in argv or "info" in argv:
            return self.completed(argv, {"ServerVersion": "fixture-only", "Version": "2.30.0"})
        raise AssertionError(f"Docker fake 미정의: {argv!r}")

    @staticmethod
    def config_id(service):
        return "sha256:" + ("1" if service == "frontend" else "2") * 64

    def container(self, service):
        return {"Id": "container-" + service, "ID": "container-" + service,
                "Name": "fixture-" + service, "Service": service,
                "Image": self.config_id(service), "State": {"Status": "running", "Health": {"Status": "healthy"}},
                "Config": {"Labels": {"com.docker.compose.service": service}}}

    @staticmethod
    def completed(argv, value, code=0):
        return subprocess.CompletedProcess(argv, code, json.dumps(value), "fixture failure" if code else "")

    def probe(self, target):
        db = {"connected": True, "server_version_num": 180003, "database": target["db_name"],
              "schema": "delta_notes", "table": "notes",
              "columns": ["position", "id", "title", "content", "created_at"], "schema_ok": True}
        db.update(self.db_change)
        frontend = {"healthy": bool(self.active), "commit": self.request["commit_sha"],
                    "version": self.request["commit_sha"], "cloudProvider": target["cloud"]}
        backend = {"healthy": bool(self.active), "storage": "postgres", "db": copy.deepcopy(db)}
        if self.active:
            frontend["commit"] = frontend["version"] = self.active["services"]["frontend"]["environment"]["APP_VERSION"]
            if self.is_new(self.active):
                frontend.update(self.health_change.get("frontend", {}))
                backend.update(self.health_change.get("backend", {}))
        return {"target_id": target["target_id"], "environment": target["environment"],
                "preflight": {"disk_ok": True, "docker_ok": True, "compose_ok": True,
                              "registry_ok": True, "db": db},
                "frontend": frontend, "backend": backend}


def wait_file(path, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if Path(path).exists():
            return json.loads(Path(path).read_text())
        time.sleep(0.02)
    raise AssertionError(f"분리 worker가 시험 gate에 도달하지 않음: {path}")


if __name__ == "__main__":
    # CLI도 실제 별도 Python 프로세스에서 실행한다. 도구/진단만 fake로 주입한다.
    args = sys.argv[1:]
    if args[0] == "query":
        def no_tools(*args, **kwargs):
            raise AssertionError("query CLI에서 외부 도구를 실행했다")
        runner = probe = no_tools
    else:
        request_path = Path(args[args.index("--request") + 1])
        request = json.loads(request_path.read_text())
        runtime = HostRuntime(request, request_path.parent / "runtime")
        runtime.gated = True
        runner, probe = runtime.runner, runtime.probe
    result = load_host().main(args, runner=runner, probe=probe)
    if isinstance(result, dict):
        print(json.dumps(result))
    elif isinstance(result, int):
        sys.exit(result)
