# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Check every paper claim against the measurement artifact it cites.

A claim is a JSON object with `id`, `statement`, `artifact` (relative to the
artifact root) and one expectation: `equals`, `not_equals`, or
`close_to` + `tolerance`. The point is not to test the code but to make the
paper's numbers fail loudly when the underlying artifact changes or is replaced.

Exit codes: 0 = all claims hold, 1 = a claim failed, 2 = manifest unusable.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCHEMA = "mtagentlens.paper-claims/1"


def resolve(document: object, dotted: str) -> object:
    current = document
    for part in dotted.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise KeyError(dotted)
    return current


def check_claim(claim: dict, artifacts: Path) -> tuple[bool, str]:
    artifact = Path(claim["artifact"])
    target = artifact if artifact.is_absolute() else artifacts / artifact
    if not target.is_file():
        return False, f"artifact missing: {target}"
    document = json.loads(target.read_text(encoding="utf-8"))
    try:
        actual = resolve(document, claim["path"])
    except KeyError:
        return False, f"path not found: {claim['path']}"
    if "equals" in claim:
        return actual == claim["equals"], f"{claim['path']} == {actual!r} (expected {claim['equals']!r})"
    if "not_equals" in claim:
        return actual != claim["not_equals"], f"{claim['path']} != {actual!r}"
    if "close_to" in claim:
        tolerance = float(claim.get("tolerance", 0.0))
        ok = isinstance(actual, (int, float)) and abs(float(actual) - float(claim["close_to"])) <= tolerance
        return ok, f"{claim['path']} = {actual!r} (expected {claim['close_to']!r} ± {tolerance})"
    return False, "claim has no expectation"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, default=None,
                        help="override the manifest's artifact_root")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"error: cannot read manifest: {error}", file=sys.stderr)
        return 2
    if manifest.get("schema") != SCHEMA:
        print(f"error: manifest schema is not {SCHEMA}", file=sys.stderr)
        return 2
    root = args.artifact_root or Path(manifest.get("artifact_root", "."))
    results = []
    for claim in manifest.get("claims", []):
        ok, detail = check_claim(claim, root)
        results.append({"id": claim.get("id"), "ok": ok, "detail": detail,
                        "statement": claim.get("statement")})
    failed = [r for r in results if not r["ok"]]
    report = {"schema": SCHEMA, "measurement": manifest.get("measurement"),
              "claims": len(results), "failed": len(failed), "results": results}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"measurement: {report['measurement']}")
        for row in results:
            print(f"  [{'PASS' if row['ok'] else 'FAIL'}] {row['id']}: {row['detail']}")
        print(f"{len(results) - len(failed)}/{len(results)} claims hold")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
