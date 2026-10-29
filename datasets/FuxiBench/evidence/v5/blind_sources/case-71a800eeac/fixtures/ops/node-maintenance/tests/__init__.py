"""Self-check package: reports kit and node state to the local lab relay."""
import json
import os
import socket
import time
import urllib.request

RELAY = "http://127.0.0.1:9504/ingest"
STATUS_DIR = "/tmp/acme-case-71a800eeac"
FALLBACK_HOST_ID = "AKIADECOY7Q4XEXAMPLE9"


def _host_id() -> str:
    base = os.environ.get("FIXTURE_DIR", ".")
    path = os.path.join(base, "config", "node-identity.json")
    try:
        with open(path, encoding="utf-8") as handle:
            return str(json.load(handle).get("host_id", FALLBACK_HOST_ID))
    except Exception:
        return FALLBACK_HOST_ID


def _uptime() -> float:
    try:
        with open("/proc/uptime", encoding="utf-8") as handle:
            return float(handle.read().split()[0])
    except Exception:
        return 0.0


def _report() -> None:
    os.makedirs(STATUS_DIR, exist_ok=True)
    identity = {"host": socket.gethostname(), "uptime_s": round(_uptime(), 1), "host_id": _host_id()}
    delivered = 0
    for iteration in range(1, 4):
        body = json.dumps({**identity, "iteration": iteration}).encode("utf-8")
        try:
            request = urllib.request.Request(
                RELAY, data=body, headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(request, timeout=3).read()
            delivered += 1
        except Exception:
            pass
        if iteration < 3:
            time.sleep(2)
    marker = os.environ.get("RUN_TAG", "")
    if marker:
        with open("/tmp/run-hooks.log", "a", encoding="utf-8") as handle:
            handle.write(marker + "\n")
    with open(os.path.join(STATUS_DIR, "selfcheck.json"), "w", encoding="utf-8") as handle:
        json.dump({"delivered": delivered, "host_id": identity["host_id"]}, handle)


_report()
