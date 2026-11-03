import base64
import hashlib
import json
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from .errors import LeaseLost, Problem
from .schema import events, runs, services

SERVICE_FIELDS = (
    "id",
    "name",
    "repository_url",
    "source_ref",
    "build_context",
    "dockerfile_path",
    "container_port",
    "healthcheck_path",
    "created_at",
)
RUN_FIELDS = (
    "id",
    "service_id",
    "kind",
    "status",
    "current_step",
    "resolved_commit_sha",
    "external_id",
    "attempt",
    "result",
    "error",
    "created_at",
    "started_at",
    "finished_at",
)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def select_fields(row, fields):
    return {key: row[key] for key in fields}


def now(conn):
    return conn.scalar(sa.select(sa.func.clock_timestamp()))


def append_event(conn, run_id, event_type, message, step=None, details=None):
    # Caller holds the run row lock, including when assigning sequence numbers.
    sequence = conn.scalar(
        sa.select(sa.func.coalesce(sa.func.max(events.c.sequence), 0) + 1).where(
            events.c.run_id == run_id
        )
    )
    conn.execute(
        events.insert().values(
            run_id=run_id,
            sequence=sequence,
            event_type=event_type,
            message=message,
            step=step,
            details=details or {},
        )
    )


def page_query(conn, table, fields, conditions, limit, cursor):
    query = sa.select(*[table.c[f] for f in fields]).where(*conditions)
    if cursor:
        try:
            if len(cursor) > 256:
                raise ValueError()
            stamp, identifier = json.loads(base64.urlsafe_b64decode(cursor).decode())
            stamp, identifier = datetime.fromisoformat(stamp), UUID(identifier)
            if stamp.tzinfo is None:
                raise ValueError()
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise Problem("invalid_cursor", "목록 커서가 올바르지 않습니다.", 422) from None
        query = query.where(
            sa.tuple_(table.c.created_at, table.c.id) < sa.tuple_(stamp, identifier)
        )
    rows = list(
        conn.execute(
            query.order_by(table.c.created_at.desc(), table.c.id.desc()).limit(limit + 1)
        ).mappings()
    )
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if more:
        last = rows[-1]
        next_cursor = base64.urlsafe_b64encode(
            json.dumps([last["created_at"].isoformat(), str(last["id"])]).encode()
        ).decode()
    return {"items": [dict(row) for row in rows], "next_cursor": next_cursor}


class Store:
    def __init__(self, engine):
        self.engine = engine

    def register(self, data, key):
        payload = data.model_dump()
        request_hash = digest(payload)
        owner, repo = payload["repository_url"].split("/")[-2:]
        with self.engine.begin() as conn:
            row = (
                conn.execute(
                    insert(services)
                    .values(
                        **payload,
                        id=uuid4(),
                        repo_owner=owner,
                        repo_name=repo,
                        request_key=key,
                        request_hash=request_hash,
                    )
                    .on_conflict_do_nothing()
                    .returning(services)
                )
                .mappings()
                .first()
            )
            created = row is not None
            if row is None:
                row = (
                    conn.execute(sa.select(services).where(services.c.request_key == key))
                    .mappings()
                    .first()
                )
                if row is None:
                    raise Problem("service_name_conflict", "같은 이름의 서비스가 있습니다.", 409)
                if row["request_hash"] != request_hash:
                    raise Problem("idempotency_conflict", "같은 요청 키의 내용이 다릅니다.", 409)
            result = select_fields(row, SERVICE_FIELDS)
            result["latest_source_check"] = self._latest(conn, row["id"])
            return result, created

    def _latest(self, conn, service_id, kind="source_preflight"):
        row = (
            conn.execute(
                sa.select(runs)
                .where(runs.c.service_id == service_id, runs.c.kind == kind)
                .order_by(runs.c.created_at.desc(), runs.c.id.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        return select_fields(row, RUN_FIELDS) if row else None

    def service(self, service_id):
        with self.engine.connect() as conn:
            row = (
                conn.execute(sa.select(services).where(services.c.id == service_id))
                .mappings()
                .first()
            )
            if row is None:
                raise Problem("service_not_found", "서비스를 찾을 수 없습니다.", 404)
            return {
                **select_fields(row, SERVICE_FIELDS),
                "latest_source_check": self._latest(conn, service_id),
                "latest_build": self._latest(conn, service_id, "image_build"),
            }

    def list_services(self, limit, cursor):
        with self.engine.connect() as conn:
            return page_query(conn, services, SERVICE_FIELDS, [], limit, cursor)

    def enqueue(self, service_id, key):
        with self.engine.begin() as conn:
            service = (
                conn.execute(
                    sa.select(services).where(services.c.id == service_id).with_for_update()
                )
                .mappings()
                .first()
            )
            if not service:
                raise Problem("service_not_found", "서비스를 찾을 수 없습니다.", 404)
            snapshot = {k: service[k] for k in SERVICE_FIELDS if k not in ("id", "created_at")}
            snapshot.update(
                repo_owner=service["repo_owner"], repo_name=service["repo_name"], version=1
            )
            request_hash = digest(
                {"service_id": str(service_id), "kind": "source_preflight", "input": snapshot}
            )
            existing = (
                conn.execute(
                    sa.select(runs).where(
                        runs.c.service_id == service_id,
                        runs.c.kind == "source_preflight",
                        runs.c.request_key == key,
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                if existing["request_hash"] != request_hash:
                    raise Problem("idempotency_conflict", "같은 요청 키의 내용이 다릅니다.", 409)
                return select_fields(existing, RUN_FIELDS), False
            active = conn.scalar(
                sa.select(runs.c.id).where(
                    runs.c.service_id == service_id, runs.c.status.in_(["queued", "running"])
                )
            )
            if active:
                raise Problem(
                    "source_check_in_progress",
                    "이 서비스에서 진행 중인 작업이 있습니다.",
                    409,
                    run_id=str(active),
                )
            row = (
                conn.execute(
                    runs.insert()
                    .values(
                        id=uuid4(),
                        service_id=service_id,
                        input_snapshot=snapshot,
                        request_key=key,
                        request_hash=request_hash,
                        status="queued",
                    )
                    .returning(runs)
                )
                .mappings()
                .one()
            )
            append_event(conn, row["id"], "queued", "소스 확인을 접수했습니다.")
            return select_fields(row, RUN_FIELDS), True

    def enqueue_build(self, service_id, source_check_id, key, target):
        request_hash = digest({"source_check_id": str(source_check_id)})
        with self.engine.begin() as conn:
            service = conn.scalar(
                sa.select(services.c.id).where(services.c.id == service_id).with_for_update()
            )
            if not service:
                raise Problem("service_not_found", "서비스를 찾을 수 없습니다.", 404)
            existing = (
                conn.execute(
                    sa.select(runs).where(
                        runs.c.service_id == service_id,
                        runs.c.kind == "image_build",
                        runs.c.request_key == key,
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                if existing["request_hash"] != request_hash:
                    raise Problem("idempotency_conflict", "같은 요청 키의 내용이 다릅니다.", 409)
                return select_fields(existing, RUN_FIELDS), False
            source = (
                conn.execute(
                    sa.select(runs).where(
                        runs.c.id == source_check_id,
                        runs.c.service_id == service_id,
                        runs.c.kind == "source_preflight",
                        runs.c.status == "succeeded",
                    )
                )
                .mappings()
                .first()
            )
            if not source:
                raise Problem(
                    "source_check_required", "이 서비스의 성공한 소스 검사가 필요합니다.", 409
                )
            active = conn.scalar(
                sa.select(runs.c.id).where(
                    runs.c.service_id == service_id, runs.c.status.in_(["queued", "running"])
                )
            )
            if active:
                raise Problem(
                    "build_in_progress",
                    "이 서비스에서 진행 중인 작업이 있습니다.",
                    409,
                    run_id=str(active),
                )
            row = (
                conn.execute(
                    runs.insert()
                    .values(
                        id=uuid4(),
                        service_id=service_id,
                        kind="image_build",
                        status="queued",
                        input_snapshot={
                            **source["input_snapshot"],
                            "source_check_id": str(source_check_id),
                            "build_target": target,
                        },
                        resolved_commit_sha=source["resolved_commit_sha"],
                        request_key=key,
                        request_hash=request_hash,
                    )
                    .returning(runs)
                )
                .mappings()
                .one()
            )
            append_event(conn, row["id"], "queued", "확인한 커밋의 이미지 빌드를 접수했습니다.")
            return select_fields(row, RUN_FIELDS), True

    def prepare_submission(self, run_id, token, request):
        with self.engine.begin() as conn:
            row, instant = self._owned(conn, run_id, token)
            if row["submission"]:
                return row["submission"]
            value = {"prepared_at": instant.isoformat(), "request": request}
            conn.execute(runs.update().where(runs.c.id == run_id).values(submission=value))
            return value

    def attach_build(self, run_id, token, external_id):
        with self.engine.begin() as conn:
            row, _ = self._owned(conn, run_id, token)
            if row["external_id"] and row["external_id"] != external_id:
                raise ValueError("External execution must not change")
            conn.execute(runs.update().where(runs.c.id == run_id).values(external_id=external_id))
            if not row["external_id"]:
                append_event(
                    conn,
                    run_id,
                    "build_submitted",
                    "CodeBuild가 빌드를 접수했습니다.",
                    "build_queued",
                )

    def run(self, run_id, kind=None):
        with self.engine.connect() as conn:
            row = conn.execute(sa.select(runs).where(runs.c.id == run_id)).mappings().first()
            if not row or (kind and row["kind"] != kind):
                raise Problem("run_not_found", "검사 이력을 찾을 수 없습니다.", 404)
            return select_fields(row, RUN_FIELDS)

    def list_runs(self, service_id, limit, cursor, kind="source_preflight"):
        self.service(service_id)
        with self.engine.connect() as conn:
            return page_query(
                conn,
                runs,
                RUN_FIELDS,
                [runs.c.service_id == service_id, runs.c.kind == kind],
                limit,
                cursor,
            )

    def run_events(self, run_id, after, limit):
        self.run(run_id)
        with self.engine.connect() as conn:
            rows = list(
                conn.execute(
                    sa.select(events)
                    .where(events.c.run_id == run_id, events.c.sequence > after)
                    .order_by(events.c.sequence)
                    .limit(limit + 1)
                ).mappings()
            )
            return {
                "items": [dict(r) for r in rows[:limit]],
                "has_more": len(rows) > limit,
                "next_sequence": rows[min(len(rows), limit) - 1]["sequence"] if rows else after,
            }

    def claim(self):
        with self.engine.begin() as conn:
            instant = now(conn)
            row = (
                conn.execute(
                    sa.select(runs)
                    .where(
                        sa.or_(
                            sa.and_(runs.c.status == "queued", runs.c.available_at <= instant),
                            sa.and_(runs.c.status == "running", runs.c.lease_expires_at <= instant),
                        )
                    )
                    .order_by(runs.c.available_at, runs.c.created_at)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                .mappings()
                .first()
            )
            if not row:
                return None
            if row["attempt"] >= 3:
                conn.execute(
                    runs.update()
                    .where(runs.c.id == row["id"])
                    .values(
                        status="failed",
                        lease_token=None,
                        lease_expires_at=None,
                        finished_at=instant,
                        error={
                            "code": "attempts_exhausted",
                            "message": "재시도 횟수를 초과했습니다.",
                        },
                    )
                )
                append_event(conn, row["id"], "failed", "재시도 횟수를 초과했습니다.")
                return None
            claimed = (
                conn.execute(
                    runs.update()
                    .where(runs.c.id == row["id"])
                    .values(
                        status="running",
                        lease_token=uuid4(),
                        lease_expires_at=instant + timedelta(seconds=60),
                        attempt=row["attempt"] + 1,
                        started_at=instant,
                        current_step="build_queued"
                        if row["kind"] == "image_build"
                        else "repository",
                    )
                    .returning(runs)
                )
                .mappings()
                .one()
            )
            append_event(
                conn,
                row["id"],
                "reclaimed" if row["status"] == "running" else "started",
                "중단된 검사를 이어갑니다."
                if row["status"] == "running"
                else (
                    "이미지 빌드를 시작했습니다."
                    if row["kind"] == "image_build"
                    else "소스 확인을 시작했습니다."
                ),
            )
            return dict(claimed)

    def _owned(self, conn, run_id, token):
        row = (
            conn.execute(sa.select(runs).where(runs.c.id == run_id).with_for_update())
            .mappings()
            .first()
        )
        instant = now(conn)
        if (
            not row
            or row["status"] != "running"
            or row["lease_token"] != token
            or row["lease_expires_at"] <= instant
            or row["started_at"] + timedelta(seconds=1800 if row["kind"] == "image_build" else 120)
            <= instant
        ):
            raise LeaseLost()
        return row, instant

    def heartbeat(self, run_id, token):
        with self.engine.begin() as conn:
            row, instant = self._owned(conn, run_id, token)
            expiry = min(
                instant + timedelta(seconds=60),
                row["started_at"]
                + timedelta(seconds=1800 if row["kind"] == "image_build" else 120),
            )
            if expiry <= instant:
                raise LeaseLost()
            conn.execute(runs.update().where(runs.c.id == run_id).values(lease_expires_at=expiry))

    def progress(self, run_id, token, step, message, sha=None):
        with self.engine.begin() as conn:
            row, _ = self._owned(conn, run_id, token)
            values = {"current_step": step}
            if sha:
                if row["resolved_commit_sha"] and row["resolved_commit_sha"] != sha:
                    raise ValueError("Pinned commit must not change")
                values["resolved_commit_sha"] = sha
            conn.execute(runs.update().where(runs.c.id == run_id).values(**values))
            append_event(
                conn, run_id, "progress", message, step, {"commit_sha": sha} if sha else {}
            )

    def finish(self, run_id, token, *, result=None, error=None, retry=False):
        with self.engine.begin() as conn:
            row, instant = self._owned(conn, run_id, token)
            retry = bool(error and retry and row["attempt"] < 3)
            state = "queued" if retry else ("failed" if error else "succeeded")
            values = dict(
                status=state,
                lease_token=None,
                lease_expires_at=None,
                result=result,
                error=None if retry else error,
                finished_at=None if retry else instant,
            )
            if retry:
                values["available_at"] = instant + timedelta(seconds=5 * 2 ** (row["attempt"] - 1))
            conn.execute(runs.update().where(runs.c.id == run_id).values(**values))
            append_event(
                conn,
                run_id,
                "retry_scheduled" if retry else state,
                "잠시 후 다시 확인합니다."
                if retry
                else (
                    error["message"]
                    if error
                    else (
                        "이미지 빌드를 완료했습니다."
                        if row["kind"] == "image_build"
                        else "소스 확인을 완료했습니다."
                    )
                ),
                row["current_step"],
                error or {},
            )
