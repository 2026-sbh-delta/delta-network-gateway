"""Bridge to the platform's durable on-premise queue, not a second K3s runner.

The existing worker retains ownership, the PostgreSQL sandbox lock, cancellation
and cleanup. Construct NativeOnpremJob only inside a trusted platform context.
"""

import re
import time
from uuid import UUID

from delta_pipeline.clients import (
    ServiceUnavailable,
    TransientServiceError,
    evidence_hash,
)


class NativeOnpremJob:
    def __init__(
        self,
        store,
        settings,
        *,
        service_id,
        build_id,
        security_check_id=None,
        backend_build_id=None,
        cleanup_probe=None,
    ):
        self.store, self.settings = store, settings
        self.cleanup_probe = cleanup_probe
        self.service_id = UUID(str(service_id))
        self.build_id = UUID(str(build_id))
        self.security_id = UUID(str(security_check_id)) if security_check_id else None
        self.kind = "dast_check" if self.security_id else "sandbox_check"
        self.backend_build_id = (
            UUID(str(backend_build_id)) if backend_build_id else None
        )

    def start(self, key, *, repo, commit, image, sast_run_id, sast_evidence_sha256):
        from delta_platform.adapters.dast import dast_target
        from delta_platform.adapters.sandbox import sandbox_target
        from delta_platform.core.errors import Problem

        try:
            service = self.store.service(self.service_id)
            build = self.store.run(self.build_id, "image_build")
            if (
                service["repository_url"] != repo
                or build["status"] != "succeeded"
                or str(build["service_id"]) != str(self.service_id)
                or build["resolved_commit_sha"] != commit
                or build["result"]["image"] != image
            ):
                raise ValueError()
            security = (
                self.store.run(self.security_id, "security_check")
                if self.security_id
                else None
            )
            if security and (
                security["status"] != "succeeded"
                or str(security["service_id"]) != str(self.service_id)
                or security["resolved_commit_sha"] != commit
                or security["result"]["build_id"] != str(self.build_id)
                or security["result"]["scanner_run_id"] != sast_run_id
                or security["result"]["evidence_sha256"] != sast_evidence_sha256
            ):
                raise ValueError()
            job, _ = self.store.enqueue_sandbox(
                self.service_id,
                self.build_id,
                key,
                sandbox_target(self.settings, "onprem"),
                backend_build_id=self.backend_build_id,
                security_check_id=self.security_id,
                dast_target=dast_target(self.settings) if self.security_id else None,
            )
            return str(job["id"])
        except (Problem, KeyError, ValueError, TypeError):
            raise ServiceUnavailable(
                "온프레미스 설정·동일 서비스의 빌드/SAST 근거·실행 슬롯을 확인하세요"
            ) from None

    def get(self, run_id):
        from sqlalchemy.exc import DBAPIError

        try:
            value = self.store.run(UUID(run_id), self.kind)
        except DBAPIError as exc:
            # Read-only retry: do not classify arbitrary DB/auth/configuration errors as transient.
            temporary = exc.connection_invalidated or getattr(exc.orig, "sqlstate", None) in {
                "08001", "08003", "08006", "57P01", "57P02", "57P03", "40001", "40P01",
            }
            error = TransientServiceError if temporary else ServiceUnavailable
            raise error("온프레미스 작업 저장소 조회 실패") from None
        if str(value["service_id"]) != str(self.service_id):
            raise ServiceUnavailable("온프레미스 실행 소유권이 다릅니다")
        return value

    def retry_ready(self, run_id, *, key, commit, image):
        return self._retry_ready(run_id, key=key, commit=commit, image=image,
                                 codes={'sandbox_health_failed'})

    def resume_ready(self, run_id, *, key, commit, image):
        return self._retry_ready(run_id, key=key, commit=commit, image=image,
                                 codes={'sandbox_health_failed', 'sandbox_provider_error'})

    def _retry_ready(self, run_id, *, key, commit, image, codes):
        """Read-only proof before a new native job; never delete or unlock here.

        The existing worker must have finished cleanup, and a live GET must confirm
        absence of both resources. Bundle recovery is intentionally unsupported.
        """
        import sqlalchemy as sa
        from delta_platform.adapters.onprem_sandbox import OnpremSandboxRunner
        from delta_platform.adapters.sandbox import sandbox_target
        from delta_platform.core.schema import runs

        job = self.get(run_id)
        if (self.backend_build_id is not None or job["status"] != "failed"
                or job.get("cancel_requested") is not False
                or (job.get("error") or {}).get("code") not in codes
                or job.get("resolved_commit_sha") != commit
                or job.get("current_step") != "sandbox_cleanup"):
            return False
        with self.store.engine.connect() as conn:
            row = conn.execute(sa.select(runs.c.input_snapshot, runs.c.request_key).where(
                runs.c.id == UUID(run_id), runs.c.service_id == self.service_id,
                runs.c.kind == self.kind)).mappings().first()
        source = row["input_snapshot"] if row else None
        target = sandbox_target(self.settings, "onprem")
        probe = self.cleanup_probe or OnpremSandboxRunner(self.settings).provider
        if (not source or row["request_key"] != key or source.get("backend")
                or source.get("build_id") != str(self.build_id)
                or source.get("security_check_id") != (str(self.security_id) if self.security_id else None)
                or source.get("image") != image
                or source.get("sandbox_target") != target
                or probe.target.model_dump() != target
                or job.get("external_id") != probe.target.resource(run_id)):
            return False
        name = probe.target.name(run_id)
        return all(probe.call("GET", collection, name, missing=True) is None
                   for collection in ("pods", "secrets"))

    def observation_ready(self, run_id, *, key, commit, image):
        import sqlalchemy as sa
        from delta_platform.adapters.sandbox import sandbox_target
        from delta_platform.core.schema import runs

        job = self.get(run_id)
        with self.store.engine.connect() as conn:
            row = conn.execute(sa.select(runs.c.input_snapshot, runs.c.request_key).where(
                runs.c.id == UUID(run_id), runs.c.service_id == self.service_id, runs.c.kind == self.kind)).mappings().first()
        source = row['input_snapshot'] if row else {}
        return bool(row and row['request_key'] == key and not job.get('cancel_requested')
            and job.get('resolved_commit_sha') == commit and source.get('image') == image
            and source.get('build_id') == str(self.build_id)
            and source.get('security_check_id') == (str(self.security_id) if self.security_id else None)
            and source.get('sandbox_target') == sandbox_target(self.settings, 'onprem'))

    def inspect(self, run_id):
        """Read own immutable snapshot/events and, if still present, own Pod status/log."""
        import sqlalchemy as sa
        from delta_ai.diagnostic_contract import safe_text, scrub
        from delta_platform.adapters.onprem_sandbox import OnpremSandboxRunner
        from delta_platform.adapters.sandbox import sandbox_target
        from delta_platform.core.schema import runs

        job = self.get(run_id)
        with self.store.engine.connect() as conn:
            source = conn.scalar(sa.select(runs.c.input_snapshot).where(
                runs.c.id == UUID(run_id), runs.c.service_id == self.service_id, runs.c.kind == self.kind))
        target = sandbox_target(self.settings, 'onprem')
        changed = sorted(k for k in set(target) | set((source or {}).get('sandbox_target', {}))
                         if target.get(k) != (source or {}).get('sandbox_target', {}).get(k))
        result = {'facts': {k: job.get(k) for k in ('status', 'current_step', 'resolved_commit_sha',
                  'external_id', 'cancel_requested')}, 'limitations': []}
        result['facts']['error_code'] = (job.get('error') or {}).get('code')
        result['facts']['changed_target_fields'] = changed
        result['facts']['build_matches'] = (source or {}).get('build_id') == str(self.build_id)
        result['facts']['image_digest'] = (source or {}).get('image_digest')
        # Events are scoped by the already verified job ID; only public event summaries leave here.
        events = self.store.run_events(UUID(run_id), 0, 40)
        if isinstance(events, dict):
            events = events.get('items') or events.get('events') or []
        result['events'] = [{k: e.get(k) for k in ('sequence', 'event_type', 'step', 'message')}
                            for e in events[-20:]]
        if changed or not source or source.get('backend'):
            result['limitations'].append('설정 차이 또는 bundle 실행이어서 Pod 로그를 조회하지 않았습니다.')
            return scrub(result)
        probe = self.cleanup_probe or OnpremSandboxRunner(self.settings).provider
        if job.get('external_id') not in (None, probe.target.resource(run_id)):
            raise ServiceUnavailable('실행 자원 소유권이 다릅니다')
        pod = probe.call('GET', 'pods', probe.target.name(run_id), missing=True)
        if pod is None:
            result['limitations'].append('Pod가 이미 정리되어 컨테이너 로그를 읽을 수 없습니다.')
            return scrub(result)
        probe.owned(pod, run_id, job['resolved_commit_sha'], source['image'])
        meta, status = pod.get('metadata', {}), pod.get('status', {})
        result['pod'] = {'uid': meta.get('uid'), 'phase': status.get('phase'),
                         'conditions': status.get('conditions', [])[:5]}
        try:
            if self.cleanup_probe is not None:
                raw = probe.logs(run_id)  # Explicit trusted test/provider port, not an HTTP user parameter.
            else:
                import httpx
                endpoint = probe.target.api_url + '/api/v1/namespaces/' + probe.target.namespace + '/pods/' + probe.target.name(run_id) + '/log'
                with httpx.Client(verify=probe.tls, timeout=3, trust_env=False, follow_redirects=False) as client:  # noqa: SIM117 — bounded stream lifetime
                    with client.stream('GET', endpoint, params={'tailLines': 40, 'limitBytes': 6000},
                        headers={'Authorization': 'Bearer ' + self.settings.onprem_sandbox_token.get_secret_value()}) as response:
                        response.raise_for_status()
                        raw = response.iter_bytes()
                        collected = bytearray()
                        expires = time.monotonic() + 3
                        for chunk in raw:
                            if time.monotonic() > expires:
                                break
                            collected.extend(chunk[:6000-len(collected)])
                            if len(collected) >= 6000:
                                break
                        raw = collected.decode('utf-8', errors='replace')
            result['logs'] = safe_text(str(raw)[:6000])
        except Exception:  # noqa: BLE001 — missing pod/log RBAC must stay a visible evidence gap
            result['limitations'].append('Pod 로그 권한/연결을 확인하지 못했습니다.')
        return scrub(result)

    def cancel(self, run_id):
        self.get(run_id)
        self.store.cancel_sandbox(UUID(run_id), self.kind)


class RegisteredSast:
    """Read the platform's completed SAST run, keeping its real scanner UUID."""

    def __init__(self, client, *, run_id, repo, commit):
        self.client, self.run_id, self.repo, self.commit = (
            client,
            str(UUID(str(run_id))),
            repo,
            commit,
        )

    def start(self, run_id, repo, commit):
        if (repo, commit) != (self.repo, self.commit):
            raise ServiceUnavailable("등록된 SAST와 저장소·커밋이 다릅니다")
        return self.scan(self.run_id)

    def scan(self, run_id):
        if run_id != self.run_id:
            raise ServiceUnavailable("등록된 SAST 실행이 아닙니다")
        value = self.client.scan(run_id)
        if value.get("run_id") != self.run_id or value.get("commit") != self.commit:
            raise ServiceUnavailable("등록된 SAST의 실행 ID·커밋이 다릅니다")
        return value

    def sarif(self, run_id):
        self.scan(run_id)
        return self.client.sarif(run_id)


def start(steps, state):
    from delta_pipeline.steps import Stopped, assert_gates_passed

    deps = steps._deps
    assert_gates_passed(state.get("gates") or {}, ("sast",))
    if deps.cancelled():
        raise Stopped("온프레미스 배포 전에 취소했습니다")
    if state.get("recovery_previous_run_id"):
        ready = deps.onprem.resume_ready if state.get("manual_redeploy") else deps.onprem.retry_ready
        if not ready(state["recovery_previous_run_id"], key=state["recovery_previous_key"],
                     commit=steps._commit(state), image=state["image"]):
            raise Stopped("재실행 직전 소유권·설정·자원 정리를 확인하지 못했습니다")
    report = deps.sast.sarif(state["sast_run_id"])
    report_hash = evidence_hash(report)
    identifier = deps.onprem.start(
        steps._key(state, "onprem"),
        repo=deps.options.repo,
        commit=steps._commit(state),
        image=state["image"],
        sast_run_id=state["sast_run_id"],
        sast_evidence_sha256=report_hash,
    )
    UUID(identifier)
    deps.board.update(
        "stage",
        state="running",
        summary="기존 플랫폼 Worker에서 온프레미스 배포·검사·정리 실행 중",
    )
    deps.board.add_detail("stage", platform_run_id=identifier, execution_cloud="onprem",
                          platform_status="queued", cleanup="pending", error_code=None)
    return {"onprem_run_id": identifier, "onprem_evidence_sha256": report_hash}


def wait(steps, state):
    from delta_pipeline.steps import POLL_SECONDS, STAGE_DEADLINE_SECONDS, Stopped

    deps, run_id = steps._deps, state["onprem_run_id"]
    deadline = time.monotonic() + STAGE_DEADLINE_SECONDS
    captures, last_capture = 0, -100.0
    while True:
        if deps.cancelled():
            deps.onprem.cancel(
                run_id
            )  # Native cancellation is idempotent; never delete by a new identity.
        job = steps._read(lambda: deps.onprem.get(run_id))
        if str(job.get("id")) != run_id:
            raise ServiceUnavailable("온프레미스 실행 ID가 다릅니다")
        if job.get("status") not in ("queued", "running"):
            break
        if (deps.options.assistance and job.get('status') == 'running'
                and job.get('current_step') in ('sandbox_verify', 'dast_wait')
                and captures < 3 and time.monotonic() - last_capture >= 10):
            from delta_pipeline.evidence import capture_runtime
            capture_runtime(deps, state)
            captures += 1
            last_capture = time.monotonic()
        if job.get("current_step") == "sandbox_cleanup_wait":
            deps.board.add_detail("stage", cleanup="pending", platform_run_id=run_id)
            deps.board.update(
                "stage",
                state="fail",
                summary="온프레미스 정리 미확인. 기존 Worker가 실행 슬롯을 유지합니다",
            )
            raise Stopped("온프레미스 정리 미확인. 배포·검사를 다시 실행하지 않습니다")
        if time.monotonic() > deadline:
            deps.onprem.cancel(run_id)
            deps.board.add_detail("stage", cleanup="pending", platform_run_id=run_id)
            raise Stopped(
                "온프레미스 실행 대기 초과. 취소 요청 후 Worker의 정리 확인이 필요합니다"
            )
        deps.sleep(POLL_SECONDS)
    if job.get("status") != "succeeded":
        code = (job.get("error") or {}).get("code", "execution_failed")
        deps.board.update("stage", state="fail", summary="온프레미스 실행 실패")
        deps.board.add_detail(
            "stage",
            platform_status=job.get("status"),
            error_code=code,
            platform_step=job.get("current_step"),
            cleanup="worker_terminal",
        )
        raise Stopped(f"온프레미스 실행 실패: {code}", code=code)
    value = job.get("result") or {}
    if (
        value.get("execution_cloud") != "onprem"
        or value.get("runtime") != "k3s"
        or value.get("cleanup") != "stopped"
        or value.get("runtime_check") != "passed"
        or value.get("image") != state["image"]
        or value.get("image_digest") != state["image_digest"]
        or value.get("commit_sha") != steps._commit(state)
        or job.get("kind") not in ("dast_check", "sandbox_check")
        or (
            job.get("kind") == "dast_check"
            and (
                value.get("scanner_run_id") != run_id
                or value.get("sast_evidence_sha256") != state["onprem_evidence_sha256"]
            )
        )
        or value.get("resource")
        != "namespaces/delta-platform-sandbox/pods/delta-sbx-" + UUID(run_id).hex
        or not isinstance(value.get("revision"), str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value["revision"])
    ):
        raise Stopped("온프레미스 실행 이미지·자원·정리 결과가 다릅니다")
    deps.board.update(
        "stage", state="pass", summary="온프레미스 Pod UID·이미지·HTTP·정리 확인"
    )
    deps.board.add_detail(
        "stage",
        platform_run_id=run_id,
        cleanup="stopped",
        runtime_resource=value["resource"],
        runtime_revision=value["revision"],
    )
    if job["kind"] == "sandbox_check":
        deps.board.add_detail("dast", coverage="not_run", deployment_approved=False)
        return steps._ask(
            "dast", "dast", "온프레미스 실행 검사만 완료 · DAST 미실행", 0
        )
    findings = value.get("dast_findings")
    eligible = value.get("eligible_count")
    if (
        not isinstance(findings, list)
        or value.get("deployment_approved") is not False
        or value.get("coverage") != "sast_xss_candidates_only"
        or type(eligible) is not int
        or eligible < len(findings)
        or type(value.get("requires_review")) is not bool
        or value.get("findings_truncated") is not (eligible > len(findings))
        or not re.fullmatch(r"[a-f0-9]{64}", str(value.get("dast_evidence_sha256", "")))
        or any(
            not isinstance(f, dict)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(f.get("finding_id", "")))
            or not re.fullmatch(r"[a-f0-9]{64}", str(f.get("evidence_sha256", "")))
            for f in findings
        )
    ):
        raise Stopped("온프레미스 DAST 근거가 없습니다")
    deps.board.add_detail("dast", run_id=run_id, results=findings, cleanup="stopped")
    if value.get("dast_check") != "completed":
        return steps._ask("dast", "dast", "온프레미스 DAST 미실행·검사 범위 확인", 0)
    if (
        value["requires_review"]
        and not any(f.get("status") != "NOT_REPRODUCED" for f in findings)
        and not value["findings_truncated"]
    ):
        return steps._ask(
            "dast", "dast", "온프레미스 실행기가 추가 확인을 요구합니다", len(findings)
        )
    gate = steps._dast_gate(
        {
            "status": "done",
            "results": findings,
            "finding_ids": [f.get("finding_id") for f in findings],
            "eligible_count": value.get("eligible_count"),
            "evidence_sha256": value.get("dast_evidence_sha256"),
        },
        {**state, "dast_run_id": run_id},
    )
    if gate.get("pending_gate"):
        # A native sandbox is already cleaned. A fix must create a new platform build/security job.
        gate["pending_gate"].pop("fix_target", None)
    return {**gate, "dast_run_id": run_id}
