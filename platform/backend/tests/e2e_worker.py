"""외부 HTTP fixture를 주입한 브라우저 검증 전용 Worker."""

import time

import httpx
from build_fixture import BrowserBuilder
from dast_fixture import BrowserDast
from github_fixture import handler
from sandbox_fixture import BrowserSandbox
from security_fixture import BrowserSecurity
from support import settings

from delta_platform.adapters.github import GitHubReader
from delta_platform.core.database import engine_for
from delta_platform.core.store import Store
from delta_platform.worker.main import process_one

if __name__ == "__main__":
    engine = engine_for(settings())
    reader = GitHubReader(httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        while True:
            if not process_one(
                Store(engine),
                reader,
                BrowserBuilder(),
                BrowserSandbox(),
                BrowserSecurity(),
                BrowserDast(),
            ):
                time.sleep(0.2)
    finally:
        reader.client.close()
        engine.dispose()
