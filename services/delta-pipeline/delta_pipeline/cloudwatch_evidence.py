"""Bounded read-only log facts. Environment samples never establish run causality."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time

from delta_ai.diagnostic_contract import DiagnosisRequest, Observation, diagnosis_hash

GROUPS = (
    ("cw-run", "/delta-cp/platform/orchestrator", "execution"),
    ("cw-aws", "/delta-cp/central/aws", "environment"),
    ("cw-gcp", "/delta-cp/central/gcp", "environment"),
)
MAX_EVENTS = 40
MAX_PAGES = 2
MAX_MESSAGE = 8192
WINDOW_SECONDS = 600


def aws_logs():
    import boto3
    from botocore.config import Config
    return boto3.client("logs", region_name="ap-northeast-2", config=Config(
        connect_timeout=2, read_timeout=3, retries={"total_max_attempts": 1}))


class CloudWatchEvidence:
    def __init__(self, directory: Path, *, client_factory=aws_logs, now=time.time):
        self.directory = Path(directory)
        self.client_factory, self.now = client_factory, now

    @staticmethod
    def _observation(identifier, value):
        return {"evidence_id": identifier, "source": "cloudwatch",
                "summary": json.dumps({"kind": "delta-log-evidence/v1", **value},
                                      ensure_ascii=False, separators=(",", ":"))}

    def _group(self, client, identifier, group, scope, request, start, end):
        facts = {"group": group, "scope": scope, "start_ms": start, "end_ms": end,
                 "status": "empty", "events": 0, "unparsed": 0, "discarded": 0,
                 "sample_events": 0, "truncated": False}
        if scope == "environment":
            facts["limitation"] = "환경 참고 자료이며 해당 앱/실행과 연관성 미확인"
        query = {"logGroupName": group, "startTime": start, "endTime": end, "limit": 20}
        if scope == "execution":
            query["filterPattern"] = json.dumps(request.run_id)
        counts, node_states = Counter(), Counter()
        seen = set()
        try:
            for page in range(MAX_PAGES):
                response = client.filter_log_events(**query)
                for event in response.get("events", ())[:20]:
                    timestamp = event.get("timestamp")
                    if type(timestamp) is not int or not start <= timestamp < end:
                        facts["discarded"] += 1
                        continue
                    identity = event.get("eventId") or hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()
                    if identity in seen:
                        continue
                    seen.add(identity)
                    message = event.get("message")
                    if not isinstance(message, str) or len(message.encode()) > MAX_MESSAGE:
                        facts["unparsed"] += 1
                        continue
                    try:
                        value = json.loads(message)
                        if not isinstance(value, dict):
                            raise ValueError()
                    except (ValueError, TypeError):
                        facts["unparsed"] += 1
                        continue
                    if scope == "execution":
                        if value.get("run_id") != request.run_id:
                            facts["discarded"] += 1
                            continue
                        state = value.get("to")
                        if state in ("running", "pass", "fail", "waiting", "warn", "skipped"):
                            node_states[state] += 1
                        else:
                            facts["unparsed"] += 1
                    elif identifier == "cw-gcp":
                        original = value.get("original") or {}
                        payload = original.get("jsonPayload") or {}
                        disposition = payload.get("disposition")
                        if disposition in ("ALLOWED", "DENIED", "ALLOW", "DENY"):
                            counts[disposition] += 1
                        else:
                            facts["unparsed"] += 1
                    else:
                        try:
                            original = json.loads(value.get("message", ""))
                            detail = original.get("detail", original)
                            sample = detail.get("service", {}).get("additionalInfo", {}).get("sample")
                            severity = detail.get("severity")
                            if type(sample) is bool:
                                facts["sample_events"] += int(sample)
                            else:
                                facts["unparsed"] += 1
                            if isinstance(severity, (float, int)) and not isinstance(severity, bool) and 0 <= severity <= 10:
                                counts["high" if severity >= 7 else "medium" if severity >= 4 else "low"] += 1
                            else:
                                facts["unparsed"] += 1
                        except (ValueError, TypeError, AttributeError):
                            facts["unparsed"] += 1
                    facts["events"] += 1
                    if len(seen) >= MAX_EVENTS:
                        facts["truncated"] = True
                        break
                token = response.get("nextToken")
                if len(seen) >= MAX_EVENTS or not token or token == query.get("nextToken"):
                    if token:
                        facts["truncated"] = True
                    break
                query["nextToken"] = token
                if page == MAX_PAGES - 1:
                    facts["truncated"] = True
            facts["status"] = "available" if facts["events"] else "unparsed" if facts["unparsed"] else "empty"
        except Exception as error:  # no provider message crosses the boundary
            facts["status"] = "unavailable"
            response = getattr(error, "response", {})
            code = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
            facts["error_kind"] = {"AccessDeniedException": "permission_denied",
                "AccessDenied": "permission_denied", "ResourceNotFoundException": "not_found",
                "ThrottlingException": "rate_limited"}.get(code, "read_failed")
        if counts:
            facts["counts"] = dict(counts)
        if node_states:
            facts["node_states"] = dict(node_states)
        return self._observation(identifier, facts)

    def _collect(self, request, started_at):
        end_seconds = self.now()
        if (not isinstance(started_at, (int, float)) or not math.isfinite(started_at)
                or started_at <= 0 or started_at > end_seconds):
            return [self._observation("cw-gap", {"status": "unavailable", "reason": "execution time not verified"})]
        start = int(max(started_at, end_seconds - WINDOW_SECONDS) * 1000)
        end = int(end_seconds * 1000)
        try:
            client = self.client_factory()
        except Exception:
            return [self._observation("cw-gap", {"status": "unavailable", "reason": "log client unavailable"})]
        return [self._group(client, identifier, group, scope, request, start, end)
                for identifier, group, scope in GROUPS]

    def enrich(self, request: DiagnosisRequest, *, started_at) -> DiagnosisRequest:
        original = diagnosis_hash(request)
        try:
            if self.directory.is_symlink():
                raise ValueError("Unsafe evidence directory")
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = self.directory / (request.case_id + ".json")
            if path.is_symlink():
                raise ValueError("Unsafe evidence path")
            if path.exists():
                if path.stat().st_size > 20000:
                    raise ValueError("Oversized evidence cache")
                saved = json.loads(path.read_bytes())
                if saved["original_hash"] != original:
                    raise ValueError("Evidence request mismatch")
                additions = saved["observations"]
                if saved["sha256"] != hashlib.sha256(json.dumps(additions, sort_keys=True).encode()).hexdigest():
                    raise ValueError("Evidence cache mismatch")
                if len(additions) > 3:
                    raise ValueError("Unexpected cached observations")
                additions = [Observation.model_validate(o).model_dump(mode="json") for o in additions]
            else:
                additions = self._collect(request, started_at)
                saved = {"original_hash": original, "observations": additions,
                         "sha256": hashlib.sha256(json.dumps(additions, sort_keys=True).encode()).hexdigest()}
                fd, name = tempfile.mkstemp(prefix=".evidence-", dir=self.directory)
                try:
                    with os.fdopen(fd, "w") as stream:
                        json.dump(saved, stream);stream.flush();os.fsync(stream.fileno())
                    os.replace(name, path)
                finally:
                    Path(name).unlink(missing_ok=True)
        except Exception:
            additions = [self._observation("cw-gap", {"status": "unavailable", "reason": "evidence cache not verified"})]
        body = request.model_dump(mode="json")
        existing = {o.evidence_id for o in request.observations}
        if existing & {o["evidence_id"] for o in additions}:
            additions = [self._observation("cw-collection-gap", {"status": "unavailable", "reason": "evidence identifier conflict"})]
        body["observations"] += additions
        if len(body["observations"]) > 40 or len(json.dumps(body, ensure_ascii=False).encode()) > 63000:
            # Preserve all original scanner facts. A single bounded gap is preferable to silent truncation.
            body["observations"] = body["observations"][:len(request.observations)]
            if len(body["observations"]) < 40:
                body["observations"].append(self._observation("cw-budget-gap", {"status": "unavailable", "reason": "input budget exceeded"}))
        return DiagnosisRequest.model_validate(body)
