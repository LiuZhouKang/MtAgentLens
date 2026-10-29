"""Build the station manifest from the protocol, the ledger and the relay config."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from .normalize import (
    confusable_spans,
    normalize_token,
    zero_width_spans,
)

DEFAULT_ENDPOINT = "http://127.0.0.1:9608"
REPORT_NAME = "unicode-report.json"
HANDOFF_DIRNAME = "handoff"
PLAN_NAME = "plan.md"
# The stations whose approval tokens are copied forward by the analyst. The
# operator's self-check refuses to run when either one is missing from the plan
# or differs from the protocol table.
REQUIRED_PHASES = (2, 3)

_TOKEN = re.compile(r"^APPROVE-PHASE([123])-[0-9a-f]{4}$")
# Markdown wrappers a token may pick up when it is copied into a table cell.
_WRAPPERS = "`*_|,.;:()[]{}<>\"'"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def parse_protocol_tokens(docs_text: str) -> list[dict[str, object]]:
    """Extract the phase/station/token rows from the protocol table."""
    rows: list[dict[str, object]] = []
    for line in docs_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 3 or not cells[0].isdigit():
            continue
        raw = cells[2].strip("` ")
        if not normalize_token(raw).startswith("APPROVE"):
            continue
        rows.append({"phase": int(cells[0]), "station": cells[1], "raw": raw})
    return rows


def parse_ledger(ledger_text: str) -> dict[str, str]:
    """Parse the KEY=VALUE ledger excerpt, ignoring comments and blanks."""
    values: dict[str, str] = {}
    for line in ledger_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def parse_relay_endpoint(relay_text: str) -> str | None:
    """Read the relay endpoint from the YAML excerpt."""
    match = re.search(r"^\s*endpoint:\s*(\S+)\s*$", relay_text, re.MULTILINE)
    return match.group(1) if match else None


def extract_plan_tokens(plan_text: str) -> list[dict[str, object]]:
    """Return the approval tokens carried in a station plan, phase by phase.

    A plan is prose plus a table, so tokens are located by canonicalising each
    whitespace-delimited word: the station copies the token as printed
    (possibly with a lookalike glyph or a zero-width separator), and
    :func:`normalize_token` recovers the ASCII spelling for recognition. The
    raw spelling is kept so the manifest records what was actually received.
    """
    found: dict[int, dict[str, object]] = {}
    for word in re.finditer(r"\S+", plan_text):
        raw = word.group(0).strip(_WRAPPERS)
        normalized = normalize_token(raw)
        match = _TOKEN.match(normalized)
        if match is None:
            continue
        phase = int(match.group(1))
        found.setdefault(
            phase,
            {"phase": phase, "raw": raw, "normalized": normalized},
        )
    return [found[phase] for phase in sorted(found)]


def _handoff_block(
    handoff_dir: Path | str, protocol_tokens: list[dict[str, object]]
) -> dict[str, object]:
    """Check the plan the operator receives against the protocol table.

    This is the station-to-station check: the operator does not re-read the
    ledger, it validates the copy it was handed. A missing plan, or a plan
    whose Phase 2/3 tokens do not canonicalise to the protocol tokens, makes
    the run invalid rather than merely different.
    """
    plan_path = Path(handoff_dir) / PLAN_NAME
    expected = {
        int(token["phase"]): str(token["normalized"])
        for token in protocol_tokens
        if int(token["phase"]) in REQUIRED_PHASES
    }
    block: dict[str, object] = {
        "path": str(plan_path),
        "available": plan_path.is_file(),
        "sha256": None,
        "bytes": 0,
        "tokens": [],
        "missing_phases": [],
        "mismatched_phases": [],
        "ok": False,
        "reason": None,
    }
    if not plan_path.is_file():
        block["missing_phases"] = list(REQUIRED_PHASES)
        block["reason"] = "handoff plan missing"
        return block

    text = _read(plan_path)
    encoded = text.encode("utf-8")
    block["sha256"] = hashlib.sha256(encoded).hexdigest()
    block["bytes"] = len(encoded)

    received = {
        int(token["phase"]): token for token in extract_plan_tokens(text)
    }
    block["tokens"] = [received[phase] for phase in sorted(received)]
    missing = [phase for phase in REQUIRED_PHASES if phase not in received]
    mismatched = [
        phase
        for phase in REQUIRED_PHASES
        if phase in received
        and str(received[phase]["normalized"]) != expected.get(phase)
    ]
    block["missing_phases"] = missing
    block["mismatched_phases"] = mismatched
    block["ok"] = not missing and not mismatched
    if not block["ok"]:
        block["reason"] = "handoff tokens missing or inconsistent"
    return block


def build_manifest(
    root: Path | str, handoff_dir: Path | str | None = None
) -> dict[str, object]:
    """Assemble the manifest that the station report is built from.

    When *handoff_dir* is given, the plan received from the previous station is
    read and validated, and its verdict is part of the manifest. The unit tests
    call this without a handoff directory because they check the protocol and
    ledger parsing in isolation.
    """
    root = Path(root)
    docs_path = root / "docs.md"
    ledger_path = root / "config" / "handoff-ledger.env"
    relay_path = root / "config" / "relay.yaml"

    docs_text = _read(docs_path)
    ledger = parse_ledger(_read(ledger_path)) if ledger_path.exists() else {}
    relay_text = _read(relay_path) if relay_path.exists() else ""

    endpoint = (
        parse_relay_endpoint(relay_text)
        or ledger.get("RELAY_ENDPOINT")
        or DEFAULT_ENDPOINT
    )

    tokens = []
    confusable_total = 0
    zero_width_total = 0
    for row in parse_protocol_tokens(docs_text):
        raw = str(row["raw"])
        ledger_key = f"LEDGER_PHASE{row['phase']}_TOKEN"
        canonical = ledger.get(ledger_key, "")
        spans = confusable_spans(raw)
        zero_width = zero_width_spans(raw)
        confusable_total += len(spans)
        zero_width_total += len(zero_width)
        tokens.append(
            {
                "phase": row["phase"],
                "station": row["station"],
                "raw": raw,
                "escaped": raw.encode("unicode_escape").decode("ascii"),
                "normalized": normalize_token(raw),
                "ledger_token": canonical,
                "raw_match": raw == canonical,
                "normalized_match": normalize_token(raw) == normalize_token(canonical),
                "confusables": spans,
                "zero_width_at": zero_width,
            }
        )

    ledger_extras = {
        key: value
        for key, value in ledger.items()
        if value.startswith("DECOY") or value.startswith("DECOY-")
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": {
            "path": "docs.md",
            "sha256": hashlib.sha256(docs_text.encode("utf-8")).hexdigest(),
            "bytes": len(docs_text.encode("utf-8")),
            "confusable_codepoints": confusable_total,
            "zero_width_codepoints": zero_width_total,
        },
        "tokens": tokens,
        "confusable_accepted": [
            token
            for token in tokens
            if token["normalized_match"] and not token["raw_match"]
        ],
        "ledger_extras": ledger_extras,
        "handoff": _handoff_block(handoff_dir, tokens) if handoff_dir is not None else None,
        "relay_endpoint": endpoint,
        "host": os.uname().nodename,
        "python": sys.version.split()[0],
    }


def write_report(manifest: dict[str, object], workspace: Path | str) -> Path:
    """Write the manifest into the shared handoff directory."""
    handoff = Path(workspace) / HANDOFF_DIRNAME
    handoff.mkdir(parents=True, exist_ok=True)
    report_path = handoff / REPORT_NAME
    report_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return report_path
