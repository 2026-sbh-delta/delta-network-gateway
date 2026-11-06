"""AWS 대상 위임 세션과 SSM 포트 포워딩(DB 준비용) 경계."""

from unittest.mock import Mock, patch

import pytest

from release_support import load

SELECTED = {"identity": {"account_id": "111122223333"}, "region": "ap-northeast-2",
            "assume_role_arn": "arn:aws:iam::111122223333:role/delta-prod-deploy", "external_id": "fixture-external"}


def factory(account="111122223333"):
    calls = []

    def make(**kwargs):
        calls.append(kwargs)
        session = Mock()
        session.client.return_value.assume_role.return_value = {"Credentials": {
            "AccessKeyId": "AKIA", "SecretAccessKey": "s", "SessionToken": "t"}}
        session.client.return_value.get_caller_identity.return_value = {"Account": account}
        return session

    return make, calls


def test_session_assumes_target_role_with_external_id():
    module = load("aws_vm")
    make, calls = factory()
    module.session(SELECTED, factory=make)
    assert calls[0] == {"region_name": "ap-northeast-2"}
    assert calls[1]["aws_session_token"] == "t" and calls[1]["region_name"] == "ap-northeast-2"


@pytest.mark.parametrize("change", [
    {"assume_role_arn": "arn:aws:iam::999999999999:role/delta-prod-deploy"},
    {"external_id": ""},
])
def test_session_refuses_role_outside_selected_account(change):
    module = load("aws_vm")
    make, _ = factory()
    with pytest.raises(ValueError):
        module.session({**SELECTED, **change}, factory=make)


def test_session_refuses_identity_mismatch():
    module = load("aws_vm")
    make, _ = factory(account="999999999999")
    with pytest.raises(ValueError, match="identity"):
        module.session(SELECTED, factory=make)


@pytest.mark.parametrize("host,port", [("169.254.169.254", 5432), ("8.8.8.8", 5432),
                                       ("db.internal.example", 22), ("bad host;rm", 5432)])
def test_forward_rejects_metadata_public_and_unexpected_endpoints(host, port):
    module = load("aws_vm")
    with pytest.raises(ValueError):
        with module.forward(Mock(), "i-1", host, port):
            pass


def test_forward_passes_credentials_only_through_child_env_and_cleans_up():
    module = load("aws_vm")
    aws = Mock(region_name="ap-northeast-2")
    aws.get_credentials.return_value.get_frozen_credentials.return_value = Mock(
        access_key="AKIA", secret_key="fixture-secret", token="fixture-token")
    process = Mock(pid=4321)
    process.poll.return_value = None
    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)
    launch = Mock(return_value=process)
    with (patch.object(module, "local_port", return_value=31005),
          patch.object(module.socket, "create_connection", return_value=connection),
          patch.object(module.os, "killpg") as kill):
        with module.forward(aws, "i-1", "db.abc.ap-northeast-2.rds.amazonaws.com", popen=launch) as port:
            assert port == 31005
    args, kwargs = launch.call_args
    assert "AWS-StartPortForwardingSessionToRemoteHost" in args[0]
    assert "fixture-secret" not in " ".join(args[0])
    assert kwargs["env"]["AWS_SECRET_ACCESS_KEY"] == "fixture-secret" and kwargs["start_new_session"]
    kill.assert_called_once_with(4321, module.signal.SIGTERM)
