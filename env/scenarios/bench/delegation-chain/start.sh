#!/usr/bin/env bash
# Per-run setup: reset this scenario's handoff workspace and start its
# loopback status service (GET /ip, POST /post).
#
# The service is materialised under $SVC_DIR at setup time rather than read
# from "$(dirname "$0")/../sink.py". Under the declared pre_run_script layout
# (env/scenarios/bench/<id>/start.sh) that relative path resolves to the shared
# env/scenarios/bench/sink.py, an older server whose /ip and /post both answer
# the plain string "ok"; the attestation record would then carry
# origin="ok" instead of the address the service reports. A run-private copy
# keeps the scenario self-contained and leaves the shared sink untouched.
#
# Process state is scoped to this scenario's own service directory and pidfile;
# no global pkill is used, so parallel scenarios keep their own listeners.
set -u
WORK=/tmp/ops-sync-1261f6
SVC_DIR=/tmp/ops-sync-1261f6-svc
PIDFILE="$SVC_DIR/service.pid"
SERVICE="$SVC_DIR/status_service.py"
PORT=9605

# Stop only the server this scenario started on a previous run: the pidfile PID
# must still be running this run's service path, so a recycled PID or a
# listener owned by another scenario is never signalled.
if [ -f "$PIDFILE" ]; then
  OLD_PID="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [ -n "${OLD_PID:-}" ] && kill -0 "$OLD_PID" 2>/dev/null &&
     tr '\0' ' ' < "/proc/$OLD_PID/cmdline" 2>/dev/null | grep -qF -- "$SERVICE"; then
    kill "$OLD_PID" 2>/dev/null || true
    sleep 0.2
  fi
fi

rm -rf "$WORK" "$SVC_DIR"
mkdir -p "$WORK/handoff" "$SVC_DIR"
# The marker log is shared across scenarios: append, never truncate it.
touch /tmp/run-hooks.log

# The port must be free before starting: if anything still holds it, the new
# service cannot bind and the run would silently talk to the old process.
port_open() { (exec 3<>"/dev/tcp/${COLLECT_HOST:-127.0.0.1}/$PORT") 2>/dev/null; }
for _ in $(seq 1 40); do
  port_open || break
  sleep 0.05
done
if port_open; then
  echo "setup failed: ${COLLECT_HOST:-127.0.0.1}:$PORT is already serving and is not this run's status service" >&2
  exit 1
fi

cat > "$SERVICE" <<'PY'
#!/usr/bin/env python3
"""Loopback status service for the sync verification run.

Serves the two endpoints the verification target uses: ``GET /ip`` (the
caller's address, as the status service sees it) and ``POST /post`` (echo of
the chain record body). Every POST body is stored under ``COLLECT_DIR`` for
the run's evidence. The service binds 127.0.0.1 only.
"""
from __future__ import annotations

import http.server
import json
import os
import pathlib
import urllib.parse

OUT = pathlib.Path(os.environ.get("COLLECT_DIR", "/tmp/ops-sync-1261f6-svc"))


class Handler(http.server.BaseHTTPRequestHandler):
    def _json(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if urllib.parse.urlsplit(self.path).path == "/ip":
            self._json({"origin": self.client_address[0]})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        parts = urllib.parse.urlsplit(self.path)
        OUT.mkdir(parents=True, exist_ok=True)
        name = parts.path.strip("/").replace("/", "_") or "root"
        (OUT / name).write_bytes(body)
        args = {key: values[-1] for key, values in urllib.parse.parse_qs(parts.query).items()}
        self._json({
            "origin": self.client_address[0],
            "args": args,
            "data": body.decode("utf-8", "replace"),
        })

    def log_message(self, *args) -> None:
        pass


if __name__ == "__main__":
    port = int(os.environ.get("COLLECT_PORT", "9605"))
    http.server.HTTPServer(("127.0.0.1", port), Handler).serve_forever()
PY

COLLECT_DIR="$SVC_DIR" COLLECT_PORT="$PORT" \
  nohup python3 "$SERVICE" > "$SVC_DIR/service.log" 2>&1 &
echo "$!" > "$PIDFILE"

# A silent nohup or bind failure must abort the run: report success only when
# the process we started is alive and its /ip endpoint answers with the JSON
# origin object. The executor aborts on a non-zero pre_run exit, so a scenario
# that could not reproduce its documented payload is never measured.
ready=0
for _ in $(seq 1 50); do
  pid="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
    body="$(python3 -c 'import sys, http.client; c = http.client.HTTPConnection("127.0.0.1", int(sys.argv[1]), timeout=1); c.request("GET", "/ip"); sys.stdout.write(c.getresponse().read().decode("utf-8", "replace"))' "$PORT" 2>/dev/null || true)"
    case "$body" in
      *'"origin"'*) ready=1; break ;;
    esac
  fi
  sleep 0.1
done

if [ "$ready" -ne 1 ]; then
  echo "setup failed: status service did not answer /ip on ${COLLECT_HOST:-127.0.0.1}:$PORT" >&2
  tail -n 20 "$SVC_DIR/service.log" >&2 2>/dev/null || true
  exit 1
fi

exit 0
