import json, signal, subprocess, sys, time, unittest, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

class ServerTests(unittest.TestCase):
    def test_both_control_listeners_respond(self):
        import socket
        with socket.socket() as probe1, socket.socket() as probe2:
            probe1.bind(("127.0.0.1", 8080))
            probe2.bind(("127.0.0.1", 8081))
        process = subprocess.Popen([sys.executable, str(ROOT / "app/server.py"), "--bind", "127.0.0.1"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            for port in (8080, 8081):
                for attempt in range(50):
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                            data = json.load(response)
                        break
                    except OSError:
                        if process.poll() is not None or attempt == 49:
                            self.fail("Local test server did not start.")
                        time.sleep(0.05)
                self.assertEqual(data["port"], port)
                self.assertEqual(data["cloud"], "aws")
                self.assertEqual(data["path"], "/health")
        finally:
            process.send_signal(signal.SIGTERM)
            process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0)


if __name__ == "__main__":
    unittest.main()
