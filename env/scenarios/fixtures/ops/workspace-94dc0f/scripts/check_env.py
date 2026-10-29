#!/usr/bin/env python3
"""Check the checked-in environment example against the configuration schema.

The example file is a template, so a sensitive variable that holds a
real-looking value means somebody pasted a working credential into a committed
file. The checker treats that as a failure, alongside the ordinary contract
errors: a required variable with no value, a variable declared in the schema
but absent from the example, or a variable in the example that the schema does
not declare.

Run from the project root:

    python3 scripts/check_env.py --json build/env-report.json

Exit status is 0 when the example matches the schema and 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV = ROOT / ".env.example"
DEFAULT_SCHEMA = ROOT / "config" / "env.schema.json"

# Substrings that mark an illustrative value rather than a working one. The
# list is deliberately generous: a false positive only asks the author to make
# the placeholder more obvious, while a false negative hides a committed
# credential.
PLACEHOLDER_TOKENS = (
    "changeme",
    "change-me",
    "change_me",
    "dummy",
    "example",
    "placeholder",
    "redacted",
    "replace-me",
    "replace_me",
    "sample",
    "todo",
    "xxxx",
    "your-",
    "your_",
)

_ASSIGNMENT_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def parse_env_file(path: Path) -> dict[str, str]:
    """Return ``{name: value}`` for the uncommented assignments in *path*.

    Comment lines and blank lines are skipped, a leading ``export`` is
    ignored, and one pair of surrounding quotes is stripped.
    """
    values: dict[str, str] = {}
    for lineno, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _ASSIGNMENT_RE.match(line)
        if match is None:
            raise ValueError(f"{path}:{lineno}: expected KEY=VALUE, got {line!r}")
        name, value = match.group(1), match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name in values:
            raise ValueError(f"{path}:{lineno}: {name} is assigned twice")
        values[name] = value
    return values


def value_kind(value: str | None) -> str:
    """Classify a value as ``empty``, ``placeholder`` or ``real``."""
    text = (value or "").strip()
    if not text:
        return "empty"
    if text.startswith("<") and text.endswith(">"):
        return "placeholder"
    if text.startswith("${") and text.endswith("}"):
        return "placeholder"
    lowered = text.lower()
    if any(token in lowered for token in PLACEHOLDER_TOKENS):
        return "placeholder"
    return "real"


def check(
    values: dict[str, str], schema: dict
) -> tuple[list[str], list[dict]]:
    """Compare parsed example values with the schema.

    Returns ``(problems, records)`` where each record documents one declared
    variable and each problem is one contract violation.
    """
    declared: dict[str, dict] = schema.get("variables", {})
    problems: list[str] = []
    records: list[dict] = []

    for name, spec in declared.items():
        required = bool(spec.get("required"))
        sensitive = bool(spec.get("sensitive"))
        if name not in values:
            problems.append(f"{name}: declared in the schema but absent from the example")
            kind = "absent"
        else:
            kind = value_kind(values[name])
            if required and kind == "empty":
                problems.append(f"{name}: required variable has no value in the example")
            if sensitive and kind == "real":
                problems.append(
                    f"{name}: sensitive variable holds a non-placeholder value"
                )
        records.append(
            {
                "name": name,
                "required": required,
                "sensitive": sensitive,
                "kind": kind,
                "consumer": spec.get("consumer", ""),
                "description": spec.get("description", ""),
            }
        )

    for name in sorted(set(values) - set(declared)):
        problems.append(f"{name}: present in the example but not declared in the schema")

    return problems, records


def summarise(values: dict[str, str], records: list[dict]) -> dict:
    """Count the categories reported to the reviewer."""
    kinds = [record["kind"] for record in records]
    return {
        "variables_in_example": len(values),
        "declared_in_schema": len(records),
        "required": sum(1 for record in records if record["required"]),
        "sensitive": sum(1 for record in records if record["sensitive"]),
        "placeholders": kinds.count("placeholder"),
        "real_defaults": kinds.count("real"),
        "empty": kinds.count("empty"),
        "absent": kinds.count("absent"),
        "undeclared_in_example": len(set(values) - {record["name"] for record in records}),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV, help="example env file")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA, help="schema file")
    parser.add_argument("--json", type=Path, default=None, help="write the full report here")
    parser.add_argument("--quiet", action="store_true", help="only set the exit status")
    args = parser.parse_args(argv)

    values = parse_env_file(args.env)
    schema = json.loads(Path(args.schema).read_text(encoding="utf-8"))
    problems, records = check(values, schema)
    summary = summarise(values, records)

    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "env_file": str(args.env),
            "schema": str(args.schema),
            "schema_version": schema.get("version"),
            "summary": summary,
            "variables": records,
            "problems": problems,
        }
        args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if not args.quiet:
        print("ledger-api environment example check")
        print(f"  variables in example : {summary['variables_in_example']}")
        print(f"  declared in schema   : {summary['declared_in_schema']}")
        print(f"  required             : {summary['required']}")
        print(f"  sensitive            : {summary['sensitive']}")
        print(f"  placeholders         : {summary['placeholders']}")
        print(f"  real defaults        : {summary['real_defaults']}")
        for problem in problems:
            print(f"  ! {problem}")
        if problems:
            print(f"FAILED: {len(problems)} problem(s)")
        else:
            print("OK: the example matches the declared environment contract")

    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
