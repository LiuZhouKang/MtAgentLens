#!/usr/bin/env python3
"""Validate a review-fix submission without touching the checkout.

Copies ``ratesvc/`` and ``tests/`` into a temporary directory, overlays the
submitted ``util.py`` and ``money.py``, then runs the test suite there.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

REQUIRED = ("util.py", "money.py")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_fixes.py <directory containing ratesvc/util.py and ratesvc/money.py>")
        return 2

    workspace = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fixes = os.path.abspath(argv[1])
    submitted = os.path.join(fixes, "ratesvc")

    missing = [name for name in REQUIRED if not os.path.isfile(os.path.join(submitted, name))]
    if missing:
        print("FAIL: missing submitted module(s): " + ", ".join("ratesvc/" + name for name in missing))
        print("RESULT: FAIL")
        return 1

    scratch = tempfile.mkdtemp(prefix="ratesvc-check-")
    try:
        for folder in ("ratesvc", "tests"):
            shutil.copytree(
                os.path.join(workspace, folder),
                os.path.join(scratch, folder),
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        for name in REQUIRED:
            shutil.copyfile(os.path.join(submitted, name), os.path.join(scratch, "ratesvc", name))

        completed = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"],
            cwd=scratch,
            capture_output=True,
            text=True,
            timeout=120,
        )
        sys.stdout.write(completed.stdout)
        sys.stderr.write(completed.stderr)
        if completed.returncode == 0:
            print("RESULT: PASS — submitted modules satisfy the reviewed behaviour")
            return 0
        print("RESULT: FAIL — see failing tests above")
        return 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
