import base64
import hashlib
import json
from types import SimpleNamespace

import pytest

from delta_platform.adapters.registry_copy import RegistryCopy
from delta_platform.adapters.sandbox import SandboxError


def test_checked_artifact_registry_pair_can_be_copied_without_ecr_credentials():
    source = "asia-northeast3-docker.pkg.dev/term2-delta/team/backend"
    target = "asia-northeast3-docker.pkg.dev/term2-delta/platform/runtime"
    config = json.dumps({"architecture": "amd64", "os": "linux"}).encode()
    config_hash = "sha256:" + hashlib.sha256(config).hexdigest()
    manifest = json.dumps(
        {
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {"digest": config_hash, "size": len(config)},
            "layers": [],
        }
    ).encode()
    digest = "sha256:" + hashlib.sha256(manifest).hexdigest()
    requests = []

    class Response:
        def __init__(self, status=200, content=b"", headers=None):
            self.status_code, self.content, self.headers = status, content, headers or {}

        def raise_for_status(self):
            assert self.status_code < 400

        def iter_content(self, _):
            yield self.content

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def close(self):
            pass

    class Http:
        def request(self, method, url, headers, **kwargs):
            requests.append((method, url, headers))
            if "/manifests/" in url:
                return Response(content=manifest)
            if method == "HEAD":
                return Response(status=404)
            if method == "GET":
                return Response(content=config)
            if method == "POST":
                return Response(
                    headers={"Location": "https://" + target.split("/")[0] + "/upload/id"}
                )
            return Response()

    token = SimpleNamespace(token="fixture-token", refresh=lambda _: None)
    copier = RegistryCopy(
        SimpleNamespace(repository=target, allowed_image_prefixes=[source + "@"]),
        SimpleNamespace(credentials=token),
        http=Http(),
    )
    assert copier.copy(source + "@" + digest, lambda: None) == target + "@" + digest
    expected = "Basic " + base64.b64encode(b"oauth2accesstoken:fixture-token").decode()
    assert all(h["Authorization"] == expected for _, _, h in requests)
    with pytest.raises(SandboxError):
        copier.copy("unapproved.example/repo@" + digest, lambda: None)
