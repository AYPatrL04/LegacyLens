from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import unittest
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from legacylens.server import LegacyLensRequestHandler


class ServerStreamTests(unittest.TestCase):
    def test_analyze_stream_returns_metadata_delta_and_done(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), LegacyLensRequestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            payload = json.dumps(
                {
                    "code": "def load(path):\n    return open(path).read()\n",
                    "fileName": "sample.py",
                    "language": "python",
                    "cursorLine": 2,
                    "contextScope": "none",
                    "useLlm": False,
                }
            ).encode("utf-8")
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/analyze/stream",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                events = [json.loads(line.decode("utf-8")) for line in response if line.strip()]
        finally:
            server.shutdown()
            server.server_close()

        event_types = [event["type"] for event in events]
        self.assertIn("metadata", event_types)
        self.assertIn("delta", event_types)
        self.assertEqual(event_types[-1], "done")


if __name__ == "__main__":
    unittest.main()
