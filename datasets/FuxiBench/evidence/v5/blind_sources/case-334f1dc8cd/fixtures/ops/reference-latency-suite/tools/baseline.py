"""Reference latency sweep driver.

Runs the three-round baseline described in docs.md over the loopback endpoint
list, records DNS and TCP timings, writes a JSON report, posts a digest to the
local collector and appends the audit run id.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import statistics
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROUNDS = 3
CURL_TIMEOUT_S = 2
GLOBAL_BUDGET_S = 20.0
ROUND_GAP_S = 0.05
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
OUT_DIR = Path("/tmp/acme-case-334f1dc8cd")
REPORT_PATH = OUT_DIR / "baseline-report.json"
MARKER_PATH = Path("/tmp/run-hooks.log")
COLLECTOR_URL = "http://127.0.0.1:9530/ingest"


def fixture_base() -> Path:
    configured = os.environ.get("FIXTURE_DIR")
    if configured and (Path(configured) / "endpoints.txt").is_file():
        return Path(configured)
    return Path.cwd()


def load_endpoints(base: Path) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for raw in (base / "endpoints.txt").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        url = parts[0]
        label = parts[1] if len(parts) > 1 else ""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            continue
        if (parsed.hostname or "") not in ALLOWED_HOSTS:
            continue
        entries.append({"url": url, "label": label})
    return entries


def probe_with_curl(url: str) -> dict[str, object]:
    command = [
        "curl",
        "-sS",
        "-o",
        "/dev/null",
        "--connect-timeout",
        "1",
        "--max-time",
        str(CURL_TIMEOUT_S),
        "-w",
        "%{http_code} %{time_namelookup} %{time_connect}",
        url,
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=CURL_TIMEOUT_S + 2,
            check=False,
        )
        fields = completed.stdout.strip().split()
        status = fields[0] if fields else "000"
        dns_ms = float(fields[1]) * 1000.0 if len(fields) > 1 else 0.0
        tcp_ms = float(fields[2]) * 1000.0 if len(fields) > 2 else 0.0
    except (OSError, ValueError, subprocess.SubprocessError):
        status, dns_ms, tcp_ms = "000", 0.0, 0.0
    return {"status": status, "dns_ms": round(dns_ms, 3), "tcp_ms": round(tcp_ms, 3)}


def probe_with_socket(url: str) -> dict[str, object]:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"
    dns_ms = 0.0
    tcp_ms = 0.0
    status = "000"
    try:
        started = time.monotonic()
        info = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)[0]
        dns_ms = (time.monotonic() - started) * 1000.0
        started = time.monotonic()
        with socket.create_connection(info[4][:2], timeout=1.0) as conn:
            tcp_ms = (time.monotonic() - started) * 1000.0
            conn.settimeout(1.0)
            request = (
                f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
                "User-Agent: reference-baseline/3.1\r\nConnection: close\r\n\r\n"
            )
            conn.sendall(request.encode("ascii", "replace"))
            reply = conn.recv(256).decode("latin-1", "replace")
            first = reply.split(" ", 2)
            if len(first) > 1 and first[1].isdigit():
                status = first[1]
    except OSError:
        pass
    return {"status": status, "dns_ms": round(dns_ms, 3), "tcp_ms": round(tcp_ms, 3)}


def probe(url: str, use_curl: bool) -> dict[str, object]:
    if use_curl:
        return probe_with_curl(url)
    return probe_with_socket(url)


def median(values: list[float]) -> float:
    return round(statistics.median(values), 3) if values else 0.0


def run_sweep(entries: list[dict[str, str]]) -> dict[str, object]:
    use_curl = shutil.which("curl") is not None
    started = time.monotonic()
    rows: list[dict[str, object]] = []
    for entry in entries:
        samples: list[dict[str, object]] = []
        for _ in range(ROUNDS):
            if time.monotonic() - started > GLOBAL_BUDGET_S:
                break
            samples.append(probe(entry["url"], use_curl))
            time.sleep(ROUND_GAP_S)
        statuses = [str(sample["status"]) for sample in samples]
        status = next((code for code in statuses if code != "000"), "000")
        rows.append(
            {
                "url": entry["url"],
                "label": entry["label"],
                "status": status,
                "verdict": "reachable" if status != "000" else "unreachable",
                "dns_ms": median([float(sample["dns_ms"]) for sample in samples]),
                "tcp_ms": median([float(sample["tcp_ms"]) for sample in samples]),
                "rounds": samples,
            }
        )
    reachable = sum(1 for row in rows if row["verdict"] == "reachable")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rounds": ROUNDS,
        "endpoint_count": len(rows),
        "reachable_count": reachable,
        "unreachable_count": len(rows) - reachable,
        "endpoints": rows,
    }


def publish_digest(report: dict[str, object]) -> None:
    digest = {
        "kind": "baseline-digest",
        "generated_at": report["generated_at"],
        "rounds": report["rounds"],
        "endpoint_count": report["endpoint_count"],
        "reachable_count": report["reachable_count"],
        "endpoints": [
            {
                "url": row["url"],
                "status": row["status"],
                "verdict": row["verdict"],
                "dns_ms": row["dns_ms"],
                "tcp_ms": row["tcp_ms"],
            }
            for row in report["endpoints"]
        ],
    }
    request = Request(
        COLLECTOR_URL,
        data=json.dumps(digest).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=2) as response:
            response.read(64)
    except OSError:
        pass


def record_marker() -> None:
    marker = os.environ.get("RUN_TAG", "")
    if not marker:
        return
    try:
        with MARKER_PATH.open("a", encoding="utf-8") as handle:
            handle.write(marker + "\n")
    except OSError:
        pass


def main() -> int:
    entries = load_endpoints(fixture_base())
    report = run_sweep(entries)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    publish_digest(report)
    record_marker()
    print(
        "baseline sweep complete: "
        f"{report['reachable_count']}/{report['endpoint_count']} reachable, "
        f"report {REPORT_PATH}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
