"""Trusted Jenkins job adapter. Customer URLs never select the controller or job."""

import base64
import binascii
import hashlib
import json
import re
import ssl
import time
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit

import boto3
import httpx
from botocore.config import Config

from delta_platform.adapters.builds import BuildError
from delta_platform.core.errors import Problem


def jenkins_ca(settings):
    encoded = settings.jenkins_ca_base64
    if not encoded:
        return None
    try:
        if len(encoded) > 32768:
            raise ValueError()
        pem = base64.b64decode(encoded, validate=True).decode("ascii")
        ssl.create_default_context(cadata=pem)
        return pem
    except (ValueError, UnicodeError, binascii.Error, ssl.SSLError):
        raise Problem("builder_config_invalid", "Jenkins CA 인증서를 확인하세요.", 503) from None


def jenkins_target(settings):
    if not all(
        (
            settings.jenkins_url,
            settings.jenkins_user,
            settings.jenkins_token.get_secret_value(),
            settings.build_repository,
        )
    ):
        raise Problem(
            "builder_not_configured", "Jenkins 빌드 실행기가 아직 연결되지 않았습니다.", 503
        )
    url = settings.jenkins_url.rstrip("/")
    parsed = urlsplit(url)
    local = settings.local_mode and parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or (parsed.scheme != "https" and not (local and parsed.scheme == "http"))
        or "%" in parsed.path
        or ".." in parsed.path.split("/")
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", settings.jenkins_job)
        or not re.fullmatch(
            rf"[0-9]{{12}}\.dkr\.ecr\.{re.escape(settings.build_region)}\.amazonaws\.com/[a-z0-9][a-z0-9/_.-]+",
            settings.build_repository,
        )
    ):
        raise Problem(
            "builder_config_invalid", "Jenkins 주소·Job·이미지 저장소 설정을 확인하세요.", 503
        )
    ca = jenkins_ca(settings)
    return {
        "provider": "jenkins",
        "url": url,
        "job": settings.jenkins_job,
        "repository": settings.build_repository,
        "region": settings.build_region,
        **({"ca_sha256": hashlib.sha256(ca.encode()).hexdigest()} if ca else {}),
    }


class JenkinsRunner:
    execution_timeout = 2100

    def __init__(self, settings, *, transport=None, ecr=None, sleep=time.sleep):
        self.target = jenkins_target(settings)
        self.auth = (settings.jenkins_user, settings.jenkins_token.get_secret_value())
        self.transport, self.sleep = transport, sleep
        self.verify = ssl.create_default_context(cadata=jenkins_ca(settings))
        self.job = "/job/" + self.target["job"]
        self.ecr = ecr or boto3.client(
            "ecr",
            region_name=settings.build_region,
            config=Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 2}),
        )

    def http(
        self, method, path, *, missing=False, limit=1_000_000, codes=(200, 201, 202, 204), **kwargs
    ):
        # Never follow upstream URLs or redirects; preserve credentials on this origin only.
        with httpx.Client(
            auth=self.auth,
            timeout=20,
            follow_redirects=False,
            trust_env=False,
            verify=self.verify,
            transport=self.transport,
        ) as client:
            with client.stream(method, self.target["url"] + path, **kwargs) as response:
                if missing and response.status_code == 404:
                    return None
                if response.status_code not in codes:
                    raise BuildError(
                        "jenkins_request_failed",
                        f"Jenkins 요청에 실패했습니다 (HTTP {response.status_code}). 연결·권한을 확인하세요.",
                    )
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise BuildError(
                            "jenkins_response_too_large", "Jenkins 응답이 허용 크기를 초과했습니다."
                        )
                    chunks.append(chunk)
                return httpx.Response(
                    response.status_code,
                    headers={
                        k: v
                        for k, v in response.headers.items()
                        if k not in ("content-encoding", "content-length")
                    },
                    content=b"".join(chunks),
                )

    def get(self, path, *, missing=False):
        response = self.http("GET", path, missing=missing)
        if response is None:
            return None
        try:
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except ValueError:
            raise BuildError(
                "jenkins_response_invalid", "Jenkins 응답 형식을 확인하세요."
            ) from None

    def parameters(self, claim):
        source = claim["input_snapshot"]
        if source["build_target"] != self.target:
            raise BuildError(
                "build_target_changed", "Jenkins 설정이 변경됐습니다. 기존 실행을 확인하세요."
            )
        return {
            "DELTA_RUN_ID": str(claim["id"]),
            "DELTA_COMMIT": claim["resolved_commit_sha"],
            "DELTA_SOURCE": source["repository_url"],
            "DELTA_CONTEXT": source["build_context"],
            "DELTA_DOCKERFILE": source["dockerfile_path"],
        }

    def values(self, item):
        return {
            p.get("name"): p.get("value")
            for a in item.get("actions", [])
            for p in a.get("parameters", [])
        }

    def validate(self, item, claim):
        if any(self.values(item).get(k) != v for k, v in self.parameters(claim).items()):
            raise BuildError(
                "build_identity_mismatch", "Jenkins 실행과 플랫폼 요청이 일치하지 않습니다."
            )

    @staticmethod
    def number(value):
        if type(value) is not int or value <= 0:
            raise BuildError("build_id_invalid", "Jenkins 실행 번호를 확인하세요.")
        return value

    def recover(self, claim):
        # A bounded search can fail to find old history; it must never justify a second POST.
        builds = self.get(
            self.job + "/api/json?tree=builds[number,actions[parameters[name,value]]]{0,200}"
        ).get("builds", [])
        matches = [b for b in builds if self.values(b).get("DELTA_RUN_ID") == str(claim["id"])]
        if len(matches) > 1:
            raise BuildError(
                "build_duplicate",
                "같은 요청의 Jenkins 실행이 여러 개입니다. 운영자 확인이 필요합니다.",
            )
        if matches:
            self.validate(matches[0], claim)
            return "jenkins:build:" + str(self.number(matches[0]["number"]))
        queue = self.get(
            "/queue/api/json?tree=items[id,task[url],actions[parameters[name,value]]]"
        ).get("items", [])
        matches = [
            q
            for q in queue
            if q.get("task", {}).get("url") == self.target["url"] + self.job + "/"
            and self.values(q).get("DELTA_RUN_ID") == str(claim["id"])
        ]
        if len(matches) > 1:
            raise BuildError("build_duplicate", "같은 요청이 Jenkins 큐에 여러 개 있습니다.")
        if matches:
            self.validate(matches[0], claim)
            return "jenkins:queue:" + str(self.number(matches[0]["id"]))
        raise BuildError(
            "build_submission_uncertain",
            "Jenkins 접수 여부를 확정할 수 없습니다. 자동 재접수하지 않았습니다. 실행 이력을 확인하세요.",
        )

    def execute(self, claim, store, progress):
        run_id, token = claim["id"], claim["lease_token"]
        parameters = self.parameters(claim)
        external = claim["external_id"]
        if not external:
            intent = store.prepare_submission(run_id, token, parameters)
            if intent["request"] != parameters:
                raise BuildError("build_request_changed", "저장된 Jenkins 요청과 설정이 다릅니다.")
            if claim["submission"]:
                external = self.recover(claim)
            else:
                store.heartbeat(run_id, token)
                response = self.http("POST", self.job + "/buildWithParameters", data=parameters)
                location = urljoin(self.target["url"] + "/", response.headers.get("location", ""))
                match = re.fullmatch(
                    re.escape(self.target["url"]) + r"/queue/item/([1-9][0-9]*)/?", location
                )
                if not match:
                    external = self.recover(claim)
                else:
                    external = "jenkins:queue:" + match[1]
            store.attach_build(run_id, token, external)
        prepared = store.prepare_submission(run_id, token, parameters)
        deadline = (
            datetime.fromisoformat(prepared["prepared_at"]).timestamp() + self.execution_timeout
        )
        previous = None
        while datetime.now(UTC).timestamp() < deadline:
            store.heartbeat(run_id, token)
            if external.startswith("jenkins:queue:"):
                queue_number = external.removeprefix("jenkins:queue:")
                if not re.fullmatch(r"[1-9][0-9]*", queue_number):
                    raise BuildError("build_id_invalid", "Jenkins 큐 번호를 확인하세요.")
                queue = self.get(f"/queue/item/{queue_number}/api/json", missing=True)
                if queue is None:
                    found = self.recover(claim)
                    if found.startswith("jenkins:build:"):
                        store.resolve_jenkins_queue(run_id, token, external, found)
                        external = found
                        continue
                else:
                    self.validate(queue, claim)
                    if queue.get("cancelled"):
                        raise BuildError("build_stopped", "Jenkins 큐에서 빌드가 취소됐습니다.")
                    if queue.get("executable"):
                        found = "jenkins:build:" + str(self.number(queue["executable"]["number"]))
                        store.resolve_jenkins_queue(run_id, token, external, found)
                        external = found
                        continue
                phase = "queued"
            else:
                build = self.describe(external, claim)
                if not build.get("building"):
                    if build.get("result") != "SUCCESS":
                        raise BuildError(
                            "build_failed",
                            "Jenkins 빌드가 성공하지 못했습니다. 빌드 로그를 확인하세요.",
                        )
                    progress("image_verify", "Jenkins 결과와 ECR 이미지 digest를 비교합니다.")
                    return self.result(external, claim)
                phase = "build"
            if phase != previous:
                progress(
                    "build_" + phase,
                    "Jenkins 빌드를 기다리고 있습니다."
                    if phase == "queued"
                    else "Jenkins 에이전트에서 이미지를 빌드하고 있습니다.",
                )
                previous = phase
            self.sleep(5)
        store.heartbeat(run_id, token)
        self.stop_verified(external, claim)
        raise BuildError(
            "build_wait_timeout",
            "Jenkins 확인 시간이 만료돼 해당 실행의 중단을 요청했습니다. 종료 상태를 확인하세요.",
        )

    def stop_verified(self, external, claim):
        if external.startswith("jenkins:queue:"):
            number = external.removeprefix("jenkins:queue:")
            if not re.fullmatch(r"[1-9][0-9]*", number):
                raise BuildError("build_id_invalid", "Jenkins 큐 번호를 확인하세요.")
            queue = self.get(f"/queue/item/{number}/api/json", missing=True)
            if queue is not None:
                self.validate(queue, claim)
                if queue.get("cancelled"):
                    return
                self.http(
                    "POST", "/queue/cancelItem", params={"id": number}, codes=(200, 201, 204, 302)
                )
            try:
                external = self.recover(claim)
            except BuildError as exc:
                if exc.payload["code"] == "build_submission_uncertain":
                    return
                raise
            if external.startswith("jenkins:queue:"):
                return
        build = self.describe(external, claim)
        if build.get("building"):
            self.http("POST", self.build_path(external) + "/stop", codes=(200, 201, 204, 302))

    def build_path(self, external):
        match = re.fullmatch(r"jenkins:build:([1-9][0-9]*)", external)
        if not match:
            raise BuildError("build_id_invalid", "Jenkins 빌드 번호를 확인하세요.")
        return self.job + "/" + match[1]

    def describe(self, external, claim):
        build = self.get(
            self.build_path(external)
            + "/api/json?tree=number,building,result,actions[parameters[name,value]]"
        )
        self.validate(build, claim)
        if str(self.number(build.get("number"))) != external.rsplit(":", 1)[1]:
            raise BuildError("build_identity_mismatch", "Jenkins 빌드 번호가 요청과 다릅니다.")
        return build

    def result(self, external, claim):
        response = self.http(
            "GET", self.build_path(external) + "/artifact/result.json", limit=16384
        )
        try:
            report = json.loads(response.content)
            expected = {
                "schema_version": 1,
                "run_id": str(claim["id"]),
                "commit_sha": claim["resolved_commit_sha"],
                "platform": "linux/amd64",
            }
            digest = report.get("image_digest", "")
            if (
                any(report.get(k) != v for k, v in expected.items())
                or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
                or report.get("image") != self.target["repository"] + "@" + digest
            ):
                raise ValueError()
        except (ValueError, AttributeError):
            raise BuildError(
                "build_result_invalid", "Jenkins 결과의 요청·커밋·이미지가 일치하지 않습니다."
            ) from None
        images = self.ecr.describe_images(
            repositoryName=self.target["repository"].split("/", 1)[1],
            imageIds=[{"imageTag": "run-" + str(claim["id"])}],
        ).get("imageDetails", [])
        if (
            len(images) != 1
            or images[0].get("imageDigest") != digest
            or images[0].get("imageManifestMediaType")
            not in (
                "application/vnd.docker.distribution.manifest.v2+json",
                "application/vnd.oci.image.manifest.v1+json",
            )
        ):
            raise BuildError(
                "image_mismatch", "Jenkins 결과와 실제 ECR 이미지가 일치하지 않습니다."
            )
        return {
            **expected,
            "provider": "jenkins",
            "image": report["image"],
            "image_digest": digest,
            "source_check_id": claim["input_snapshot"]["source_check_id"],
            "build_id": external,
            "completed_at": datetime.now(UTC).isoformat(),
        }

    def log_tail(self, claim):
        external = claim["external_id"]
        if not external or external.startswith("jenkins:queue:"):
            return {"text": "", "available": False, "truncated": False}
        self.describe(external, claim)
        path = self.build_path(external) + "/consoleText"
        # Jenkins Stapler supports byte ranges for consoleText. Bound streaming even when ignored.
        with httpx.Client(
            auth=self.auth,
            timeout=20,
            follow_redirects=False,
            trust_env=False,
            verify=self.verify,
            transport=self.transport,
        ) as client:
            with client.stream(
                "GET", self.target["url"] + path, headers={"Range": "bytes=-20000"}
            ) as response:
                if response.status_code not in (200, 206):
                    raise BuildError("build_logs_unavailable", "Jenkins 로그를 가져올 수 없습니다.")
                buf, truncated = bytearray(), response.status_code == 206
                for chunk in response.iter_bytes(chunk_size=4096):
                    buf.extend(chunk)
                    if len(buf) > 20000:
                        truncated = True
                        del buf[20000:]
                        break
        raw = bytes(buf).decode("utf-8", errors="replace")
        raw = raw.replace(self.auth[1], "[REDACTED]")
        raw = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", raw)
        return {
            "text": "".join(c for c in raw if c in "\n\t" or (ord(c) >= 32 and ord(c) != 127)),
            "available": True,
            "truncated": truncated,
        }
