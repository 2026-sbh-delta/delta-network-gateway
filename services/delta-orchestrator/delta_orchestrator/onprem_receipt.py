"""Read a bound runtime receipt from the executor that owns the on-premise job."""
import re
from uuid import UUID


def receipt(board, rpc):
    options = board.get('options') or {}
    nodes = {n['id']: n for n in board.get('nodes', [])}
    stage = nodes.get('stage', {})
    detail = stage.get('detail') or {}
    if (options.get('execution_cloud') != 'onprem'
            or board.get('outcome') not in ('verified', 'unresolved')
            or stage.get('state') != 'pass' or detail.get('cleanup') != 'stopped'
            or any(n.get('decision') for n in nodes.values())):
        raise ValueError('Completed on-premise execution required')
    native_id = str(UUID(detail['platform_run_id']))
    build_id = str(UUID(options['onprem_build_id']))
    job = rpc.call('status', build_id=build_id, id=native_id)
    source, result = job.get('input_snapshot') or {}, job.get('result') or {}
    backend = source.get('backend') or {}
    if (job.get('id') != native_id or job.get('status') != 'succeeded'
            or job.get('kind') != 'dast_check'
            or job.get('resolved_commit_sha') != options.get('commit')
            or source.get('build_id') != build_id
            or result.get('execution_cloud') != 'onprem' or result.get('runtime') != 'k3s'
            or result.get('runtime_check') != 'passed' or result.get('cleanup') != 'stopped'
            or not result.get('revision') or result['revision'] != detail.get('runtime_revision')
            or result.get('commit_sha') != options.get('commit')
            or result.get('image') != source.get('image')
            or result.get('image_digest') != source.get('image_digest')
            or not re.fullmatch(r'[a-f0-9]{64}', str(result.get('runtime_evidence_sha256', '')))
            or result['runtime_evidence_sha256'] != (nodes.get('dast', {}).get('detail') or {}).get('runtime_evidence_sha256')):
        raise ValueError('Runtime evidence does not match the selected execution')
    images = {'frontend': source.get('image'), 'backend': backend.get('image')}
    for role, component in (('frontend', source), ('backend', backend)):
        ref = images[role]
        if (not isinstance(ref, str) or not re.fullmatch(r'[a-zA-Z0-9./_-]+@sha256:[a-f0-9]{64}', ref)
                or ref.split('@')[1] != component.get('image_digest')):
            raise ValueError('Pinned component images required')
    # No credentials, runner connection settings or raw input snapshots leave the executor.
    return {'schema': 1, 'pipeline_run_id': board['run_id'], 'native_run_id': native_id,
            'service_id': str(UUID(str(job['service_id']))), 'commit': options['commit'],
            'build_id': build_id, 'backend_build_id': str(UUID(backend['build_id'])),
            'images': images, 'runtime_revision': result['revision'],
            'runtime_evidence_sha256': result['runtime_evidence_sha256'],
            'runtime_check': 'passed', 'cleanup': 'stopped'}
