"""강제 종료 시험용 실행기. 호출 기록을 파일에 남겨 프로세스를 넘어 비교한다."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

from fakes import FakeDast, FakeDelta, FakeJenkins

from delta_pipeline.model import SCENARIOS, Options
from delta_pipeline.run import Run, latest_unfinished

KILLED = 9
BASE = Path(sys.argv[1])
KILL_AT = sys.argv[2]
SCENARIO_ID = sys.argv[3]
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


class DurableJenkins(FakeJenkins):
    def trigger(self, scenario, run_key):
        record("trigger", [scenario, run_key])
        kill_if("after_trigger")
        return len(recorded("trigger"))

    def find_build(self, run_key):
        keys = [key for _, key in recorded("trigger")]
        return keys.index(run_key) + 1 if run_key in keys else None

    def describe(self, build):
        kill_if("deploy_wait")
        return super().describe(build)


class RecordingDast(FakeDast):
    def start(self, scenario, planner, report):
        record("dast", scenario)
        return super().start(scenario, planner, report)


def main():
    store = BASE / "store"
    saved = latest_unfinished(store) if store.exists() else None
    run_id, options = saved or (None, Options(next(s for s in SCENARIOS if s.id == SCENARIO_ID)))
    run = Run(options, delta=FakeDelta(), dast=RecordingDast(), jenkins=DurableJenkins(), onprem_dir=BASE / "onprem",
              store_dir=store, service_ready=lambda: True, sleep=lambda _: None, run_id=run_id)
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
