from __future__ import annotations

from delta_pipeline.clients import ServiceUnavailable


class FakeDelta:
    def __init__(self, *, onprem="pass", fail=False):
        self.onprem, self.fail, self.kinds = onprem, fail, []

    def assess(self, body):
        if self.fail:
            raise ServiceUnavailable("죽음")
        self.kinds.append(body["kind"])
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
