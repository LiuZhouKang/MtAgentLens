#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Dependency audit gate for MtAgentLens.

Two different jobs live here, because they have different trust properties:

* ``--check-budget`` is offline and deterministic. It validates the accepted
  advisory budget file (``scripts/audit-budget.json``) so the budget cannot rot
  into a blank cheque. ``scripts/verify.sh`` runs this form.
* ``cargo`` / ``npm`` / ``all`` fetch the RustSec advisory database and the npm
  registry. They are a separate command (no CI is configured; ADR-0014) and are run by
  anyone with network access.

The rule for every accepted advisory is the same as every other budget in this
repository: it may only *decrease*, and it must carry a reason. An entry that no
longer matches a live advisory fails the gate so the number is lowered the same
day the vulnerability is fixed.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUDGET_PATH = ROOT / "scripts" / "audit-budget.json"
COLLECTOR = ROOT / "collector"
FRONTEND = ROOT / "frontend"

# npm audit severities that fail the gate. `low`/`info` are reported but do not
# fail: the product's dependency surface is the build toolchain, and a low
# advisory in a transitive dev dependency is noise until it becomes exploitable.
FAILING_NPM_SEVERITIES = {"moderate", "high", "critical"}


class GateError(RuntimeError):
    """A condition that must fail the gate rather than be skipped."""


def load_budget(path: Path | None = None) -> dict:
    path = path or BUDGET_PATH
    if not path.exists():
        raise GateError(f"missing budget file: {path}")
    try:
        budget = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise GateError(f"{path} is not valid JSON: {exc}") from exc

    for section in ("cargo", "npm"):
        entries = budget.get(section)
        if not isinstance(entries, list):
            raise GateError(f"{path}: `{section}` must be a list")
        for index, entry in enumerate(entries):
            where = f"{path}: {section}[{index}]"
            if not isinstance(entry, dict):
                raise GateError(f"{where} must be an object")
            reason = entry.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise GateError(f"{where} has no reason; an unexplained budget is a disable")
            if section == "cargo":
                advisory = entry.get("id")
                if not isinstance(advisory, str) or not advisory.strip():
                    raise GateError(f"{where} has no advisory `id`")
            else:
                package = entry.get("package")
                if not isinstance(package, str) or not package.strip():
                    raise GateError(f"{where} has no `package`")
                advisory = entry.get("advisory")
                if not isinstance(advisory, str) or not advisory.strip():
                    raise GateError(
                        f"{where} has no `advisory`; a package-level budget would "
                        f"pre-accept every future advisory for that package"
                    )
                severity = entry.get("severity")
                if severity not in FAILING_NPM_SEVERITIES:
                    raise GateError(
                        f"{where}: `severity` must be one of "
                        f"{sorted(FAILING_NPM_SEVERITIES)} (the only ones that fail)"
                    )
    return budget


def check_budget(_args: argparse.Namespace) -> int:
    budget = load_budget()
    print(
        f"audit budget: {len(budget['cargo'])} cargo / {len(budget['npm'])} npm "
        f"accepted advisories, every one with a reason"
    )

    # An empty budget is meaningful (nothing accepted). A *stale* entry is not:
    # it is a number that stayed high after the vulnerability went away.
    if budget["cargo"] or budget["npm"]:
        print(
            "note: non-empty budget — the network gates below will fail if an "
            "entry no longer matches a live advisory"
        )
    return 0


def run(command: list[str], cwd: Path, timeout_s: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            command, cwd=cwd, capture_output=True, text=True, timeout=timeout_s
        )
    except subprocess.TimeoutExpired as exc:
        raise GateError(
            f"`{' '.join(command)}` timed out after {timeout_s}s — the advisory "
            f"database or registry is unreachable. This is a gate failure, not a skip."
        ) from exc


def require_cargo_report(report: object) -> None:
    """Reject anything that is not a cargo-audit vulnerability report."""
    if (
        not isinstance(report, dict)
        or not isinstance(report.get("vulnerabilities"), dict)
        or not isinstance(report["vulnerabilities"].get("list"), list)
    ):
        raise GateError(
            "cargo audit did not return a vulnerability report (advisory database "
            "or tool failure); refusing to read that as zero vulnerabilities"
        )


def require_npm_report(report: object, returncode: int) -> None:
    """Reject anything that is not an npm audit report.

    npm uses 0 (clean) and 1 (vulnerabilities found). A mirror without the
    audit endpoint answers with an error object; reading its missing
    `vulnerabilities` key as "zero advisories" is the false green this gate
    exists to prevent.
    """
    if returncode not in (0, 1):
        raise GateError(
            f"npm audit exited {returncode} — the registry or the audit endpoint failed"
        )
    if (
        not isinstance(report, dict)
        or not isinstance(report.get("vulnerabilities"), dict)
        or "auditReportVersion" not in report
        or "metadata" not in report
        or report.get("error")
    ):
        raise GateError(
            "npm audit did not return an audit report (registry or endpoint failure)"
        )


def evaluate_cargo(report: dict, accepted: dict) -> tuple[list[str], list[str], list[str], int]:
    """Compare a `cargo audit --json` report against the accepted budget.

    Returns ``(failures, accepted_lines, stale_ids, live_count)``. Pure so the
    contract can be tested without network or a RustSec database.
    """
    vulnerabilities = report.get("vulnerabilities", {}).get("list", []) or []
    found_ids = set()
    failures = []
    accepted_lines = []
    for item in vulnerabilities:
        advisory = item.get("advisory", {}) or {}
        package = item.get("package", {}) or {}
        advisory_id = advisory.get("id", "<unknown>")
        found_ids.add(advisory_id)
        label = f"{package.get('name', '?')} {package.get('version', '?')}: {advisory_id}"
        if advisory_id in accepted:
            accepted_lines.append(f"{label} — {accepted[advisory_id]['reason']}")
        else:
            failures.append(f"{label} — {advisory.get('title', '')}")
    stale = sorted(set(accepted) - found_ids)
    for advisory_id in stale:
        failures.append(
            f"budget entry {advisory_id} is stale (no live advisory); remove it "
            f"from {BUDGET_PATH} — budgets may only decrease"
        )
    return failures, accepted_lines, stale, len(vulnerabilities)


def npm_advisory_ids(data: dict) -> list[str]:
    """Advisory identifiers attached to one vulnerable npm package.

    `via` contains objects for real advisories and plain strings for
    "this package is vulnerable because that package is". The object's URL is
    the stable identity (GHSA); `source` is npm's numeric fallback.
    """
    ids: list[str] = []
    for entry in data.get("via", []) or []:
        if not isinstance(entry, dict):
            continue
        url = entry.get("url") or ""
        match = re.search(r"(GHSA-[0-9a-z-]+)", url, re.IGNORECASE)
        if match:
            ids.append(match.group(1).upper())
        elif entry.get("source") is not None:
            ids.append(str(entry["source"]))
        elif entry.get("title"):
            ids.append(str(entry["title"]))
    return ids


def evaluate_npm(
    report: dict, accepted: dict
) -> tuple[list[str], list[tuple[str, str]], int, int]:
    """Compare an `npm audit --json` report against the accepted budget.

    Budget entries are keyed by ``(package, advisory)``, not by
    ``(package, severity)``: a package-level entry would pre-accept every
    future advisory for that package, and the stale rule could never notice
    the difference.

    npm reports every affected *parent* with ``via: ["child"]`` (plain
    strings, no advisory object). Those are propagation nodes: the real
    advisory lives on the child, which appears in the same report with its own
    severity (npm propagates the maximum), so it is gated there. Requiring a
    budget entry for the parent would make the accepted state inexpressible.

    Returns ``(failures, matched_keys, failing_count, propagated_count)``.
    """
    vulnerabilities = report.get("vulnerabilities") or {}
    failures = []
    matched = set()
    failing_count = 0
    propagated = 0
    for package, data in sorted(vulnerabilities.items()):
        severity = data.get("severity", "unknown")
        if severity not in FAILING_NPM_SEVERITIES:
            continue
        advisories = npm_advisory_ids(data)
        if not advisories:
            via_packages = [
                entry for entry in (data.get("via", []) or []) if isinstance(entry, str)
            ]
            if via_packages:
                propagated += 1
                continue
            # Fail closed: an advisory we cannot identify cannot be reviewed.
            failures.append(
                f"{package} ({severity}) has no identifiable advisory id and no "
                f"propagation source; cannot verify it against the budget"
            )
            continue
        failing_count += 1
        for advisory in advisories:
            key = (package, advisory)
            if key in accepted:
                matched.add(key)
            else:
                failures.append(f"{package} ({severity}) {advisory}")

    for key in sorted(set(accepted) - matched):
        failures.append(
            f"budget entry {key[0]} {key[1]} is stale (no live advisory); remove "
            f"it from {BUDGET_PATH} — budgets may only decrease"
        )
    return failures, sorted(matched), failing_count, propagated


def cargo_audit(_args: argparse.Namespace) -> int:
    budget = load_budget()
    accepted = {entry["id"]: entry for entry in budget["cargo"]}

    if shutil.which("cargo-audit") is None:
        raise GateError(
            "cargo-audit is not installed. Install it in CI (or locally) with:\n"
            "    cargo install cargo-audit --locked\n"
            "This gate must fail rather than skip: a skipped audit is a green "
            "badge nobody earned."
        )

    proc = run(["cargo", "audit", "--json"], COLLECTOR, timeout_s=300)
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        # A tool/network failure is not a pass. Quote the tail of stderr so the
        # operator sees whether the database could not be fetched.
        tail = "\n".join(proc.stderr.strip().splitlines()[-8:])
        raise GateError(
            f"cargo audit produced no parseable JSON (exit {proc.returncode}). "
            f"Last stderr lines:\n{tail}"
        ) from exc

    require_cargo_report(report)
    failures, accepted_lines, _stale, live_count = evaluate_cargo(report, accepted)
    for line in accepted_lines:
        print(f"  accepted {line}")

    warnings = report.get("warnings", {}) or {}
    for kind in ("unmaintained", "unsound", "yanked"):
        items = warnings.get(kind, []) or []
        if items:
            print(f"  note: {len(items)} {kind} warning(s) (not gated)")

    if failures:
        print("\ncargo audit FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print(f"cargo audit: {live_count} live vulnerabilities, all accepted")
    return 0


def npm_audit(_args: argparse.Namespace) -> int:
    budget = load_budget()
    accepted = {(entry["package"], entry["advisory"]): entry for entry in budget["npm"]}

    # `--omit=dev`: the gate is about what ships inside the collector binary.
    # Dev-only advisories (storybook, vitest, eslint) never reach the artifact;
    # they are recorded as a residual in docs/capabilities.json instead of
    # becoming a 20-entry reason wall that would rot.
    proc = run(
        [
            "npm",
            "audit",
            "--omit=dev",
            "--json",
            "--registry=https://registry.npmjs.org",
        ],
        FRONTEND,
        timeout_s=180,
    )
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        tail = "\n".join(proc.stderr.strip().splitlines()[-8:])
        raise GateError(
            f"npm audit produced no parseable JSON (exit {proc.returncode}). "
            f"Last stderr lines:\n{tail}"
        ) from exc

    require_npm_report(report, proc.returncode)
    failures, matched, failing_count, propagated = evaluate_npm(report, accepted)
    for package, advisory in matched:
        print(
            f"  accepted {package} {advisory} — "
            f"{accepted[(package, advisory)]['reason']}"
        )

    if failures:
        print("\nnpm audit FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print(
        f"npm audit (production dependencies): {failing_count} live advisories "
        f"at failing severity, all accepted; {propagated} propagation-only "
        f"package(s) covered by their source advisory"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-budget",
        action="store_true",
        help="offline: validate scripts/audit-budget.json only",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="all",
        choices=("cargo", "npm", "all"),
        help="which online audit to run (default: all; needs network)",
    )
    args = parser.parse_args(argv)

    try:
        if args.check_budget:
            return check_budget(args)
        if args.command == "cargo":
            return cargo_audit(args)
        if args.command == "npm":
            return npm_audit(args)
        return max(cargo_audit(args), npm_audit(args))
    except GateError as exc:
        print(f"audit gate FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
