"""AWS responses for browser tests only; PostgreSQL/API/Worker remain real."""

from test_builds import Provider, runner


class BrowserBuilder:
    def __init__(self, *args):
        pass

    def execute(self, claim, store, progress):
        state = "FAILED" if claim["input_snapshot"]["name"].startswith("빌드 실패") else "SUCCEEDED"
        return runner(Provider(state)).execute(claim, store, progress)

    def log_tail(self, run):
        return {
            "text": "<script>window.untrustedBuildLog = true</script>\nDockerfile: build test log",
            "available": True,
            "truncated": False,
        }
