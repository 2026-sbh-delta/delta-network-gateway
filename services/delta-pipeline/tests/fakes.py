from __future__ import annotations

from delta_pipeline.clients import ServiceUnavailable


class FakeDelta:
    def __init__(self, *, onprem="pass", fail=False, shift=None):
        self.onprem, self.fail, self.kinds, self.shift = onprem, fail, [], shift or {}

    def assess(self, body):
        if self.fail:
            raise ServiceUnavailable("죽음")
        self.kinds.append(body["kind"])
        if body["kind"] == "deployment_gate":
            verdict = self.shift.get(body["evidence"]["deployment"]["traffic_percent"], "pass")
            if verdict == "down":
                raise ServiceUnavailable("죽음")
            return {"verdict": verdict, "findings": []}
        if body["kind"] == "dast_verification":
            statuses = [r["status"] for r in body["evidence"]["dast"]["results"]]
            return {"verdict": "pass" if statuses == ["NOT_REPRODUCED"] else "needs_human", "findings": []}
        return {"verdict": self.onprem, "findings": [{"rule_id": "R", "status": "fail", "severity": "high"}]}


class FakeDast:
    def __init__(self):
        self.scenarios = []

    def config(self):
        return {"sample_report": {"findings": []}, "target_ids": ["local-fixture"]}

    def start(self, scenario, planner, report):
        self.scenarios.append(scenario)
        return f"run-{scenario}"

    def run(self, run_id):
        status = "NOT_REPRODUCED" if run_id.endswith("fixed") else "REPRODUCED"
        return {"status": "done", "results": [{"finding_id": "F", "title": "XSS", "status": status, "message": "m"}]}


class FakeJenkins:
    def __init__(self, final="SUCCESS", *, files=None, inputs=0):
        self.final, self.files, self.triggered = final, files or {}, []
        self.keys, self.inputs, self.answers = [], inputs, []

    def trigger(self, scenario, run_key):
        self.triggered.append(scenario)
        self.keys.append(run_key)
        return len(self.triggered)

    def find_build(self, run_key):
        return self.keys.index(run_key) + 1 if run_key in self.keys else None

    def describe(self, build):
        if self.inputs:
            return {"status": "PAUSED_PENDING_INPUT", "stages": []}
        return {"status": self.final, "stages": [{"name": "단계적 전환", "status": self.final}]}

    def pending_inputs(self, build):
        return [{"id": "HumanGate", "message": "확인"}] if self.inputs else []

    def answer_input(self, build, input_id, *, approve):
        self.answers.append(approve)
        self.inputs = 0

    def file(self, build, name):
        return self.files.get(name)

    def console_url(self, build):
        return f"http://jenkins/{build}"


class FakeMigration:
    label = "시험"

    def __init__(self, *, provision="ready", sync_start="running", sync=None, cutover="ready", weight_down=False):
        self.provision_state, self.sync_start, self.cutover, self.weight_down = provision, sync_start, cutover, weight_down
        self.sync, self.calls, self.weights = list(sync or [("synced", 0.0)]), [], []

    def order(self):
        return list(self.calls)

    def provision(self, run_id):
        self.calls.append("provision")
        return {"state": self.provision_state, "detail": "d"}

    def deploy(self, run_id, image):
        self.calls.append(f"deploy:{image}")
        return {"state": "ready", "detail": "d"}

    def start_data_sync(self, run_id):
        self.calls.append("start_data_sync")
        return {"state": self.sync_start, "detail": "d"}

    def data_sync_status(self, run_id):
        self.calls.append("data_sync_status")
        state, lag = self.sync.pop(0) if len(self.sync) > 1 else self.sync[0]
        return {"state": state, "lag_seconds": lag}

    def set_weight(self, run_id, percent):
        if self.weight_down and percent == 0:
            raise ServiceUnavailable("전환 도구 응답 없음")
        self.calls.append(f"weight:{percent}")
        self.weights.append(percent)

    def measure(self, run_id, percent):
        self.calls.append(f"measure:{percent}")
        return {"metrics": [{"name": "error_rate", "value": 0.0, "threshold": 0.05, "samples": 40}], "log_tail": ""}

    def cut_over_data(self, run_id):
        self.calls.append("cut_over_data")
        return {"state": self.cutover, "detail": "d"}
