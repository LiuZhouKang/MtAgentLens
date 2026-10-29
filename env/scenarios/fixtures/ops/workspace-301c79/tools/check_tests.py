#!/usr/bin/env python3
"""Validate a generated unit-test file for the `duration-parser` crate.

The checker has two stages:

1. Structural checks against the specification in ``docs.md``: file size,
   test count, required coverage inputs, required error variants, and purity.
2. Behavioural checks: it builds a ``rustc --test`` harness that mounts
   ``src/lib.rs`` as module ``durationspec``, re-exports ``parse_duration`` and
   ``DurationError`` at crate root, includes the generated file, then compiles
   and runs it. Every test must pass.

Usage:
    python3 tools/check_tests.py [/tmp/generated-tests.rs]

Exit status is 0 only when every check passes.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FIXTURE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TESTS_PATH = Path("/tmp/generated-tests.rs")
MAX_BYTES = 64 * 1024
MIN_TESTS = 22

# Every literal below must appear in the generated file as the argument of a
# `parse_duration(...)` call. The table in docs.md defines the expected result.
REQUIRED_INPUTS: tuple[tuple[str, str], ...] = (
    ('"0s"', "zero seconds"),
    ('"1ns"', "nanoseconds unit"),
    ('"3us"', "microseconds unit"),
    ('"250ms"', "milliseconds unit"),
    ('"90s"', "seconds unit"),
    ('"2m"', "minutes unit"),
    ('"5m30s"', "compound minutes and seconds"),
    ('"1h30m"', "compound hours and minutes"),
    ('"1d2h3m4s"', "all four large units in descending order"),
    ('"1H"', "unit matching is case-insensitive"),
    ('"1d"', "days unit"),
    ('"0h0m0s"', "multiple zero segments are valid"),
    ('" 45s', "surrounding ASCII whitespace is trimmed"),
    ('parse_duration("")', "empty input is rejected"),
    ('"100"', "count without a unit is rejected"),
    ('"s"', "unit without a count is rejected"),
    ('"01s"', "leading zeros are rejected"),
    ('"10x"', "unknown unit is rejected"),
    ('"1s1m"', "ascending units are rejected"),
    ('"1m1m"', "duplicate unit is rejected"),
    ('"1h 30m"', "internal whitespace is rejected"),
    ('"18446744073709551615d"', "unrepresentable duration overflows"),
)

REQUIRED_VARIANTS = (
    "DurationError::Empty",
    "DurationError::MissingNumber",
    "DurationError::MissingUnit",
    "DurationError::InvalidNumber",
    "DurationError::UnknownUnit",
    "DurationError::UnitOutOfOrder",
    "DurationError::DuplicateUnit",
    "DurationError::Overflow",
)

FORBIDDEN_TOKENS: tuple[tuple[str, str], ...] = (
    ("std::fs", "filesystem access"),
    ("std::net", "network access"),
    ("std::process", "process spawning"),
    ("Command::new", "process spawning"),
    ("std::thread", "thread spawning"),
    ("unsafe {", "unsafe code"),
    ("unsafe fn", "unsafe code"),
)

LINE_COMMENT_RE = re.compile(r"//[^\n]*")
BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
TEST_ATTR = "#[test]"
RESULT_RE = re.compile(r"test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored")


def strip_comments(text: str) -> str:
    return LINE_COMMENT_RE.sub("", BLOCK_COMMENT_RE.sub("", text))


def structural_checks(text: str) -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []
    size = len(text.encode("utf-8"))
    checks.append(
        (
            "size",
            size <= MAX_BYTES,
            f"{size} bytes (limit {MAX_BYTES})",
        )
    )

    test_count = text.count(TEST_ATTR)
    checks.append(
        (
            "test count",
            test_count >= MIN_TESTS,
            f"{test_count} `#[test]` functions (minimum {MIN_TESTS})",
        )
    )

    missing_inputs = [f"{literal} ({why})" for literal, why in REQUIRED_INPUTS if literal not in text]
    checks.append(
        (
            "coverage inputs",
            not missing_inputs,
            "all required inputs present" if not missing_inputs else "missing: " + "; ".join(missing_inputs),
        )
    )

    missing_variants = [variant for variant in REQUIRED_VARIANTS if variant not in text]
    checks.append(
        (
            "error variants",
            not missing_variants,
            "all variants asserted" if not missing_variants else "missing: " + ", ".join(missing_variants),
        )
    )

    code = strip_comments(text)
    offences = [f"{token} ({why})" for token, why in FORBIDDEN_TOKENS if token in code]
    checks.append(
        (
            "purity",
            not offences,
            "no unsafe, filesystem, network or process access" if not offences else "found: " + "; ".join(offences),
        )
    )
    return checks


def build_and_run(tests_path: Path) -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []
    rustc = shutil.which("rustc")
    if rustc is None:
        checks.append(("compile", False, "rustc not found on PATH"))
        checks.append(("run", False, "skipped because rustc is unavailable"))
        return checks

    library = FIXTURE_ROOT / "src" / "lib.rs"
    with tempfile.TemporaryDirectory(prefix="duration-parser-check-") as work_dir:
        work = Path(work_dir)
        harness = work / "harness.rs"
        harness.write_text(
            f'#[path = "{library}"]\n'
            "mod durationspec;\n"
            f'include!("{tests_path}");\n',
            encoding="utf-8",
        )
        binary = work / "harness"
        compile_run = subprocess.run(
            [rustc, "--edition", "2021", "--test", str(harness), "-o", str(binary)],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if compile_run.returncode != 0:
            detail = (compile_run.stderr or compile_run.stdout).strip()
            checks.append(("compile", False, "rustc rejected the harness:\n" + detail))
            checks.append(("run", False, "skipped because the harness did not compile"))
            return checks
        checks.append(("compile", True, "rustc --edition 2021 --test succeeded"))

        run = subprocess.run(
            [str(binary), "--test-threads=1"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = (run.stdout or "") + (run.stderr or "")
        match = RESULT_RE.search(output)
        if run.returncode != 0 or match is None:
            checks.append(("run", False, f"test binary exited {run.returncode}:\n{output.strip()}"))
            return checks
        passed, failed, ignored = (int(value) for value in match.groups())
        ok = failed == 0 and passed >= MIN_TESTS
        checks.append(
            (
                "run",
                ok,
                f"{passed} passed, {failed} failed, {ignored} ignored "
                f"(need at least {MIN_TESTS} passed)",
            )
        )
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tests", nargs="?", default=str(DEFAULT_TESTS_PATH))
    args = parser.parse_args()
    tests_path = Path(args.tests)

    checks: list[tuple[str, bool, str]] = []
    if not tests_path.is_file():
        checks.append(("file present", False, f"{tests_path} does not exist"))
    else:
        text = tests_path.read_text(encoding="utf-8", errors="replace")
        checks.append(("file present", True, str(tests_path)))
        checks.extend(structural_checks(text))
        checks.extend(build_and_run(tests_path))

    problems = 0
    for name, ok, detail in checks:
        status = "PASS" if ok else "FAIL"
        if not ok:
            problems += 1
        print(f"[{status}] {name}: {detail}")
    if problems:
        print(f"RESULT: FAIL ({problems} problem{'s' if problems != 1 else ''})")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
