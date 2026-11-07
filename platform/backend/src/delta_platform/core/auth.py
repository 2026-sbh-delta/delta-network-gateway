import hashlib
import hmac
import secrets
import threading
from datetime import timedelta

import sqlalchemy as sa
from argon2.exceptions import VerificationError
from sqlalchemy.dialects.postgresql import insert

from delta_platform.config import PASSWORD_HASHER

from .errors import Problem
from .schema import rate_limits, sessions
from .store import now

COOKIE = "delta_session"


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Auth:
    def __init__(self, engine, settings):
        self.engine, self.settings = engine, settings
        self.hash_slots = threading.BoundedSemaphore(2)

    def login(self, login_id, password, ip, old_token=None):
        if not self.hash_slots.acquire(blocking=False):
            raise Problem("login_busy", "잠시 후 다시 로그인하세요.", 429)
        try:
            return self._login(login_id, password, ip, old_token)
        finally:
            self.hash_slots.release()

    def _login(self, login_id, password, ip, old_token):
        bucket = token_hash(ip)
        outcome = None
        with self.engine.begin() as conn:
            instant = now(conn)
            conn.execute(
                insert(rate_limits)
                .values(bucket_key=bucket, window_start=instant, failures=0)
                .on_conflict_do_update(
                    index_elements=[rate_limits.c.bucket_key], set_={"bucket_key": bucket}
                )
            )
            row = (
                conn.execute(
                    sa.select(rate_limits)
                    .where(rate_limits.c.bucket_key == bucket)
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            instant = now(conn)
            if row["blocked_until"] and row["blocked_until"] > instant:
                raise Problem(
                    "login_rate_limited", "로그인 시도가 많습니다. 5분 후 다시 시도하세요.", 429
                )
            reset = row["window_start"] <= instant - timedelta(minutes=5)
            failures = 0 if reset else row["failures"]
            try:
                valid_password = PASSWORD_HASHER.verify(
                    self.settings.password_hash.get_secret_value(), password
                )
            except VerificationError:
                valid_password = False
            valid_id = hmac.compare_digest(login_id.encode(), self.settings.login_id.encode())
            if valid_id and valid_password:
                conn.execute(
                    rate_limits.update()
                    .where(rate_limits.c.bucket_key == bucket)
                    .values(failures=0, window_start=instant, blocked_until=None)
                )
                if old_token:
                    conn.execute(
                        sessions.delete().where(sessions.c.token_hash == token_hash(old_token))
                    )
                raw = secrets.token_urlsafe(32)
                csrf = secrets.token_urlsafe(32)
                expiry = instant + timedelta(hours=self.settings.session_hours)
                conn.execute(
                    sessions.insert().values(
                        token_hash=token_hash(raw),
                        csrf_token=csrf,
                        credential_version=self.settings.credential_version,
                        expires_at=expiry,
                    )
                )
                outcome = (
                    raw,
                    {"login_id": self.settings.login_id, "expires_at": expiry, "csrf_token": csrf},
                )
            else:
                conn.execute(
                    rate_limits.update()
                    .where(rate_limits.c.bucket_key == bucket)
                    .values(
                        failures=failures + 1,
                        window_start=instant if reset else row["window_start"],
                        blocked_until=instant + timedelta(minutes=5) if failures + 1 >= 5 else None,
                    )
                )
        if outcome is None:
            raise Problem("invalid_credentials", "ID 또는 비밀번호를 확인하세요.", 401)
        return outcome

    def presentation_session(self, old_token=None):
        if not self.settings.presentation_mode:
            raise Problem("not_authenticated", "로그인이 필요합니다.", 401)
        if old_token:
            try:
                return old_token, self.session(old_token)
            except Problem as exc:
                if exc.status != 401:
                    raise
        raw, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.engine.begin() as conn:
            expiry = now(conn) + timedelta(hours=self.settings.session_hours)
            conn.execute(
                sessions.insert().values(
                    token_hash=token_hash(raw),
                    csrf_token=csrf,
                    credential_version=self.settings.credential_version,
                    expires_at=expiry,
                )
            )
        return raw, {"login_id": self.settings.login_id, "expires_at": expiry, "csrf_token": csrf}

    def session(self, raw):
        if not raw or len(raw) > 128:
            raise Problem("not_authenticated", "로그인이 필요합니다.", 401)
        with self.engine.connect() as conn:
            row = (
                conn.execute(
                    sa.select(sessions).where(
                        sessions.c.token_hash == token_hash(raw),
                        sessions.c.expires_at > sa.func.clock_timestamp(),
                        sessions.c.credential_version == self.settings.credential_version,
                    )
                )
                .mappings()
                .first()
            )
        if not row:
            raise Problem("not_authenticated", "다시 로그인하세요.", 401)
        return {
            "login_id": self.settings.login_id,
            "expires_at": row["expires_at"],
            "csrf_token": row["csrf_token"],
        }

    def logout(self, raw):
        with self.engine.begin() as conn:
            conn.execute(sessions.delete().where(sessions.c.token_hash == token_hash(raw)))

    def cleanup(self):
        with self.engine.begin() as conn:
            instant = now(conn)
            conn.execute(
                sessions.delete().where(
                    sa.or_(
                        sessions.c.expires_at <= instant,
                        sessions.c.credential_version != self.settings.credential_version,
                    )
                )
            )
            conn.execute(
                rate_limits.delete().where(
                    rate_limits.c.window_start < instant - timedelta(hours=1),
                    sa.or_(
                        rate_limits.c.blocked_until.is_(None),
                        rate_limits.c.blocked_until <= instant,
                    ),
                )
            )
