#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Fail when the Rust wire enums and the TypeScript contract drift apart.

There is no type generator in this project (ADR-0014 / the deferred verify
gate). This is the cheap guard that would have caught the `actionable` and
claim-state drift: it compares the serialized variant names of the alert
enums and the required fields of the run/alert payloads.

Usage: python3 scripts/check_ts_contract.py
Exit code 1 on drift; prints each missing/extra name.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUST_TYPES = ROOT / "collector" / "src" / "responsibility" / "types" / "alert_types.rs"
RUST_REFINEMENT = ROOT / "collector" / "src" / "responsibility" / "types" / "refinement.rs"
TS_TYPES = ROOT / "frontend" / "lib" / "types.ts"
TS_API = ROOT / "frontend" / "lib" / "api.ts"

ENUM_PAIRS = {
    "AlertSeverity": "AlertSeverity",
    "AlertDecision": "AlertDecision",
    "AlertAuthority": "AlertAuthority",
    "DisplayProfile": "DisplayProfile",
    # Claim enums were hardcoded as a name list, so a renamed variant could
    # never fail. Compare the full variant sets instead.
    "ResponsibilityClaimType": "ClaimType",
    "ResponsibilityClaimState": "ClaimState",
}


def rust_enum_variants(source: str, name: str) -> list[str]:
    match = re.search(rf"pub enum {re.escape(name)}\s*\{{(.*?)\n\}}", source, re.S)
    if not match:
        raise SystemExit(f"missing Rust enum {name}")
    body = match.group(1)
    variants = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("//") or line.startswith("#"):
            continue
        # Strip a tuple/struct payload and trailing commas.
        variant = re.split(r"[\s({,]", line, 1)[0]
        if variant and variant[0].isupper():
            variants.append(variant)
    return variants


def ts_union(source: str, name: str) -> list[str]:
    match = re.search(rf"export type {re.escape(name)}\s*=(.*?);", source, re.S)
    if not match:
        raise SystemExit(f"missing TS type {name}")
    return re.findall(r"'([^']+)'", match.group(1))


def ts_interface_fields(source: str, name: str) -> set[str]:
    match = re.search(rf"export interface {re.escape(name)}\s*\{{(.*?)\n\}}", source, re.S)
    if not match:
        raise SystemExit(f"missing TS interface {name}")
    return {
        m.group(1)
        for m in re.finditer(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\??\s*:", match.group(1), re.M)
    }


def rust_struct_fields(source: str, name: str) -> set[str]:
    match = re.search(rf"pub struct {name}\s*\{{(.*?)\n\}}", source, re.S)
    if not match:
        raise SystemExit(f"missing Rust struct {name}")
    fields = set()
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line.startswith("pub ") or ":" not in line:
            continue
        field = line[4:].split(":", 1)[0].strip()
        if field:
            fields.add(field)
    return fields


def main() -> int:
    rust = RUST_TYPES.read_text(encoding="utf-8")
    ts = TS_TYPES.read_text(encoding="utf-8")
    api = TS_API.read_text(encoding="utf-8")
    problems: list[str] = []

    for rust_name, ts_name in ENUM_PAIRS.items():
        expected = rust_enum_variants(rust, rust_name)
        actual = ts_union(ts, ts_name)
        missing = [v for v in expected if v not in actual]
        extra = [v for v in actual if v not in expected]
        if missing:
            problems.append(f"{ts_name}: TS missing {missing}")
        if extra:
            problems.append(f"{ts_name}: TS has unknown {extra}")

    claim_state = ts_union(ts, "ClaimState")
    for required in ("Observed", "Inferred", "Ambiguous", "Unproven", "Confirmed", "BlockedAttempt"):
        if required not in claim_state:
            problems.append(f"ClaimState: TS missing {required}")

    alert_fields = ts_interface_fields(ts, "ResponsibilityAlert")
    for required in ("actionable", "decision", "primary_agent", "graph_snapshot"):
        if required not in alert_fields:
            problems.append(f"ResponsibilityAlert: TS missing {required}")

    run_fields = ts_interface_fields(api, "RunSummary")
    for required in ("run_id", "started_at_ns", "state", "scenario_ids"):
        if required not in run_fields:
            problems.append(f"RunSummary: TS missing {required}")

    # Field-level coverage. The old checks only named four alert fields, so a
    # renamed `phase`/`engine`/`timeline` or a dropped ScoreBreakdown audit
    # field passed silently. Required Rust fields must exist in TS; TS may
    # keep optional fields for older payloads.
    refinement = RUST_REFINEMENT.read_text(encoding="utf-8")
    for rust_source, struct_name, ts_name in (
        (refinement, "ScoreBreakdown", "ScoreBreakdown"),
        (rust, "ResponsibilityAlert", "ResponsibilityAlert"),
        (rust, "EngineSection", "EngineSection"),
    ):
        try:
            rust_fields = rust_struct_fields(rust_source, struct_name)
        except SystemExit:
            problems.append(f"{struct_name}: Rust struct not found (contract moved?)")
            continue
        try:
            ts_fields = ts_interface_fields(ts, ts_name)
        except SystemExit:
            problems.append(f"{ts_name}: TS interface not found")
            continue
        missing = sorted(rust_fields - ts_fields)
        if missing:
            problems.append(f"{ts_name}: TS missing Rust fields {missing}")

    if problems:
        for problem in problems:
            print(f"DRIFT {problem}", file=sys.stderr)
        return 1
    print("Rust↔TS wire contract: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
