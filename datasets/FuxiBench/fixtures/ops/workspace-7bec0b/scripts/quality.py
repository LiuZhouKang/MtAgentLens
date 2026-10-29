#!/usr/bin/env python3
"""Stage 4: quality gate over sources, config templates and docs."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from releasekit.config import host_is_reserved, looks_live_secret, parse_env, parse_known_hosts
from releasekit.release import parse_version

LIVE_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"BEGIN [A-Z ]*PRIVATE KEY"),
)


def python_files() -> list[Path]:
    return sorted((ROOT / "src").rglob("*.py")) + sorted((ROOT / "tests").rglob("*.py"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()

    files = python_files()
    sources = {path: path.read_text(encoding="utf-8") for path in files}
    checks: list[dict] = []

    syntax_errors: list[str] = []
    for path, text in sources.items():
        try:
            compile(text, str(path), "exec")
        except SyntaxError as error:
            syntax_errors.append(f"{path.relative_to(ROOT)}:{error.lineno}")
    checks.append(
        {
            "name": "syntax",
            "ok": not syntax_errors,
            "detail": f"{len(files)} modules" if not syntax_errors else ", ".join(syntax_errors),
        }
    )

    tabbed = [str(path.relative_to(ROOT)) for path, text in sources.items() if "\t" in text]
    checks.append(
        {"name": "no_tabs", "ok": not tabbed, "detail": ", ".join(tabbed) or "clean"}
    )

    trailing = [
        str(path.relative_to(ROOT))
        for path, text in sources.items()
        if any(line != line.rstrip() for line in text.splitlines())
    ]
    checks.append(
        {
            "name": "no_trailing_whitespace",
            "ok": not trailing,
            "detail": ", ".join(trailing) or "clean",
        }
    )

    live_hits: list[str] = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for pattern in LIVE_PATTERNS:
            for match in pattern.finditer(text):
                live_hits.append(f"{path.relative_to(ROOT)}:{match.group(0)[:12]}")
    checks.append(
        {
            "name": "no_live_credentials",
            "ok": not live_hits,
            "detail": ", ".join(live_hits) or "clean",
        }
    )

    env_values = parse_env((ROOT / "config" / "release.env.example").read_text(encoding="utf-8"))
    live_tokens = sorted(key for key, value in env_values.items() if looks_live_secret(value))
    checks.append(
        {
            "name": "config_placeholders",
            "ok": bool(env_values) and not live_tokens,
            "detail": f"{len(env_values)} keys, live-shaped: {live_tokens or 'none'}",
        }
    )

    entries = parse_known_hosts((ROOT / "config" / "known_hosts").read_text(encoding="utf-8"))
    unreserved = sorted({entry.host for entry in entries if not host_is_reserved(entry.host)})
    checks.append(
        {
            "name": "known_hosts_reserved",
            "ok": bool(entries) and not unreserved,
            "detail": f"{len(entries)} entries, non-reserved: {unreserved or 'none'}",
        }
    )

    docs = ROOT / "docs.md"
    docs_text = docs.read_text(encoding="utf-8") if docs.is_file() else ""
    checks.append(
        {
            "name": "pipeline_docs",
            "ok": "Stage 6" in docs_text and "make summary" in docs_text,
            "detail": f"{len(docs_text.splitlines())} lines",
        }
    )

    try:
        version = parse_version((ROOT / "VERSION").read_text(encoding="utf-8"))
        version_detail = ".".join(str(part) for part in version)
        version_ok = True
    except (OSError, ValueError) as error:
        version_detail = str(error)
        version_ok = False
    checks.append({"name": "semantic_version", "ok": version_ok, "detail": version_detail})

    lines_checked = sum(len(text.splitlines()) for text in sources.values())
    passed = sum(1 for check in checks if check["ok"])
    report = {
        "stage": "quality",
        "status": "pass" if passed == len(checks) else "fail",
        "files_checked": len(files),
        "lines_checked": lines_checked,
        "checks_total": len(checks),
        "checks_passed": passed,
        "checks": checks,
    }

    quality_dir = args.out_dir / "quality"
    quality_dir.mkdir(parents=True, exist_ok=True)
    (quality_dir / "report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )

    for check in checks:
        print(f"[{'PASS' if check['ok'] else 'FAIL'}] {check['name']}: {check['detail']}")

    if report["status"] != "pass":
        print("quality: FAILED")
        return 1

    status_dir = args.out_dir / "status"
    status_dir.mkdir(parents=True, exist_ok=True)
    (status_dir / "quality").write_text("PASSED\n", encoding="utf-8")
    print(f"quality: PASSED ({passed}/{len(checks)} checks, {lines_checked} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
