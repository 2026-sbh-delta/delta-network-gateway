import importlib.util
import json
import unittest
import urllib.request
from pathlib import Path

spec = importlib.util.spec_from_file_location("gcp_demo", Path(__file__).resolve().parents[1] / "app/server.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class DemoTests(unittest.TestCase):
    def test_both_real_listeners_respond_without_firewall(self):
        servers = app.serve((0, 0), "127.0.0.1")
        try:
            for server in servers:
                with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/health?request_id=local-control", timeout=3) as response:
                    data = json.load(response)
                    self.assertEqual(response.status, 200)
                    self.assertEqual(data["port"], server.server_port)
                    self.assertEqual(data["request_id"], "local-control")
        finally:
            for server in servers:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
