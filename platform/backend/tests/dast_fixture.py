"""Real workflow with ECS and scanner HTTP boundaries replaced for browser tests."""

import time

from test_dast import Dast, runner
from test_sandbox import Ecs
from test_security import Sast


class BrowserDast:
    def execute(self, claim, store, progress):
        name = store.service(claim["service_id"])["name"]
        provider = Ecs()
        remote = Dast("skipped" if name.startswith("DAST 없음") else "done", provider=provider)
        if name.startswith("DAST 중단"):
            remote.mode = "running"
            remote.on_start = lambda: time.sleep(3)
        return runner(provider, remote, Sast()).execute(claim, store, progress)
