# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Contract tests for verify.sh's cargo preflight.

Every Rust gate in verify.sh runs ``flock "$CARGO_LOCK" cargo ...``. When cargo
is not on PATH the wrappers swallow the real cause: ``cargo fmt`` is reported as
"diffs" and ``cargo test`` as "no 'test result' line". These tests pin the fix:
resolve rustup's default location when PATH is missing it, and fail with an
actionable message (not a fake gate failure) when cargo is genuinely absent.

``--preflight-only`` runs just the cheap environment checks, so the test never
builds eBPF or compiles Rust.
"""

from __future__ import annotations

import os
import pathlib
import stat
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
VERIFY = ROOT / "scripts" / "verify.sh"
# A PATH that cannot contain cargo on any of the hosts this repo runs on.
BASE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
FAKE_CARGO = "#!/bin/sh\necho 'cargo 1.99.0 (stub 2026-10-27)'\n"


def _write_cargo(bin_dir: pathlib.Path) -> pathlib.Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    cargo = bin_dir / "cargo"
    cargo.write_text(FAKE_CARGO, encoding="utf-8")
    cargo.chmod(cargo.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return cargo


def _run(home: pathlib.Path, path: str = BASE_PATH) -> subprocess.CompletedProcess:
    env = {"PATH": path, "HOME": str(home), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    return subprocess.run(
        ["bash", str(VERIFY), "--preflight-only"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


class CargoPreflightTests(unittest.TestCase):
    def test_unknown_argument_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run(
                ["bash", str(VERIFY), "--no-such-flag"],
                cwd=str(ROOT),
                env={"PATH": BASE_PATH, "HOME": home, "LANG": "C.UTF-8"},
                capture_output=True,
                text=True,
                timeout=120,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown argument", result.stdout + result.stderr)

    def test_resolves_home_cargo_when_path_is_missing_it(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            cargo = _write_cargo(pathlib.Path(home) / ".cargo" / "bin")
            self.assertTrue(cargo.is_file())
            result = _run(pathlib.Path(home))
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("cargo resolved from", out)
        self.assertIn("1.99.0", out)

    def test_fails_loudly_when_cargo_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            result = _run(pathlib.Path(home))
        out = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, out)
        self.assertIn("cargo not found", out)
        # The misleading wrapper diagnosis must not be what the user sees.
        self.assertNotIn("reported diffs", out)

    def test_uses_cargo_already_on_path(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as stub:
            _write_cargo(pathlib.Path(stub))
            result = _run(pathlib.Path(home), path=f"{stub}:{BASE_PATH}")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn("1.99.0", out)
        self.assertNotIn("cargo resolved from", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
