"""공개 주소(CloudFront 뒤)로 들어온 요청의 로그인 세션. DAST 작업실과 같은 방식이다.

공개 주소는 ORCH_PUBLIC_ORIGIN 하나만 받고, ORCH_ADMIN_PASSWORD로 로그인해야 한다.
"""

from collections import deque
import hashlib
import hmac
from html import escape
import os
import secrets
import time
from urllib.parse import urlsplit

COOKIE = "__Host-delta_session"
SESSION_SECONDS = 12 * 3600
# 비밀번호를 연속으로 틀리면 잠시 막는다. IP별 제한은 앞단 WAF가 맡는다.
MAX_FAILURES = 10
LOCK_SECONDS = 300


class Access:
    def __init__(self, public_origin: str | None = None, password: str | None = None,
                 secret: str | None = None):
        self.public_origin = public_origin.rstrip("/") if public_origin else None
        self.public_host = None
        if self.public_origin:
            parts = urlsplit(self.public_origin)
            if parts.scheme != "https" or not parts.netloc or parts.path:
                raise ValueError("ORCH_PUBLIC_ORIGIN은 경로 없는 https 주소여야 합니다.")
            if not password or len(password) < 16:
                raise ValueError("공개 주소를 쓰려면 16자 이상의 ORCH_ADMIN_PASSWORD가 필요합니다.")
            self.public_host = parts.netloc
        self._password = (password or "").encode()
        # 비밀값이 없으면 프로세스마다 새로 만든다(재시작하면 다시 로그인).
        self._secret = (secret or secrets.token_urlsafe(32)).encode()
        self._failures: deque[float] = deque()

    @classmethod
    def from_env(cls) -> "Access":
        return cls(os.environ.get("ORCH_PUBLIC_ORIGIN") or None,
                   os.environ.get("ORCH_ADMIN_PASSWORD") or None,
                   os.environ.get("ORCH_SESSION_SECRET") or None)

    def is_public(self, host: str) -> bool:
        return self.public_host is not None and host == self.public_host

    def _sign(self, body: str) -> str:
        return hmac.new(self._secret, body.encode(), hashlib.sha256).hexdigest()

    def issue(self) -> str:
        body = f"{int(time.time()) + SESSION_SECONDS}.{secrets.token_urlsafe(9)}"
        return f"{body}.{self._sign(body)}"

    def valid(self, value: str | None) -> bool:
        if not value or value.count(".") != 2:
            return False
        body, signature = value.rsplit(".", 1)
        if not hmac.compare_digest(signature, self._sign(body)):
            return False
        expires = body.split(".", 1)[0]
        return expires.isdigit() and int(expires) > time.time()

    def locked(self) -> bool:
        cutoff = time.time() - LOCK_SECONDS
        while self._failures and self._failures[0] < cutoff:
            self._failures.popleft()
        return len(self._failures) >= MAX_FAILURES

    def check_password(self, given: str) -> bool:
        ok = bool(self._password) and hmac.compare_digest(given.encode(), self._password)
        if not ok:
            self._failures.append(time.time())
        return ok

    @staticmethod
    def cookie(value: str, max_age: int = SESSION_SECONDS) -> str:
        return f"{COOKIE}={value}; Max-Age={max_age}; Path=/; Secure; HttpOnly; SameSite=Strict"


def login_page(message: str = "") -> str:
    note = f'<p class="err" role="alert">{escape(message)}</p>' if message else ""
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Delta 플랫폼 로그인</title>
<style>
:root{{color-scheme:light dark;--bg:#0f1418;--panel:#171e24;--fg:#e6edf1;--muted:#93a1ab;--line:#2a343c;--accent:#4cc4b0;--err:#f07a8a}}
@media (prefers-color-scheme: light){{:root{{--bg:#f4f6f7;--panel:#fff;--fg:#1a2228;--muted:#5b6770;--line:#d3d9dd;--accent:#0d7a6b;--err:#b3261e}}}}
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,"Apple SD Gothic Neo",sans-serif;padding:0 16px}}
form{{width:min(360px,100%);background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:28px;display:grid;gap:14px}}
h1{{font-size:19px;margin:0}} p{{margin:0;color:var(--muted)}} .err{{color:var(--err)}}
label{{display:grid;gap:6px;font-weight:600}}
input{{font:inherit;padding:10px 12px;border-radius:6px;border:1px solid var(--line);background:var(--bg);color:var(--fg)}}
input:focus-visible,button:focus-visible{{outline:2px solid var(--accent);outline-offset:2px}}
button{{font:inherit;font-weight:700;padding:10px;border:0;border-radius:6px;background:var(--accent);color:#0b1114;cursor:pointer}}
</style></head><body>
<form method="post" action="/login">
<h1>◈ Delta 플랫폼</h1><p>관리자 비밀번호로 로그인하세요.</p>{note}
<label for="password">비밀번호<input id="password" name="password" type="password" autocomplete="current-password" required autofocus></label>
<button type="submit">로그인</button>
</form></body></html>"""
