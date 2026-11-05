"""Two immutable images, one isolated run; retain the existing DAST frontend contract."""

import hashlib
import json
import re

from .onprem_sandbox import K3s, OnpremTarget
from .sandbox import SandboxError


class BackendTarget(OnpremTarget):
    def name(self, run_id):
        return super().name(run_id) + "-be"

    @property
    def url(self):
        return f"http://sandbox-backend.{self.namespace}.svc.cluster.local:8080"


class BackendRuntime(K3s):
    app_label = "delta-platform-sandbox-backend"
    service_name = "sandbox-backend"
    check_http = False


class BundleRuntime:
    def __init__(self, frontend, backend, *, backend_provider=None):
        self.frontend = frontend
        self.source = backend
        self.backend = backend_provider or BackendRuntime(
            BackendTarget.model_validate(frontend.target.model_dump()),
            frontend.token,
            transport=frontend.transport,
            ecr=frontend.ecr,
            poll_seconds=frontend.poll_seconds,
            timeout=frontend.timeout,
        )
        self.env = {"API_HOST": "sandbox-backend", "API_PORT": "8080", "API_PROTOCOL": "http"}

    def call(self, method, collection, name=None, **kwargs):
        # Always enter create(), which resumes each component independently after a crash.
        if method == "GET" and collection == "pods":
            return None
        return self.frontend.call(method, collection, name, **kwargs)

    def create(self, run_id, commit, image, port, guard):
        other = self.source["image"]
        if (
            not re.fullmatch(r"sha256:[a-f0-9]{64}", self.source["image_digest"])
            or not other.endswith("@" + self.source["image_digest"])
            or other.rsplit("@", 1)[0] + "@" not in self.frontend.target.allowed_image_prefixes
        ):
            raise SandboxError(
                "sandbox_image_invalid", "허용된 저장소의 고정 BE 이미지가 필요합니다."
            )
        for provider, img, p, env in (
            (self.backend, other, 8080, {}),
            (self.frontend, image, port, self.env),
        ):
            guard()
            pod = provider.call("GET", "pods", provider.target.name(run_id), missing=True)
            if pod is None:
                provider.create(run_id, commit, img, p, guard, env=env)
            else:
                provider.owned(pod, run_id, commit, img)

    def verify(self, run_id, commit, image, path, guard):
        backend = self.backend.verify(
            run_id,
            commit,
            self.source["image"],
            self.source["healthcheck_path"],
            guard,
        )
        for provider, extra in ((self.frontend, self.env), (self.backend, {})):
            pod = provider.call("GET", "pods", provider.target.name(run_id))
            actual_env = pod["spec"]["containers"][0].get("env", [])
            expected_env = {"DELTA_RUN_ID": str(run_id), "DELTA_COMMIT": commit, **extra}
            if (
                len(actual_env) != len(expected_env)
                or {e["name"]: e.get("value") for e in actual_env} != expected_env
            ):
                raise SandboxError(
                    "sandbox_identity_mismatch", "앱의 승인된 환경 설정이 달라졌습니다."
                )
        frontend = self.frontend.verify(run_id, commit, image, path, guard)
        # This path is served by BE through FE, proving both components can communicate.
        self.frontend.verify(run_id, commit, image, "/api/health", guard)
        components = {
            "frontend": {**frontend, "image": image},
            "backend": {
                **backend,
                "image": self.source["image"],
                "build_id": self.source["build_id"],
            },
        }
        # The release digest identifies content, not ephemeral pod IDs.
        release = {
            "commit_sha": commit,
            "frontend": image,
            "backend": self.source["image"],
            "frontend_env": self.env,
        }
        return {
            **frontend,
            "components": components,
            "backend_build_id": self.source["build_id"],
            "release_sha256": hashlib.sha256(
                json.dumps(release, sort_keys=True).encode()
            ).hexdigest(),
            "connectivity_check": "frontend_to_backend_passed",
        }

    def cleanup(self, run_id, commit, image, guard):
        failure = None
        for provider, img in ((self.frontend, image), (self.backend, self.source["image"])):
            try:
                provider.cleanup(run_id, commit, img, guard)
            except Exception as exc:
                failure = exc
        if failure:
            raise failure
