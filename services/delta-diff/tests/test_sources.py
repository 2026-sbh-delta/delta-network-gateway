from __future__ import annotations

import io
import json

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from delta_diff.sources import (
    S3StateReader,
    StateAllowlist,
    StateLocation,
    StateNotFound,
    StateTooLarge,
    StateUnreadable,
)

ALLOW = StateAllowlist.parse("tf-bucket/delta/lab/, other/env/")


class FakeS3:
    def __init__(
        self,
        *,
        body: bytes = b"{}",
        length: int | None = None,
        error: Exception | None = None,
    ) -> None:
        self.body, self.length, self.error = body, length, error

    def get_object(self, *, Bucket: str, Key: str) -> dict:
        if self.error:
            raise self.error
        return {
            "Body": io.BytesIO(self.body),
            "ContentLength": len(self.body) if self.length is None else self.length,
            "VersionId": "v1",
        }


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "x"}}, "GetObject")


@pytest.mark.parametrize(
    "bucket, key, allowed",
    [
        ("tf-bucket", "delta/lab/workload-server/terraform.tfstate", True),
        ("other", "env/a.tfstate", True),
        ("tf-bucket", "delta/prod/workload-server/terraform.tfstate", False),
        ("evil", "delta/lab/x.tfstate", False),
        ("tf-bucket", "delta/lab/notes.txt", False),
        ("tf-bucket", "delta/lab/../prod/terraform.tfstate", False),
    ],
)
def test_allowlist_permits_only_state_files_under_listed_prefixes(bucket, key, allowed):
    assert ALLOW.allows(StateLocation(bucket, key)) is allowed


@pytest.mark.parametrize("text", ["", " , ", "bucket-only", "/prefix-only"])
def test_allowlist_rejects_empty_or_malformed_configuration(text):
    with pytest.raises(ValueError):
        StateAllowlist.parse(text)


def test_reader_returns_state_and_version():
    snapshot = S3StateReader(
        FakeS3(body=json.dumps({"version": 4, "serial": 3}).encode())
    ).read(StateLocation("b", "k.tfstate"))

    assert snapshot.state["serial"] == 3
    assert snapshot.version_id == "v1"


def test_reader_refuses_declared_size_over_limit_without_reading():
    with pytest.raises(StateTooLarge):
        S3StateReader(FakeS3(length=11), max_bytes=10).read(
            StateLocation("b", "k.tfstate")
        )


def test_reader_refuses_body_over_limit_even_if_size_header_lies():
    with pytest.raises(StateTooLarge):
        S3StateReader(FakeS3(body=b"x" * 11, length=1), max_bytes=10).read(
            StateLocation("b", "k.tfstate")
        )


@pytest.mark.parametrize(
    "error, expected",
    [
        (_client_error("NoSuchKey"), StateNotFound),
        (_client_error("AccessDenied"), StateUnreadable),
        (EndpointConnectionError(endpoint_url="https://s3"), StateUnreadable),
    ],
)
def test_reader_classifies_s3_errors(error, expected):
    with pytest.raises(expected):
        S3StateReader(FakeS3(error=error)).read(StateLocation("b", "k.tfstate"))


@pytest.mark.parametrize("body", [b"not json", b"[1, 2]"])
def test_reader_rejects_non_object_state(body):
    with pytest.raises(StateUnreadable):
        S3StateReader(FakeS3(body=body)).read(StateLocation("b", "k.tfstate"))
