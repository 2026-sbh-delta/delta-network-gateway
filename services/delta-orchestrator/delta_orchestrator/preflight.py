"""Check the candidate's diagnostic authentication without state or model execution."""
import json
import os
import urllib.error
import urllib.request

from delta_orchestrator.access import Access
from delta_pipeline.diagnostic_client import DiagnosticClient


def check():
    Access.from_env()
    client = DiagnosticClient(os.environ['DELTA_DIAGNOSTIC_URL'],
        os.environ['DELTA_DIAGNOSTIC_TOKEN'], os.environ.get('DELTA_DIAGNOSTIC_CA_BASE64'))
    with client._opener.open(urllib.request.Request(client._url + '/healthz'), timeout=8) as response:
        if response.status != 200 or json.loads(response.read(4096)).get('status') != 'ok':
            raise ValueError('Diagnostic health failed')
    request = urllib.request.Request(client._url + '/v1/diagnoses', b'{}', method='POST',
        headers={'Authorization': 'Bearer ' + client._token, 'Content-Type': 'application/json'})
    try:
        with client._opener.open(request, timeout=8):
            raise ValueError('Empty diagnosis must not execute')
    except urllib.error.HTTPError as error:
        if error.code != 422:
            raise ValueError('Diagnostic authentication failed') from None
        error.close()
    result = {'diagnostics': 'ready', 'state_reads': 0, 'llm_calls': 0}
    if os.environ.get('DELTA_LOG_EVIDENCE_ENABLED') == '1':
        import boto3
        result['log_evidence'] = 'sdk-ready'
    if os.environ.get('DELTA_ONPREM_ENABLED') == '1':
        from delta_pipeline.onprem_ssm import WorkerRpc
        builds=WorkerRpc().call('catalog')
        if not builds:raise ValueError('No registered onprem image')
        result['onprem']='registered-builds-ready'
    return result


if __name__ == '__main__':
    try:
        print(json.dumps(check()))
    except Exception:
        raise SystemExit('Candidate diagnostic/auth check failed; credentials omitted') from None
