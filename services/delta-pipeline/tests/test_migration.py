from __future__ import annotations

import pytest
from fakes import FakeDast, FakeDelta, FakeJenkins, FakeMigration
from test_flow import _execute, _states, onprem_dir  # noqa: F401

from delta_pipeline import migration_steps
from delta_pipeline.clients import ServiceUnavailable
from delta_pipeline.migration import StandInMigration
from delta_pipeline.migration_steps import MigrationSteps, sync_is_ready
from delta_pipeline.model import SCENARIOS, SHIFT_STEPS, Board, Options, deployment_request
from delta_pipeline.run import Run
from delta_pipeline.steps import Deps, Stopped

MIGRATION = next(s for s in SCENARIOS if s.migrate)
NORMAL = next(s for s in SCENARIOS if s.id == "normal")
IMAGE = {"image.json": {"image_id": "sha256:abc", "platform": "linux/amd64"}}


def _run(onprem_dir, *, tools=None, delta=None, files=IMAGE, final="SUCCESS", scenario=MIGRATION):  # noqa: F811
    return Run(Options(scenario), delta=delta or FakeDelta(), dast=FakeDast(), jenkins=FakeJenkins(final, files=files),
               migration=tools, onprem_dir=onprem_dir, store_dir=onprem_dir / "store", service_ready=lambda: True,
               sleep=lambda _: None)


def test_migration_runs_in_order_and_asks_before_moving_writes(onprem_dir):  # noqa: F811
    tools = FakeMigration()

    snapshot = _execute(_run(onprem_dir, tools=tools), ["approve"])

    assert snapshot["outcome"] == "migrated"
    assert tools.order() == ["provision", "deploy:sha256:abc", "start_data_sync", "weight:10", "measure:10", "weight:50", "measure:50",
                             "weight:100", "measure:100", "data_sync_status", "data_sync_status", "cut_over_data"]
    assert all(state == "pass" for node, state in _states(snapshot).items() if node.startswith(("migrate-", "shift-")))


def test_traffic_does_not_wait_for_the_data_sync(onprem_dir):  # noqa: F811
    tools = FakeMigration(sync=[("syncing", 90.0), ("synced", 0.0)])

    _execute(_run(onprem_dir, tools=tools), ["approve"])

    order = tools.order()
    assert order.index("weight:100") < order.index("data_sync_status")


def test_bad_verdict_returns_the_traffic_and_never_moves_writes(onprem_dir):  # noqa: F811
    tools = FakeMigration()

    snapshot = _execute(_run(onprem_dir, tools=tools, delta=FakeDelta(shift={50: "needs_human"})))

    assert snapshot["outcome"] == "rolled_back"
    assert tools.weights == [10, 50, 0]
    assert "cut_over_data" not in tools.order()


def test_judgment_outage_during_the_shift_also_returns_the_traffic(onprem_dir):  # noqa: F811
    tools = FakeMigration()

    snapshot = _execute(_run(onprem_dir, tools=tools, delta=FakeDelta(shift={10: "down"})))

    assert snapshot["outcome"] == "rolled_back"
    assert tools.weights == [10, 0]


@pytest.mark.parametrize("sync", [[("failed", None)], [("syncing", 90.0)], [("synced", None)], [("synced", 60.0)]])
def test_data_that_is_not_caught_up_returns_the_traffic(onprem_dir, monkeypatch, sync):  # noqa: F811
    monkeypatch.setattr(migration_steps, "DATA_DEADLINE_SECONDS", 0.05)
    tools = FakeMigration(sync=sync)

    snapshot = _execute(_run(onprem_dir, tools=tools))

    assert snapshot["outcome"] == "rolled_back"
    assert tools.weights[-1] == 0
    assert "cut_over_data" not in tools.order()


def test_person_can_stop_before_writes_move(onprem_dir):  # noqa: F811
    tools = FakeMigration()

    snapshot = _execute(_run(onprem_dir, tools=tools), ["stop"])

    assert snapshot["outcome"] == "rolled_back"
    assert tools.weights[-1] == 0
    assert "cut_over_data" not in tools.order()


def test_sync_that_falls_behind_while_waiting_for_a_person_blocks_the_cutover(onprem_dir):  # noqa: F811
    tools = FakeMigration(sync=[("synced", 0.0), ("syncing", 60.0)])

    snapshot = _execute(_run(onprem_dir, tools=tools), ["approve"])

    assert snapshot["outcome"] == "rolled_back"
    assert "cut_over_data" not in tools.order()


def test_failed_cutover_is_not_rolled_back_automatically(onprem_dir):  # noqa: F811
    tools = FakeMigration(cutover="failed")

    snapshot = _execute(_run(onprem_dir, tools=tools), ["approve"])

    assert snapshot["outcome"] == "stopped"
    assert 0 not in tools.weights
    assert "사람이 확인" in next(n for n in snapshot["nodes"] if n["id"] == "result")["summary"]


def test_failed_provision_stops_before_any_traffic(onprem_dir):  # noqa: F811
    tools = FakeMigration(provision="failed")

    snapshot = _execute(_run(onprem_dir, tools=tools))

    assert snapshot["outcome"] == "stopped"
    assert tools.weights == []


def test_failed_data_sync_start_stops_before_any_traffic(onprem_dir):  # noqa: F811
    tools = FakeMigration(sync_start="failed")

    snapshot = _execute(_run(onprem_dir, tools=tools))

    assert snapshot["outcome"] == "stopped"
    assert tools.weights == []


def test_rollback_that_cannot_reach_the_switch_says_a_person_must_check(onprem_dir):  # noqa: F811
    tools = FakeMigration(weight_down=True)

    snapshot = _execute(_run(onprem_dir, tools=tools, delta=FakeDelta(shift={10: "needs_human"})))

    assert snapshot["outcome"] == "stopped"
    assert "사람이 확인" in next(n for n in snapshot["nodes"] if n["id"] == "result")["summary"]


def test_build_without_an_image_record_is_not_migrated(onprem_dir):  # noqa: F811
    tools = FakeMigration()

    snapshot = _execute(_run(onprem_dir, tools=tools, files={}))

    assert snapshot["outcome"] == "stopped"
    assert tools.order() == []


def test_migration_refuses_without_gate_records_even_if_the_graph_routes_there(tmp_path):
    options, tools = Options(MIGRATION), FakeMigration()
    deps = Deps(run_id="r", options=options, board=Board(tmp_path / "board.json", options, "r"), delta=FakeDelta(), dast=FakeDast(),
                jenkins=FakeJenkins(files=IMAGE), onprem_dir=tmp_path, service_ready=lambda: True, on_release_start=lambda: None,
                sleep=lambda _: None, migration=tools)

    with pytest.raises(Stopped):
        MigrationSteps(deps).migrate_provision({"gates": {"onprem": "pass"}, "build": 1})

    assert tools.order() == []


def test_failed_release_never_starts_the_migration(onprem_dir):  # noqa: F811
    tools = FakeMigration()

    snapshot = _execute(_run(onprem_dir, tools=tools, final="FAILED"))

    assert snapshot["outcome"] == "rolled_back"
    assert tools.order() == []


def test_scenario_without_migration_ends_at_the_release(onprem_dir):  # noqa: F811
    tools = FakeMigration()

    snapshot = _execute(_run(onprem_dir, tools=tools, scenario=NORMAL))

    assert snapshot["outcome"] == "deployed"
    assert tools.order() == []
    assert not any(n["id"].startswith("migrate-") for n in snapshot["nodes"])


def test_migration_without_tools_stops(onprem_dir):  # noqa: F811
    snapshot = _execute(_run(onprem_dir))

    assert snapshot["outcome"] == "stopped"


def test_stand_in_completes_the_flow_and_says_it_is_a_stand_in(onprem_dir):  # noqa: F811
    snapshot = _execute(_run(onprem_dir, tools=StandInMigration()), ["approve"])

    assert snapshot["outcome"] == "migrated"
    assert "대역" in next(n for n in snapshot["nodes"] if n["id"] == "result")["summary"]


@pytest.mark.parametrize("status,expected", [
    ({"state": "synced", "lag_seconds": 0.0}, True), ({"state": "synced", "lag_seconds": None}, False),
    ({"state": "synced", "lag_seconds": 999.0}, False), ({"state": "syncing", "lag_seconds": 0.0}, False), ({}, False),
])
def test_unknown_lag_is_not_treated_as_caught_up(status, expected):
    assert sync_is_ready(status) is expected


def test_shift_request_matches_the_gate_contract():
    body = deployment_request("run", SHIFT_STEPS[0], {"metrics": [{"name": "error_rate", "value": 0, "threshold": 0.05, "samples": 40}]})

    assert body["kind"] == "deployment_gate"
    assert body["evidence"]["deployment"]["target"] == "gcp"
    assert body["evidence"]["deployment"]["traffic_percent"] == SHIFT_STEPS[0]


def test_tool_outage_is_a_service_error_not_a_pass():
    tools = FakeMigration(weight_down=True)

    with pytest.raises(ServiceUnavailable):
        tools.set_weight("r", 0)
