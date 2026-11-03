from __future__ import annotations

import io
import json
import urllib.error

import pytest

from delta_pipeline.clients import Jenkins, ServiceUnavailable


class _Opener:
    def __init__(self, *, code: int | None = None, body: object = None) -> None:
        self._code, self._body = code, body

    def open(self, request, timeout):
        if self._code is not None:
            raise urllib.error.HTTPError(request.full_url, self._code, "error", {}, io.BytesIO())
        return io.BytesIO(json.dumps(self._body).encode())


def _jenkins(opener: _Opener) -> Jenkins:
    jenkins = Jenkins("http://jenkins", "admin", "pw")
    jenkins._opener = opener
    return jenkins


def test_artifact_that_is_not_uploaded_yet_reads_as_none():
    assert _jenkins(_Opener(code=404)).file(1, "metrics-10.json") is None


def test_artifact_lookup_failure_is_not_treated_as_missing():
    with pytest.raises(ServiceUnavailable):
        _jenkins(_Opener(code=503)).file(1, "metrics-10.json")


def test_artifact_is_read_as_json():
    assert _jenkins(_Opener(body={"image_id": "sha256:abc"})).file(1, "image.json") == {"image_id": "sha256:abc"}
