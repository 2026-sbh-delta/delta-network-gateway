from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "traffic-control"))
sys.path.insert(0, str(ROOT / "pipeline" / "scripts"))

from control import ControlError, Controller  # noqa: E402

SERVICES = [{"id": "web", "backend": "mig_web", "path": "/"}, {"id": "api", "backend": "mig_api", "path": "/api/hello"}]


class FakeHaproxy:
    def __init__(self) -> None:
        self.backends = {"mig_web": {"aws": 100, "gcp": 0}, "mig_api": {"aws": 100, "gcp": 0}}
        self.down: set[tuple[str, str]] = set()
        self.calls: list[tuple[str, dict[str, int]]] = []

    def weights(self, backend: str) -> dict[str, int]:
        return dict(self.backends[backend])

    def set_weights(self, backend: str, weights) -> None:
        self.calls.append((backend, dict(weights)))
        self.backends[backend] = dict(weights)

    def statuses(self) -> dict[tuple[str, str], str]:
        return {(b, s): "DOWN" if (b, s) in self.down else "UP" for b in self.backends for s in ("aws", "gcp")}


@pytest.fixture
def haproxy() -> FakeHaproxy:
    return FakeHaproxy()


@pytest.fixture
def controller(haproxy: FakeHaproxy) -> Controller:
    return Controller(SERVICES, ("aws", "gcp"), haproxy, clock=lambda: 1.0)


def _state(controller: Controller, service_id: str) -> str:
    return next(s["state"] for s in controller.snapshot()["services"] if s["id"] == service_id)


def test_screen_cannot_change_weights_without_a_manual_run(controller):
    with pytest.raises(ControlError) as exc:
        controller.set_weight("web", 10, "ui")
    assert exc.value.status == 409

    controller.start_run("build-1", "auto", "aws", "gcp")
    with pytest.raises(ControlError) as exc:
        controller.set_weight("web", 10, "ui")
    assert exc.value.status == 403


def test_manual_change_sets_both_weights_and_locks_the_service_until_judged(controller, haproxy):
    controller.start_run("build-1", "manual", "aws", "gcp")

    event = controller.set_weight("web", 25, "ui")

    assert haproxy.backends["mig_web"] == {"aws": 75, "gcp": 25}
    assert haproxy.backends["mig_api"] == {"aws": 100, "gcp": 0}  # 다른 서비스는 그대로다
    assert (event["from"], event["to"], event["actor"]) == (0, 25, "ui")
    assert _state(controller, "web") == "measuring"
    with pytest.raises(ControlError):
        controller.set_weight("web", 50, "ui")

    controller.report("web", "passed", "ok", 25)
    controller.set_weight("web", 50, "ui")
    assert haproxy.backends["mig_web"] == {"aws": 50, "gcp": 50}


def test_raising_traffic_needs_the_target_entry_to_be_up(controller, haproxy):
    haproxy.down.add(("mig_api", "gcp"))
    controller.start_run("build-1", "manual", "aws", "gcp")

    with pytest.raises(ControlError) as exc:
        controller.set_weight("api", 10, "ui")

    assert exc.value.status == 409
    assert haproxy.calls == []


@pytest.mark.parametrize("value", [-1, 101, 10.5, "10", True, None])
def test_percent_must_be_an_integer_between_0_and_100(controller, value):
    controller.start_run("build-1", "manual", "aws", "gcp")
    with pytest.raises(ControlError) as exc:
        controller.set_weight("web", value, "ui")
    assert exc.value.status == 400


def test_rollback_works_in_auto_mode_and_a_late_pass_does_not_override_it(controller, haproxy):
    controller.start_run("build-1", "auto", "aws", "gcp")
    controller.set_weight("api", 50, "pipeline")

    controller.rollback("api", "ui", "화면에서 되돌림")

    assert haproxy.backends["mig_api"] == {"aws": 100, "gcp": 0}
    with pytest.raises(ControlError) as exc:
        controller.report("api", "passed", "늦게 온 통과", 50)
    assert exc.value.status == 409
    assert _state(controller, "api") == "rolled_back"
    with pytest.raises(ControlError):
        controller.set_weight("api", 10, "pipeline")


def test_passing_at_100_percent_completes_the_service(controller):
    controller.start_run("build-1", "auto", "aws", "gcp")
    controller.set_weight("web", 100, "pipeline")
    controller.report("web", "passed", "ok", 100)
    assert _state(controller, "web") == "completed"


def test_needs_ack_blocks_changes_until_a_person_confirms(controller):
    controller.start_run("build-1", "manual", "aws", "gcp")
    controller.set_weight("web", 10, "ui")
    controller.report("web", "needs_ack", "표본 부족", 10)

    with pytest.raises(ControlError):
        controller.set_weight("web", 50, "ui")
    controller.ack("web")
    controller.set_weight("web", 50, "ui")


def test_finish_is_refused_while_a_service_is_between_clouds(controller):
    controller.start_run("build-1", "manual", "aws", "gcp")
    controller.set_weight("web", 50, "ui")
    controller.report("web", "passed", "ok", 50)

    with pytest.raises(ControlError) as exc:
        controller.finish()
    assert "중간 비율" in exc.value.message

    controller.set_weight("web", 100, "ui")
    controller.report("web", "passed", "ok", 100)
    controller.finish()
    assert controller.snapshot()["run"]["finish_requested"] is True


def test_a_run_cannot_start_over_a_half_migrated_service(controller, haproxy):
    haproxy.backends["mig_web"] = {"aws": 50, "gcp": 50}
    with pytest.raises(ControlError) as exc:
        controller.start_run("build-2", "auto", "aws", "gcp")
    assert exc.value.status == 409


def test_another_run_is_refused_while_one_is_active_but_can_be_cleaned_up(controller):
    controller.start_run("build-1", "auto", "aws", "gcp")
    with pytest.raises(ControlError):
        controller.start_run("build-2", "auto", "aws", "gcp")
    controller.end_run("*", "aborted")
    controller.start_run("build-2", "auto", "aws", "gcp")


def test_direction_is_read_from_the_run(controller, haproxy):
    """GCP → AWS 에서는 같은 화면 값이 AWS 비율이다."""
    haproxy.backends = {"mig_web": {"aws": 0, "gcp": 100}, "mig_api": {"aws": 0, "gcp": 100}}
    controller.start_run("build-1", "manual", "gcp", "aws")
    controller.set_weight("web", 10, "ui")
    assert haproxy.backends["mig_web"] == {"aws": 10, "gcp": 90}


def test_an_ended_run_shows_services_as_idle_with_the_last_result(controller):
    controller.start_run("build-1", "auto", "aws", "gcp")
    controller.set_weight("web", 100, "pipeline")
    controller.report("web", "passed", "ok", 100)
    controller.end_run("build-1", "partial")

    web = next(s for s in controller.snapshot()["services"] if s["id"] == "web")
    assert (web["state"], web["detail"]) == ("idle", "지난 실행: completed")
