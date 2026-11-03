"""Real platform workflow with only the SAST HTTP boundary replaced for browser tests."""

from test_security import Sast, runner


class BrowserSecurity:
    def execute(self, claim, store, progress):
        name = store.service(claim["service_id"])["name"]
        return runner(Sast(bad_commit=name.startswith("검사 불일치"))).execute(
            claim, store, progress
        )
