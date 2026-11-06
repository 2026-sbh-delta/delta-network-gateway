"""Pre-cleanup evidence survives Pod deletion and is bound to one run/image/commit."""

import json
from types import SimpleNamespace
from uuid import uuid4

from delta_ai.diagnostic_contract import DiagnosisRequest
from delta_pipeline.evidence import EvidenceCollector, cache_path, capture_runtime


def deps(tmp_path):
    class Port:
        def inspect(self, rid):
            return {'pod': {'uid': 'own-uid'}, 'logs': 'password=NEVER-EXPOSE postgres://u:PRIVATE@db ' + 'x' * 10000}

    return SimpleNamespace(options=SimpleNamespace(assistance=True, commit='a' * 40), run_id='run1',
                           report_dir=tmp_path / 'reports', onprem=Port(), cancelled=lambda: False,
                           board=SimpleNamespace(add_detail=lambda *a, **kw: None))


def test_cached_logs_are_available_after_native_cleanup(tmp_path):
    dependency = deps(tmp_path)
    state = {'onprem_run_id': str(uuid4()), 'image_digest': 'sha256:' + 'a' * 64}
    capture_runtime(dependency, state)
    cache = cache_path(dependency, state)
    assert cache.stat().st_mode & 0o777 == 0o600
    assert cache.stat().st_size < 32000
    assert 'NEVER-EXPOSE' not in cache.read_text() and 'PRIVATE' not in cache.read_text()
    dependency.onprem.inspect = lambda rid: {'limitations': ['Pod already cleaned']}
    state['diagnosis_request'] = DiagnosisRequest(case_id='case1', run_id='run1', service_id='svc',
        node_id='onprem_wait', trigger='execution_failure',
        observations=[{'evidence_id': 'failed', 'source': 'worker', 'summary': 'HTTP failed'}]).model_dump(mode='json')
    request = EvidenceCollector(dependency).collect(state)['diagnosis_request']
    assert any(o['evidence_id'] == 'native-before-cleanup' for o in request['observations'])


def test_mismatched_cached_image_is_not_used(tmp_path):
    dependency = deps(tmp_path)
    state = {'onprem_run_id': str(uuid4()), 'image_digest': 'sha256:' + 'a' * 64}
    capture_runtime(dependency, state)
    path = cache_path(dependency, state)
    saved = json.loads(path.read_text())
    saved['image_digest'] = 'sha256:' + 'b' * 64
    path.write_text(json.dumps(saved))
    state['diagnosis_request'] = DiagnosisRequest(case_id='case1', run_id='run1', service_id='svc',
        node_id='onprem_wait', trigger='execution_failure',
        observations=[{'evidence_id': 'failed', 'source': 'worker', 'summary': 'HTTP failed'}]).model_dump(mode='json')
    request = EvidenceCollector(dependency).collect(state)['diagnosis_request']
    assert not any(o['evidence_id'] == 'native-before-cleanup' for o in request['observations'])
