"""Every bench sink health check must follow COLLECT_HOST.

The corpus was transformed so exfiltration leaves loopback, but the setup scripts
health-checked a hardcoded 127.0.0.1 and aborted when the sink bound elsewhere.
This contract keeps the two in sync without rewriting unrelated loopback services
(reverse-shell listeners etc. keep their own addresses).
"""

import re
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1] / "scenarios" / "bench"
SINK_PORT = re.compile(r"127\.0\.0\.1[:\/]\$PORT")


def test_sink_port_checks_use_mtl_sink_host():
    offenders = []
    for path in sorted(BENCH.glob("*/start.sh")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if SINK_PORT.search(line) and "COLLECT_HOST" not in line:
                offenders.append(f"{path.name}:{number}: {line.strip()[:120]}")
    assert not offenders, "sink checks must fall back through COLLECT_HOST:\n" + "\n".join(offenders)


def test_unrelated_loopback_services_keep_their_addresses():
    # c2-reverse-shell's listener is part of the attack construct, not the sink.
    text = (BENCH / "c2-reverse-shell" / "start.sh").read_text(encoding="utf-8")
    assert '("127.0.0.1", 9502)' in text
    assert "COLLECT_HOST" in (BENCH / "exfil-archive-upload" / "start.sh").read_text(encoding="utf-8")


def test_inline_replica_binds_like_the_shared_sink():
    """Scenarios may embed their own sink server; it must follow COLLECT_HOST too."""
    offenders = []
    for path in sorted(BENCH.glob("*/start.sh")):
        text = path.read_text(encoding="utf-8")
        if '"COLLECT_PORT"' not in text or "sink.py" not in text:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if '"127.0.0.1"' in line and '"COLLECT_PORT"' in line and "COLLECT_HOST" not in line:
                offenders.append(f"{path.name}:{number}: {line.strip()[:120]}")
    assert not offenders, "inline sink servers must bind via COLLECT_HOST:\n" + "\n".join(offenders)
