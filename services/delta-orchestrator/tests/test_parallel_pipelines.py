import threading

import pytest

from delta_orchestrator.pipelines import Pipelines
from delta_pipeline.model import Board, Options
from delta_pipeline.run import unfinished_runs

REPO = 'https://github.com/2026-sbh-delta/delta-sample-app'
COMMIT = 'a' * 40


class WaitingRun:
    def __init__(self, options, directory, identifier):
        self.options, self.run_id = options, identifier
        self.done = threading.Event()
        (directory / 'runs').mkdir(exist_ok=True)
        self.board = Board(directory / 'runs' / (identifier + '.json'), options, identifier)
        self.board.update('dast', state='waiting', decision={'id': identifier + '-decision', 'options': []})
        self.answers = []

    def execute(self):
        self.done.wait(10)

    def snapshot(self):
        return self.board.snapshot()

    def answer(self, value, case_id=None, decision_id=None):
        if decision_id != self.run_id + '-decision':
            return False
        self.answers.append(value)
        self.done.set()
        return True

    def cancel(self):
        if self.options.execution_cloud != 'onprem':
            return False
        self.done.set()
        return True


def stop(manager):
    for run in manager._runs.values():
        run.done.set()
    for thread in manager._threads.values():
        thread.join(2)


def test_two_environments_run_together_with_separate_approval_and_capacity(tmp_path):
    p = Pipelines(tmp_path, WaitingRun)
    try:
        gcp = p.start(REPO, COMMIT, request_key='gcp')
        onprem = p.start(REPO, COMMIT, execution_cloud='onprem', request_key='onprem')
        assert set(p.active_ids()) == {gcp, onprem}
        with pytest.raises(RuntimeError):
            p.start(REPO, COMMIT, request_key='gcp-other')
        with pytest.raises(RuntimeError):
            p.start(REPO, COMMIT, execution_cloud='onprem', request_key='onprem-other')
        assert p.start(REPO, COMMIT, request_key='gcp') == gcp
        assert not p.answer(gcp, 'approve', decision_id=onprem + '-decision')
        assert p.answer(gcp, 'approve', decision_id=gcp + '-decision')
        p._threads[gcp].join(2)
        assert p.active_ids() == [onprem]
        assert p._runs[onprem].answers == []
        assert p.cancel(onprem)
        p._threads[onprem].join(2)
        assert not p.running()
    finally:
        stop(p)


def test_restart_restores_both_environments(tmp_path):
    (tmp_path / 'runs').mkdir()
    for cloud in ['gcp', 'onprem']:
        Board(tmp_path / 'runs' / (cloud + '.json'), Options(REPO, COMMIT, execution_cloud=cloud), cloud)
    assert len(unfinished_runs(tmp_path)) == 2
    p = Pipelines(tmp_path, WaitingRun)
    try:
        assert set(p.active_ids()) == {'gcp', 'onprem'}
        assert p.running('gcp') and p.running('onprem')
    finally:
        stop(p)
