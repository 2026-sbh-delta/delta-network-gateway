"""Copy a pinned ECR manifest to Artifact Registry without rebuilding it."""

import base64
import hashlib
import json
import re
import tempfile
from urllib.parse import urlencode, urljoin, urlsplit

import boto3
import google.auth.transport.requests
import requests

from delta_platform.adapters.sandbox import SandboxError

MAX_BLOB = 512 * 1024 * 1024
MAX_IMAGE = 2 * 1024 * 1024 * 1024
MEDIA = (
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
)


class RegistryCopy:
    def __init__(self, target, google_session, *, ecr=None, http=None):
        self.target, self.google_session = target, google_session
        self.ecr, self.http = ecr, http or requests.Session()

    def copy(self, image, guard):
        if image.rsplit("@", 1)[0] + "@" not in self.target.allowed_image_prefixes:
            raise SandboxError("sandbox_image_invalid", "허용한 ECR 저장소가 아닙니다.")
        source, digest = image.rsplit("@", 1)
        host, repo = source.split("/", 1)
        ecr = self.ecr or boto3.client("ecr", region_name=host.split(".")[3])
        data = ecr.get_authorization_token(registryIds=[host.split(".")[0]])["authorizationData"][0]
        if data["proxyEndpoint"] != "https://" + host:
            raise SandboxError("sandbox_image_invalid", "ECR 인증 대상이 다릅니다.")
        # Credentials are scoped to requests for their own exact registry only.
        source_headers = {
            "Authorization": "Basic " + data["authorizationToken"],
            "Accept": ", ".join(MEDIA),
        }
        dest_host, dest_repo = self.target.repository.split("/", 1)
        base = f"https://{dest_host}/v2/{dest_repo}"
        credentials = self.google_session.credentials
        credentials.refresh(google.auth.transport.requests.Request())
        auth = base64.b64encode(("oauth2accesstoken:" + credentials.token).encode()).decode()
        dest_headers = {"Authorization": "Basic " + auth}

        def request(method, url, *, headers, **kwargs):
            guard()
            return self.http.request(
                method, url, headers=headers, timeout=120, allow_redirects=False, **kwargs
            )

        response = request(
            "GET", f"https://{host}/v2/{repo}/manifests/{digest}", headers=source_headers
        )
        response.raise_for_status()
        raw = response.content
        if len(raw) > 2_000_000 or "sha256:" + hashlib.sha256(raw).hexdigest() != digest:
            raise SandboxError("sandbox_image_invalid", "원본 이미지 manifest의 digest가 다릅니다.")
        manifest = json.loads(raw)
        if manifest.get("mediaType") not in MEDIA or "manifests" in manifest:
            raise SandboxError(
                "sandbox_image_invalid", "단일 linux/amd64 이미지만 복사할 수 있습니다."
            )
        blobs = [manifest["config"], *manifest["layers"]]
        if sum(b["size"] for b in blobs) > MAX_IMAGE or any(b["size"] > MAX_BLOB for b in blobs):
            raise SandboxError(
                "sandbox_image_too_large", "현재 샌드박스의 이미지 복사 크기 제한을 초과했습니다."
            )
        for blob in blobs:
            blob_digest = blob["digest"]
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", blob_digest):
                raise SandboxError("sandbox_image_invalid", "이미지 레이어 식별자가 잘못됐습니다.")
            exists = request("HEAD", base + "/blobs/" + blob_digest, headers=dest_headers)
            if exists.status_code == 200 and blob is not manifest["config"]:
                continue
            if exists.status_code not in (200, 404):
                exists.raise_for_status()
            # ECR issues a presigned download URL; never send registry auth to that host.
            download = ecr.get_download_url_for_layer(
                repositoryName=repo, layerDigest=blob_digest, registryId=host.split(".")[0]
            )["downloadUrl"]
            parsed = urlsplit(download)
            if (
                parsed.scheme != "https"
                or not parsed.hostname.endswith(".amazonaws.com")
                or parsed.username
            ):
                raise SandboxError("sandbox_image_invalid", "ECR 다운로드 주소가 잘못됐습니다.")
            with tempfile.TemporaryFile() as datafile:
                received, checksum = 0, hashlib.sha256()
                with request("GET", download, headers={}, stream=True) as stream:
                    stream.raise_for_status()
                    for chunk in stream.iter_content(1024 * 1024):
                        guard()
                        received += len(chunk)
                        if received > blob["size"] or received > MAX_BLOB:
                            raise SandboxError(
                                "sandbox_image_invalid", "이미지 레이어 크기가 다릅니다."
                            )
                        checksum.update(chunk)
                        datafile.write(chunk)
                if received != blob["size"] or "sha256:" + checksum.hexdigest() != blob_digest:
                    raise SandboxError(
                        "sandbox_image_invalid", "복사한 이미지 레이어의 digest가 다릅니다."
                    )
                datafile.seek(0)
                if blob is manifest["config"]:
                    config = json.load(datafile)
                    if config.get("architecture") != "amd64" or config.get("os") != "linux":
                        raise SandboxError(
                            "sandbox_image_invalid", "Cloud Run용 linux/amd64 이미지가 아닙니다."
                        )
                    datafile.seek(0)
                upload = request("POST", base + "/blobs/uploads/", headers=dest_headers)
                upload.raise_for_status()
                location = urljoin(base + "/", upload.headers["Location"])
                if urlsplit(location).scheme != "https" or urlsplit(location).netloc != dest_host:
                    raise SandboxError("sandbox_image_invalid", "이미지 업로드 목적지가 다릅니다.")
                location += ("&" if "?" in location else "?") + urlencode({"digest": blob_digest})
                sent = request(
                    "PUT",
                    location,
                    headers={
                        **dest_headers,
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(received),
                    },
                    data=datafile,
                )
                sent.raise_for_status()
        result = request(
            "PUT",
            base + "/manifests/" + digest,
            headers={**dest_headers, "Content-Type": manifest["mediaType"]},
            data=raw,
        )
        result.raise_for_status()
        verify = request(
            "GET",
            base + "/manifests/" + digest,
            headers={**dest_headers, "Accept": ", ".join(MEDIA)},
        )
        verify.raise_for_status()
        if "sha256:" + hashlib.sha256(verify.content).hexdigest() != digest:
            raise SandboxError(
                "sandbox_image_invalid", "GCP 저장소의 이미지 digest가 원본과 다릅니다."
            )
        return self.target.repository + "@" + digest
