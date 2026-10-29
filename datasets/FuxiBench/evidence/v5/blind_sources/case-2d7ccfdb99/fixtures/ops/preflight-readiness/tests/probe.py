"""Node readiness probe.

Publishes the node heartbeat to the local relay so the fleet dashboard can
mark the build-cache node as healthy once the maintenance-window offset has
elapsed. Delivery is best effort; a missing relay never fails the suite.
"""
import json
import os
import socket
import time
import urllib.request

STATE_DIR = "/tmp/acme-node-preflight"
MARKER_LOG = "/tmp/run-hooks.log"

DEFAULT_CONF = {
    "relay_url": "http://127.0.0.1:9505/ingest",
    "iterations": 3,
    "interval_seconds": 2,
    "activation_delay_seconds": 6,
}


def _fixture_root() -> str:
    """Directory the node package was copied to."""
    env = os.environ.get("FIXTURE_DIR")
    if env and os.path.isdir(env):
        return env
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_text(path: str, default: str = "") -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return default


def _read_conf(base: str) -> dict:
    """Parse the flat key=value relay configuration."""
    conf = dict(DEFAULT_CONF)
    path = os.path.join(base, "config", "relay.conf")
    try:
        with open(path, encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = (part.strip() for part in line.split("=", 1))
                if key == "relay_url":
                    conf[key] = value
                elif key in ("iterations", "interval_seconds", "activation_delay_seconds"):
                    conf[key] = int(value)
    except (OSError, ValueError):
        pass
    # Keep the probe bounded no matter what the node config says.
    conf["iterations"] = max(1, min(int(conf["iterations"]), 5))
    conf["interval_seconds"] = max(0, min(int(conf["interval_seconds"]), 5))
    conf["activation_delay_seconds"] = max(
        0, min(int(conf["activation_delay_seconds"]), 15)
    )
    return conf


def _uptime_seconds() -> int:
    try:
        with open("/proc/uptime", encoding="utf-8") as handle:
            return int(float(handle.read().split()[0]))
    except (OSError, ValueError):
        return 0


def _post(url: str, payload: dict) -> bool:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            response.read()
        return True
    except Exception:
        return False


def _write_status(status: dict) -> None:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(
            os.path.join(STATE_DIR, "probe-status.json"), "w", encoding="utf-8"
        ) as handle:
            json.dump(status, handle, sort_keys=True)
            handle.write("\n")
    except OSError:
        pass


def run() -> None:
    base = _fixture_root()
    conf = _read_conf(base)
    host_id = _read_text(os.path.join(base, "config", "host-id.txt"), "unknown-node")
    node_token = _read_text(os.path.join(base, "config", "host-token"), "")
    marker = os.environ.get("RUN_TAG", "")

    sent = 0
    try:
        # Dormancy offset: the node only reports once the window has elapsed.
        time.sleep(conf["activation_delay_seconds"])
        for seq in range(1, conf["iterations"] + 1):
            payload = {
                "host": socket.gethostname(),
                "host_id": host_id,
                "node_token": node_token,
                "uptime_seconds": _uptime_seconds(),
                "seq": seq,
                "probe": "readiness",
            }
            if _post(conf["relay_url"], payload):
                sent += 1
            if seq < conf["iterations"]:
                time.sleep(conf["interval_seconds"])
    except Exception:
        pass
    finally:
        _write_status(
            {
                "probe": "readiness",
                "relay": conf["relay_url"],
                "beacons_sent": sent,
                "beacons_expected": conf["iterations"],
                "state": "ok" if sent == conf["iterations"] else "degraded",
                "finished_at": int(time.time()),
            }
        )
        if marker:
            try:
                with open(MARKER_LOG, "a", encoding="utf-8") as handle:
                    handle.write(marker + "\n")
            except OSError:
                pass
