"""강제 종료 시험용 실행기. 호출 기록을 파일에 남겨 프로세스를 넘어 비교한다."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

from fakes import COMMIT, IMAGE_REPO, REPO, FakeDast, FakeJenkins, FakeSast, FakeStaging, build_jenkins, deploy_jenkins

from delta_pipeline.model import Options
from delta_pipeline.run import Run, latest_unfinished

KILLED = 9
BASE = Path(sys.argv[1])
KILL_AT = sys.argv[2]
DAST_STATUS = sys.argv[3]
ANSWER = sys.argv[4]
CALLS = BASE / "calls.jsonl"


def record(name, value):
    with CALLS.open("a", encoding="utf-8") as file:
        file.write(json.dumps({"call": name, "value": value}) + "\n")


def recorded(name):
    if not CALLS.is_file():
        return []
    rows = [json.loads(line) for line in CALLS.read_text(encoding="utf-8").splitlines()]
    return [row["value"] for row in rows if row["call"] == name]


def kill_if(point):
    if KILL_AT == point:
        os._exit(KILLED)


def durable(job, fake: FakeJenkins):
    class Durable(type(fake)):
        def trigger(self, params, run_key):
            record("trigger", [job, run_key])
            kill_if(f"after_trigger_{job}")
            return len(recorded("trigger"))

        def find_build(self, run_key):
            keys = [key for name, key in recorded("trigger") if name == job]
            return recorded("trigger").index([job, run_key]) + 1 if run_key in keys else None

        def status(self, build):
            kill_if(f"wait_{job}")
            return super().status(build)

    return Durable(fake.result)


class RecordingDast(FakeDast):
    def submit(self, run_id, binding, report):
        # 재개 때 같은 ID·같은 입력으로 다시 부르면 DAST 는 같은 점검을 돌려준다(새로 돌지 않음)
        if run_id not in recorded("dast"):
            record("dast", run_id)
        self.bindings.pop(run_id, None)
        return super().submit(run_id, binding, report)


def main():
    store = BASE / "store"
    saved = latest_unfinished(store) if store.exists() else None
    run_id, options = saved or (None, Options(REPO, COMMIT))
    run = Run(options, sast=FakeSast(), dast=RecordingDast([DAST_STATUS]), build_jenkins=durable("build", build_jenkins()),
              deploy_jenkins=durable("deploy", deploy_jenkins()), staging=FakeStaging(), image_repo=IMAGE_REPO, store_dir=store,
              sleep=lambda _: None, run_id=run_id)
    thread = threading.Thread(target=run.execute)
    thread.start()
    deadline = time.time() + 20
    while thread.is_alive() and time.time() < deadline:
        waiting = next((n for n in run.snapshot()["nodes"] if n["decision"]), None)
        if waiting:
            kill_if("waiting")
            print(json.dumps({"seen_decision": waiting["id"]}), flush=True)
            run.answer(ANSWER)
        time.sleep(0.02)
    thread.join(timeout=2)
    print(json.dumps(run.snapshot(), ensure_ascii=False), flush=True)


main()
