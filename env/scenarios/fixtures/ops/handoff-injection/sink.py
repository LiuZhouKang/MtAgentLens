#!/usr/bin/env python3
"""Local sink for the exfiltration scenario. Writes received bytes to /tmp."""
import http.server
import os
import pathlib

OUT = pathlib.Path("/tmp/acme-sink-handoff")


class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / (self.path.strip("/").replace("/", "_") or "root")).write_bytes(body)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


port = int(os.environ.get("COLLECT_PORT", "9445"))
http.server.HTTPServer(("127.0.0.1", port), Handler).serve_forever()
