from types import SimpleNamespace

from botocore.exceptions import ClientError
import pytest
from test_sources import FakeS3

from delta_diff.sources import AssumedRoleStateReader, StateLocation, StateUnreadable


def test_assumption_refreshes_each_read_and_closes_clients():
    calls, clients = [], []
    def assume(**kw):
        calls.append(kw)
        return {"Credentials": {"AccessKeyId": "test", "SecretAccessKey": "secret", "SessionToken": "session"}}
    class S3(FakeS3):
        closed = False
        def close(self):
            self.closed = True
    def factory(service, **kw):
        assert service == "s3" and kw["aws_session_token"] == "session"
        client = S3(body=b'{"version":4}')
        clients.append(client)
        return client
    reader = AssumedRoleStateReader(SimpleNamespace(assume_role=assume),
                                   "arn:aws:iam::123456789012:role/state-reader", "external", client_factory=factory)
    for _ in range(2):
        assert reader.read(StateLocation("b", "x.tfstate")).state["version"] == 4
    assert len(calls) == 2 and all(c.closed for c in clients)
    assert calls[0]["ExternalId"] == "external"


def test_failed_assumption_is_safe_and_cannot_return_state():
    def assume(**kw):
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "PRIVATE-DETAIL"}}, "AssumeRole")
    reader = AssumedRoleStateReader(SimpleNamespace(assume_role=assume),
                                   "arn:aws:iam::123456789012:role/state-reader", "external")
    with pytest.raises(StateUnreadable) as exc:
        reader.read(StateLocation("b", "x.tfstate"))
    assert "PRIVATE-DETAIL" not in str(exc.value)
