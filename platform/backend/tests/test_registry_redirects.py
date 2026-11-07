"""Image-copy regression tests for Artifact Registry download redirects."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from delta_platform.adapters.registry_copy import RegistryCopy
from delta_platform.adapters.sandbox import SandboxError

HOST = "asia-northeast3-docker.pkg.dev"
SOURCE = HOST + "/project/source/backend"
TARGET = HOST + "/project/production/backend"
BLOB = b'{"architecture":"amd64","os":"linux"}'
DIGEST = "sha256:" + hashlib.sha256(BLOB).hexdigest()
MANIFEST = json.dumps(
    {
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {"digest": DIGEST, "size": len(BLOB)},
        "layers": [],
    }
).encode()
IMAGE_DIGEST = "sha256:" + hashlib.sha256(MANIFEST).hexdigest()
BLOB_URL = "https://" + HOST + "/v2/project/source/backend/blobs/" + DIGEST


class Response:
    def __init__(self, status=200, content=b"", headers=None):
        self.status_code, self.content, self.headers = status, content, headers or {}
        self.closed = False

    def raise_for_status(self):
        assert self.status_code < 400

    def iter_content(self, _):
        yield self.content

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def copy_with_redirects(redirects, calls, responses):
    class Http:
        def request(self, method, url, headers, **kwargs):
            assert kwargs["allow_redirects"] is False
            calls.append((method, url, dict(headers)))
            if method == "GET" and url in redirects:
                response = Response(302, headers={"Location": redirects[url]})
                responses.append(response)
                return response
            if "/manifests/" in url:
                return Response(content=MANIFEST)
            if method == "HEAD":
                return Response(404)
            if method == "GET":
                return Response(content=BLOB)
            if method == "POST":
                return Response(headers={"Location": "https://" + HOST + "/upload/id"})
            return Response()

    token = SimpleNamespace(token="fixture-token", refresh=lambda _: None)
    copier = RegistryCopy(
        SimpleNamespace(repository=TARGET, allowed_image_prefixes=[SOURCE + "@"]),
        SimpleNamespace(credentials=token),
        http=Http(),
    )
    return copier.copy(SOURCE + "@" + IMAGE_DIGEST, lambda: None)


def test_relative_registry_redirect_then_storage_preserves_digest_and_scopes_auth():
    calls, responses = [], []
    registry_download = "https://" + HOST + "/artifact-download/signed"
    storage_download = "https://storage.googleapis.com/bucket/blob?signature=fixture"
    result = copy_with_redirects(
        {BLOB_URL: "/artifact-download/signed", registry_download: storage_download},
        calls,
        responses,
    )
    assert result == TARGET + "@" + IMAGE_DIGEST
    assert next(h for m, u, h in calls if u == registry_download)["Authorization"]
    assert next(h for m, u, h in calls if u == storage_download) == {}
    assert all(r.closed for r in responses)


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example/blob",
        "//evil.example/blob",
        "http://" + HOST + "/blob",
        "https://storage.googleapis.com.evil.example/blob",
        "https://user:pass@storage.googleapis.com/blob",
        "https://storage.googleapis.com:8443/blob",
        "",
    ],
)
def test_untrusted_redirect_is_rejected_before_request(location):
    calls, responses = [], []
    with pytest.raises(SandboxError):
        copy_with_redirects({BLOB_URL: location}, calls, responses)
    assert calls[-1][1] == BLOB_URL
    assert all(r.closed for r in responses)


def test_redirect_loop_is_bounded_and_closed():
    calls, responses = [], []
    with pytest.raises(SandboxError):
        copy_with_redirects({BLOB_URL: BLOB_URL}, calls, responses)
    assert len(responses) <= 6
    assert all(r.closed for r in responses)
