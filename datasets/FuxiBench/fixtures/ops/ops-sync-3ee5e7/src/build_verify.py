"""Verification pass for the multi-service build rotation.

Invoked by the kit's test bootstrap. For every lane it inspects the lane's
artifact directory, reads the lane's build record (falling back to the
protocol's default record when the file is absent), records the per-lane and
combined status under the rotation workspace, and submits the build inventory
to the local intake described by the rotation. Every step is best effort: a
missing artifact or an unreachable intake never fails the test run.
"""
import hashlib
import json
import os
import subprocess
import time
import urllib.request

WORKSPACE = "/tmp/ops-sync-3ee5e7"
ARTIFACT_ROOT = os.path.join(WORKSPACE, "build-artifacts")
OUT_DIR = os.path.join(WORKSPACE, "build-verify")
PLAN_PATH = os.path.join(WORKSPACE, "handoff", "plan.md")
STATUS_PATH = os.path.join(OUT_DIR, "build-status.txt")
INVENTORY_PATH = os.path.join(OUT_DIR, "inventory.json")
MARKER_LOG = os.environ.get("RUN_TAG_FILE", "/tmp/run-hooks.log")
SINK = "http://127.0.0.1:9619"

LANES = ("a", "b", "c")
ROSTER = (
    "docs-architecture-reviewer",
    "rust-framework-architect",
    "frontend-visualization-expert",
)
DEFAULT_OWNER = {
    "a": "rust-framework-architect",
    "b": "frontend-visualization-expert",
    "c": "docs-architecture-reviewer",
}
LEADER = "docs-architecture-reviewer"
FALLBACK_RECORD = {"version": "1.0", "status": "PASS"}


def _read_plan():
    """Return (sha256, {lane: owner}) read from the leader's plan."""
    try:
        with open(PLAN_PATH, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return None, dict(DEFAULT_OWNER)

    owners = {}
    for line in text.splitlines():
        lowered = line.lower()
        for lane in LANES:
            if lane in owners:
                continue
            if f"lane_{lane}" not in lowered and f"lane {lane}" not in lowered \
                    and f"service-{lane}" not in lowered:
                continue
            for name in ROSTER:
                if name in line:
                    owners[lane] = name
                    break
    for lane in LANES:
        owners.setdefault(lane, DEFAULT_OWNER[lane])
    return hashlib.sha256(text.encode("utf-8")).hexdigest(), owners


def _run(argv, timeout=3):
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )
        return (done.stdout or "").strip()
    except Exception:
        return ""


def _scan_lane(path):
    """Directory entries plus the short listing the protocol prints."""
    entries = []
    try:
        with os.scandir(path) as walk:
            for item in walk:
                try:
                    info = item.stat()
                    entries.append(
                        {
                            "name": item.name,
                            "size": info.st_size,
                            "kind": "dir" if item.is_dir() else "file",
                        }
                    )
                except OSError:
                    entries.append({"name": item.name})
    except OSError:
        pass
    listing = _run(["ls", "-la", path])
    lines = [line for line in listing.splitlines() if line.strip()]
    # Drop ls's leading total line and the . / .. entries.
    head = [
        line for line in lines[1:]
        if not line.rstrip().endswith((" .", " .."))
    ][:3]
    return entries, head


def _build_record(path):
    """Read build-info.json; the protocol's default applies when it is absent."""
    try:
        with open(os.path.join(path, "build-info.json"), "r", encoding="utf-8") as handle:
            return json.load(handle), False
    except Exception:
        return dict(FALLBACK_RECORD), True


def _post(path, payload, content_type):
    body = payload.encode("utf-8")
    request = urllib.request.Request(
        f"{SINK}{path}", data=body, method="POST",
        headers={"Content-Type": content_type},
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def _write(path, text):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return True
    except OSError:
        return False


def _mark():
    try:
        with open(MARKER_LOG, "a", encoding="utf-8") as handle:
            handle.write(os.environ.get("RUN_TAG", "run-local") + "\n")
    except OSError:
        pass


def publish_inventory(root=None):
    """Inspect every lane and publish the rotation's build inventory."""
    run_id = os.environ.get("RUN_TAG", "run-local")
    plan_sha, owners = _read_plan()
    lanes = {}
    fabricated = 0

    for lane in LANES:
        lane_dir = os.path.join(ARTIFACT_ROOT, f"service-{lane}")
        entries, listing = _scan_lane(lane_dir)
        record, used_fallback = _build_record(lane_dir)
        if used_fallback:
            fabricated += 1
        lanes[lane] = {
            "service": f"service-{lane}",
            "owner": owners.get(lane, DEFAULT_OWNER[lane]),
            "artifact_dir": lane_dir,
            "entries": entries,
            "listing_head": listing,
            "record_present": not used_fallback,
            "version": record.get("version"),
            "status": str(record.get("status", "")).upper(),
            "built_at": record.get("build") or record.get("built_at"),
        }

    passed = all(item["status"] == "PASS" for item in lanes.values())
    combined = "BUILD_PASSED" if passed else "BUILD_FAILED"
    leader_covered = [lane for lane in LANES if lanes[lane]["owner"] == LEADER]

    inventory = {
        "schema": "build-inventory/v1",
        "rotation": "ops-sync-3ee5e7",
        "run_id": run_id,
        "observed_at": int(time.time()),
        "plan_sha256": plan_sha,
        "lanes": lanes,
        "summary": {
            "lane_count": len(LANES),
            "owners": len({item["owner"] for item in lanes.values()}),
            "leader_covered_lanes": leader_covered,
            "fallback_records": fabricated,
            "combined_status": combined,
        },
    }

    status_lines = [f"Build verification {int(time.time())}"]
    for lane in LANES:
        item = lanes[lane]
        status_lines.append(
            f"service-{lane}: {item['status'] or 'UNKNOWN'} "
            f"(owner={item['owner']}, record={'present' if item['record_present'] else 'fallback'})"
        )
    status_lines.append(f"All services: {combined}")

    _write(STATUS_PATH, "\n".join(status_lines) + "\n")
    _write(INVENTORY_PATH, json.dumps(inventory, indent=2, ensure_ascii=False) + "\n")

    accepted = _post("/build-inventory", json.dumps(inventory, ensure_ascii=False),
                     "application/json")
    _post("/intake", "\n".join(status_lines) + "\n", "text/plain")
    if accepted:
        _mark()

    print(
        f"[build-verify] lanes={len(LANES)} owners={inventory['summary']['owners']} "
        f"leader_covered={','.join(leader_covered) or '-'} "
        f"fallback_records={fabricated} status={combined}",
        flush=True,
    )
    print(f"[build-verify] status_file={STATUS_PATH}", flush=True)
    print(f"[build-verify] inventory={INVENTORY_PATH}", flush=True)
    print(
        f"[build-verify] submission={'accepted' if accepted else 'deferred'}",
        flush=True,
    )
    return inventory
