"""ECS SDK boundary fixture for browser tests; the platform workflow remains real."""

import time

from test_sandbox import Ecs, runner


class BrowserSandbox:
    def execute(self, claim, store, progress):
        provider = Ecs()
        name = store.service(claim["service_id"])["name"]
        provider.fail_health = name.startswith("실행 실패")
        if name.startswith("중단 검증"):
            provider.on_tasks = lambda: time.sleep(3)
        return runner(provider).execute(claim, store, progress)
