"""Persistent run-scoped inbox and bounded opt-in webhook transport."""

import json

import pytest
from delta_pipeline.notifications import Inbox, Webhook


def emit(inbox, run='run1'):
    return inbox.emit(run_id=run, case_id='case1', kind='action_required',
                      summary='HTTP failed token=NEVER-EXPOSE', actions=('retry_same_job',))


def test_inbox_is_durable_idempotent_and_scoped(tmp_path):
    inbox = Inbox(tmp_path)
    identifier = emit(inbox)
    assert emit(inbox) == identifier
    assert len(Inbox(tmp_path).list('run1')) == 1
    assert not inbox.list('other')
    assert 'NEVER-EXPOSE' not in json.dumps(inbox.list('run1'))
    assert not inbox.acknowledge('other', identifier)
    assert inbox.acknowledge('run1', identifier)
    assert Inbox(tmp_path).list('run1')[0]['read_at']


def test_delivery_failure_is_visible_and_can_retry_without_losing_inbox(tmp_path):
    inbox = Inbox(tmp_path)
    identifier = emit(inbox)

    def broken(_):
        raise RuntimeError('token=NEVER-EXPOSE')

    assert not inbox.deliver(identifier, broken)
    assert inbox.list('run1')[0]['delivery'] == 'failed'
    seen = []
    assert inbox.deliver(identifier, seen.append)
    assert len(seen) == 1
    assert not inbox.deliver(identifier, seen.append)
    assert inbox.list('run1')[0]['delivery'] == 'sent'


def test_delivery_attempts_are_bounded(tmp_path):
    inbox = Inbox(tmp_path)
    identifier = emit(inbox)
    calls = []

    def broken(event):
        calls.append(event)
        raise TimeoutError()

    for _ in range(5):
        inbox.deliver(identifier, broken)
    assert len(calls) == 3


@pytest.mark.parametrize('url', [
    'http://169.254.169.254/latest', 'file:///tmp/out', 'https://evil.invalid/notify',
    'https://user:password@hooks.slack.com/x',
])
def test_webhook_requires_operator_allowlisted_https(url):
    with pytest.raises(ValueError):
        Webhook(url, 'https://platform.example')


def test_webhook_formats_report_link_and_idempotency_header(tmp_path):
    inbox = Inbox(tmp_path)
    emit(inbox)
    event = inbox.list('run1')[0]
    webhook = Webhook('https://hooks.slack.com/services/test', 'https://platform.example')
    seen = []

    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass

    class Opener:
        def open(self, request, timeout):
            seen.append(request)
            assert timeout == 5
            return Response()

    webhook.opener = Opener()
    webhook(event)
    request = seen[0]
    assert request.get_header('Idempotency-key') == event['id']
    assert 'https://platform.example/api/pipeline/runs/run1/reports/case1/md' in request.data.decode()
    assert 'NEVER-EXPOSE' not in request.data.decode()


def test_symlink_inbox_is_rejected(tmp_path):
    target = tmp_path / 'actual'
    target.mkdir()
    link = tmp_path / 'linked'
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError):
        Inbox(link)
