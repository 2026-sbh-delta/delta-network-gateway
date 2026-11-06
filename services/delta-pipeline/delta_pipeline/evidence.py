"""Bounded, read-only incident evidence. Caller supplies a run-bound native adapter."""

import json
import os
import re
import tempfile
import time
from pathlib import Path

from delta_ai.diagnostic_contract import DiagnosisRequest, safe_text

from delta_pipeline.exception_flow import _bounded_summary


def clean(value, limit=2400):
    text = json.dumps(value, ensure_ascii=False, default=str)
    text = re.sub(r'([a-z][a-z0-9+.-]*://)[^\s/@]+:[^\s/@]+@', r'\1[REDACTED]@', text, flags=re.IGNORECASE)
    return _bounded_summary(safe_text(text), limit)


def cache_path(deps, state):
    from uuid import UUID
    identifier = str(UUID(state['onprem_run_id']))
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', deps.run_id):
        raise ValueError('Invalid execution identity')
    folder = Path(deps.report_dir).parent / 'runtime-evidence'
    if folder.is_symlink():
        raise ValueError('Unsafe evidence directory')
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / (deps.run_id + '-' + identifier + '.json')
    if path.is_symlink():
        raise ValueError('Unsafe evidence file')
    return path


def capture_runtime(deps, state):
    """Save bounded observations before the existing worker removes its own Pod."""
    if not deps.options.assistance or deps.cancelled():
        return
    try:
        snapshot = deps.onprem.inspect(state['onprem_run_id'])
        if not any(k in snapshot for k in ('pod','logs','preserved_logs')):
            return
        path = cache_path(deps, state)
        value = {'native_run_id': state['onprem_run_id'], 'image_digest': state['image_digest'],
                 'commit': state.get('commit') or deps.options.commit, 'captured_at': time.time(),
                 'observations': {key: clean(snapshot[key], 6000) for key in ('pod', 'logs', 'events', 'limitations') if key in snapshot}}
        if 'preserved_logs'in snapshot:value['preserved_logs']=snapshot['preserved_logs']
        raw = json.dumps(value, ensure_ascii=False).encode()
        fd, name = tempfile.mkstemp(prefix='.runtime-', dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001 — collection cannot change a verdict or stop worker cleanup
        deps.board.add_detail('stage', evidence_gap='정리 전 실행 근거를 수집하지 못했습니다')


class EvidenceCollector:
    def __init__(self, deps):
        self.deps = deps

    def collect(self, state):
        request = DiagnosisRequest.model_validate(state['diagnosis_request'])
        observations = list(request.observations)
        facts = {'requested_commit': state.get('commit') or self.deps.options.commit,
                 'image_digest': state.get('image_digest'), 'native_run_id': state.get('onprem_run_id'),
                 'registered_build_id': getattr(self.deps.options,'onprem_build_id',None),
                 'manual_attempts': state.get('manual_count', 0), 'automatic_attempts': state.get('recovery_count', 0)}
        if hasattr(self.deps.board,'snapshot'):
            stage=next((n.get('detail',{}) for n in self.deps.board.snapshot()['nodes'] if n['id']=='stage'),{})
            facts['runtime']={k:stage.get(k) for k in ('runtime_resource','runtime_revision','cleanup','platform_status','error_code')}
        extra = [('execution-binding', 'pipeline', facts)]
        if state.get('onprem_run_id') and self.deps.onprem:
            try:
                snapshot = self.deps.onprem.inspect(state['onprem_run_id'])
                for key in ('facts', 'events', 'pod', 'logs', 'limitations'):
                    if key in snapshot:
                        extra.append(('native-' + key, 'platform-worker', snapshot[key]))
                if 'preserved_logs'in snapshot:
                    record=snapshot['preserved_logs'];payload=record['record']
                    extra.append(('native-log-binding','platform-worker',{k:payload.get(k)for k in
                        ('native_run_id','build_id','commit','image_digest','captured_at','before_cleanup')}|{'sha256':record['sha256']}))
                    for container in payload['containers']:
                        extra.append(('native-log-'+container['component'],'platform-worker',container))
            except Exception:  # noqa: BLE001 — evidence gaps do not fabricate success or leak credentials
                extra.append(('native-unavailable', 'platform-worker', '추가 상태/로그를 수집하지 못했습니다. 원인 확정에 제한이 있습니다.'))
        if state.get('onprem_run_id'):
            try:
                path = cache_path(self.deps, state)
                if path.is_file():
                    value = json.loads(path.read_bytes()[:32001])
                    if (value['native_run_id'] == state['onprem_run_id'] and value['image_digest'] == state['image_digest']
                            and value['commit'] == (state.get('commit') or self.deps.options.commit)):
                        extra.append(('native-before-cleanup', 'platform-worker', {
                            'captured_at': value['captured_at'], 'observations': value['observations']}))
                    else:
                        extra.append(('runtime-cache-mismatch', 'platform-worker', '정리 전 근거의 실행/이미지/커밋 바인딩이 달라 사용하지 않았습니다.'))
            except Exception:  # noqa: BLE001 — unavailable/tampered cache is not a successful observation
                extra.append(('runtime-cache-unavailable', 'platform-worker', '정리 전 근거 파일을 검증하지 못했습니다.'))
        known = {o.evidence_id for o in observations}
        for identifier, source, value in extra:
            if identifier not in known and len(observations) < 20:
                observations.append({'evidence_id': identifier, 'source': source, 'summary': clean(value)})
        updated = DiagnosisRequest.model_validate({**request.model_dump(mode='json'),
            'observations': [o.model_dump(mode='json') if hasattr(o, 'model_dump') else o for o in observations]})
        return {'diagnosis_request': updated.model_dump(mode='json')}
