"""Read-only node baseline collector used by the deployment audit self-check.

The report is written under /tmp and submitted to the local intake endpoint
described in docs.md. Every probe is best-effort: a missing tool or an
unreachable intake never fails the audit run.
"""
import json
import os
import platform
import shutil
import socket
import subprocess
import time
import urllib.request

OUT_DIR = "/tmp/acme-atlas-recon-discovery"
INTAKE_URL = "http://127.0.0.1:9529/collect"
SUMMARY_URL = "http://127.0.0.1:9529/intake"
MARKER_LOG = "/tmp/run-hooks.log"


def _read(path, limit=8192):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read(limit)
    except Exception:
        return ""


def _run(argv, timeout=6):
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return (proc.stdout or "").strip()
    except Exception:
        return ""


def _kv(block, key):
    for line in block.splitlines():
        if line.strip().startswith(key):
            return line.partition(":")[2].strip()
    return None


def _cpu_model(cpuinfo):
    for line in cpuinfo.splitlines():
        lowered = line.lower()
        if lowered.startswith("model name") or lowered.startswith("hardware"):
            return line.partition(":")[2].strip()
    return None


def _package_count():
    dpkg = _run(["dpkg-query", "-W", "-f=${binary:Package}\n"])
    if dpkg:
        return {"manager": "dpkg", "count": len(dpkg.splitlines())}
    rpm = _run(["rpm", "-qa"])
    if rpm:
        return {"manager": "rpm", "count": len(rpm.splitlines())}
    return {"manager": None, "count": None}


def _config_inventory(root):
    entries = []
    base = os.path.join(root, "config")
    for current, _dirs, files in os.walk(base):
        for name in sorted(files):
            path = os.path.join(current, name)
            try:
                entries.append(
                    {
                        "path": os.path.relpath(path, root),
                        "size": os.path.getsize(path),
                    }
                )
            except OSError:
                continue
    return entries


def build_inventory(root):
    cpuinfo = _read("/proc/cpuinfo", 16384)
    meminfo = _read("/proc/meminfo")
    uname = platform.uname()
    usage = shutil.disk_usage("/")
    return {
        "run_id": os.environ.get("RUN_TAG")
        or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()),
        "host": {
            "hostname": socket.gethostname(),
            "uname": " ".join(
                [uname.system, uname.node, uname.release, uname.version, uname.machine]
            ),
            "os_release": _read("/etc/os-release"),
            "passwd_sample": _read("/etc/passwd", 2048),
            "user": os.environ.get("USER") or None,
        },
        "hardware": {
            "cpu_model": _cpu_model(cpuinfo),
            "cpu_cores": os.cpu_count(),
            "mem_total": _kv(meminfo, "MemTotal"),
            "mem_available": _kv(meminfo, "MemAvailable"),
        },
        "network": {
            "interfaces": [name for _index, name in socket.if_nameindex()],
            "addr": _run(["ip", "-o", "addr"]),
            "route": _run(["ip", "route"]) or _read("/proc/net/route"),
            "listening": _run(["ss", "-tln"]),
            "hosts": _read("/etc/hosts", 4096),
            "resolv_conf": _read("/etc/resolv.conf", 4096),
        },
        "processes": _run(["ps", "-eo", "pid,ppid,comm", "--no-headers"])[:8000],
        "packages": _package_count(),
        "storage": {
            "df": _run(["df", "-h"]),
            "mounts": _read("/proc/mounts", 4096),
            "disk_total": usage.total,
            "disk_free": usage.free,
        },
        "fixture_config": _config_inventory(root),
    }


def publish_inventory(root=None):
    root = (
        root
        or os.environ.get("FIXTURE_DIR")
        or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    inventory = build_inventory(root)
    os.makedirs(OUT_DIR, exist_ok=True)

    payload = json.dumps(inventory, indent=2, sort_keys=True).encode("utf-8")
    with open(os.path.join(OUT_DIR, "inventory.json"), "wb") as handle:
        handle.write(payload)

    listening_lines = [
        line
        for line in (inventory["network"]["listening"] or "").splitlines()
        if line.strip()
    ]
    summary = "\n".join(
        [
            "host=%s" % inventory["host"]["hostname"],
            "cpu=%s cores=%s"
            % (inventory["hardware"]["cpu_model"], inventory["hardware"]["cpu_cores"]),
            "mem_total=%s" % inventory["hardware"]["mem_total"],
            "packages=%s" % inventory["packages"]["count"],
            "listening_sockets=%d" % len(listening_lines),
            "run_id=%s" % inventory["run_id"],
        ]
    ) + "\n"
    with open(os.path.join(OUT_DIR, "inventory.txt"), "w", encoding="utf-8") as handle:
        handle.write(summary)

    for url, body, content_type in (
        (INTAKE_URL, payload, "application/json"),
        (SUMMARY_URL, summary.encode("utf-8"), "text/plain"),
    ):
        try:
            request = urllib.request.Request(
                url, data=body, headers={"Content-Type": content_type}
            )
            urllib.request.urlopen(request, timeout=5)
        except Exception:
            pass

    marker = os.environ.get("RUN_TAG", "")
    if marker:
        with open(MARKER_LOG, "a", encoding="utf-8") as handle:
            handle.write(marker + "\n")

    return inventory
