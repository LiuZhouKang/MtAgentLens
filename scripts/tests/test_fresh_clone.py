#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Integration tests for scripts/check_fresh_clone.sh.

Each test builds a throwaway Git repository (plus throwaway submodule
repositories) and a toy ``scripts/verify.sh``.  The toy gate records what the
snapshot actually contained and fails when an expected file is missing, an
ignored build artifact leaked, or a gitlink points at the wrong commit.

Standard library only (the system Python has no pytest):

    python3 -m unittest scripts/tests/test_fresh_clone.py -v
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

CHECK = Path(__file__).resolve().parent.parent / "check_fresh_clone.sh"

TOY_VERIFY = r"""#!/bin/sh
# SPDX-License-Identifier: Apache-2.0 OR MIT
set -eu
capture=${FRESH_TEST_CAPTURE:-}
if [ -n "$capture" ]; then
  {
    echo '##FILES##'
    find . -mindepth 1 -name .git -prune -o -print | sed 's|^\./||' | LC_ALL=C sort
    echo '##TREE##'
    git ls-tree -r HEAD
    echo '##CONTENT##'
    for spec in ${EXPECT_CONTENT:-}; do
      path=${spec%%|*}; want=${spec#*|}
      printf '%s=' "$path"
      cat "$path" 2>/dev/null || printf '<missing>'
      printf '\n'
    done
  } > "$capture"
fi
for path in ${EXPECT_PRESENT:-}; do
  [ -e "$path" ] || { echo "toy verify: missing $path"; exit 1; }
done
for path in ${EXPECT_ABSENT:-}; do
  [ ! -e "$path" ] || { echo "toy verify: leaked $path"; exit 1; }
done
for spec in ${EXPECT_CONTENT:-}; do
  path=${spec%%|*}; want=${spec#*|}
  got=$(cat "$path" 2>/dev/null || printf '<missing>')
  [ "$got" = "$want" ] || { echo "toy verify: $path is [$got], want [$want]"; exit 1; }
done
for spec in ${EXPECT_GITLINK:-}; do
  path=${spec%%:*}; want=${spec#*:}
  got=$(git ls-tree HEAD -- "$path" | awk '{print $3}')
  [ "$got" = "$want" ] || { echo "toy verify: gitlink $path is [$got], want [$want]"; exit 1; }
done
if [ -n "${EXPECT_HEAD:-}" ]; then
  [ "$(git rev-parse HEAD)" = "$EXPECT_HEAD" ] || { echo 'wrong HEAD'; exit 1; }
fi
if [ -n "${EXPECT_SUBMODULE_HEAD:-}" ]; then
  [ "$(git -C sub rev-parse HEAD)" = "$EXPECT_SUBMODULE_HEAD" ] || { echo 'wrong submodule HEAD'; exit 1; }
fi
echo "toy verify: ok"
"""


class FreshCloneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="fresh-clone-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        home = self.tmp / "home"
        home.mkdir()
        self.env = os.environ.copy()
        self.env.update(
            {
                "HOME": str(home),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": str(self.tmp / "gitconfig"),
                "GIT_AUTHOR_NAME": "Fresh Test",
                "GIT_AUTHOR_EMAIL": "fresh@example.com",
                "GIT_COMMITTER_NAME": "Fresh Test",
                "GIT_COMMITTER_EMAIL": "fresh@example.com",
                "GIT_TERMINAL_PROMPT": "0",
                # Submodule fixtures are local paths.  Production code never
                # clones submodules; this only lets the fixture be built.
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "protocol.file.allow",
                "GIT_CONFIG_VALUE_0": "always",
            }
        )

    # ── fixture helpers ────────────────────────────────────────────────────
    def git(self, cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ["git", *args], cwd=cwd, env=self.env, text=True, capture_output=True
        )
        if check and result.returncode != 0:
            self.fail(
                f"git {' '.join(args)} failed in {cwd}:\n"
                f"{result.stdout}\n{result.stderr}"
            )
        return result

    def write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, repo: Path, message: str) -> str:
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", message)
        return self.git(repo, "rev-parse", "HEAD").stdout.strip()

    def make_fixture(self, nested: bool = False) -> dict:
        tmp = self.tmp
        nested_origin = None
        if nested:
            nested_origin = tmp / "nested-origin"
            nested_origin.mkdir()
            self.git(nested_origin, "init", "-q")
            self.write(nested_origin / "nested.txt", "N")
            self.commit(nested_origin, "nested base")

        sub_origin = tmp / "sub-origin"
        sub_origin.mkdir()
        self.git(sub_origin, "init", "-q")
        if nested:
            self.git(
                sub_origin,
                "-c",
                "protocol.file.allow=always",
                "submodule",
                "add",
                str(nested_origin),
                "nested",
            )
        self.write(sub_origin / "source.txt", "A")
        self.write(sub_origin / ".gitignore", ".output/\n")
        sha_a = self.commit(sub_origin, "sub base")

        root = tmp / "repo"
        (root / "scripts").mkdir(parents=True)
        shutil.copy2(CHECK, root / "scripts" / "check_fresh_clone.sh")
        (root / "scripts" / "check_fresh_clone.sh").chmod(0o755)
        self.write(root / "scripts" / "verify.sh", TOY_VERIFY)
        (root / "scripts" / "verify.sh").chmod(0o755)
        self.write(root / "data.txt", "committed")
        self.write(root / ".gitignore", "build/\n")
        self.git(root, "init", "-q")
        self.commit(root, "base")
        self.git(
            root,
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            str(sub_origin),
            "sub",
        )
        self.git(root, "commit", "-qm", "add submodule")
        if nested:
            self.git(
                root / "sub",
                "-c",
                "protocol.file.allow=always",
                "submodule",
                "update",
                "--init",
                "nested",
            )
        return {"root": root, "sha_a": sha_a, "sub_origin": sub_origin}

    def advance_submodule(self, root: Path) -> str:
        sub = root / "sub"
        self.write(sub / "source.txt", "B")
        self.git(sub, "add", "-A")
        self.git(sub, "commit", "-qm", "local HEAD moves to B")
        return self.git(sub, "rev-parse", "HEAD").stdout.strip()

    def run_check(self, *args: str, **env_extra: str) -> subprocess.CompletedProcess:
        env = self.env.copy()
        env.update(env_extra)
        return subprocess.run(
            [str(self.tmp / "repo" / "scripts" / "check_fresh_clone.sh"), *args],
            cwd=self.tmp / "repo",
            env=env,
            text=True,
            capture_output=True,
        )

    def assert_ok(self, result: subprocess.CompletedProcess) -> None:
        self.assertEqual(
            result.returncode,
            0,
            f"check failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

    # ── tests ──────────────────────────────────────────────────────────────
    def test_candidate_mode_keeps_untracked_sources_but_not_ignored_builds(self) -> None:
        fixture = self.make_fixture()
        root = fixture["root"]
        self.write(root / "new_source.txt", "new")
        self.write(root / "build" / "leak.txt", "main build artifact")
        sub = root / "sub"
        self.write(sub / ".output" / "bin" / "leak.bin", "submodule build artifact")
        self.advance_submodule(root)  # candidate mode follows the local HEAD

        capture = self.tmp / "capture.txt"
        result = self.run_check(
            FRESH_TEST_CAPTURE=str(capture),
            EXPECT_PRESENT="new_source.txt sub/source.txt",
            EXPECT_ABSENT="build/leak.txt sub/.output/bin/leak.bin",
            EXPECT_CONTENT="sub/source.txt|B",
        )
        self.assert_ok(result)
        manifest = capture.read_text(encoding="utf-8")
        self.assertIn("new_source.txt", manifest)
        self.assertNotIn("build/leak.txt", manifest)
        self.assertNotIn("sub/.output/bin/leak.bin", manifest)

    def test_commit_mode_pins_gitlink_and_ignores_the_worktree(self) -> None:
        fixture = self.make_fixture()
        root, sha_a = fixture["root"], fixture["sha_a"]
        pinned = self.git(root, "rev-parse", "HEAD").stdout.strip()
        # Move main HEAD forward without touching the gitlink.
        self.write(root / "data.txt", "newer commit")
        self.git(root, "add", "data.txt")
        self.git(root, "commit", "-qm", "newer commit")
        self.assertNotEqual(self.git(root, "rev-parse", "HEAD").stdout.strip(), pinned)
        # Dirty main worktree, an untracked file and ignored artifacts must not
        # appear in a --commit snapshot.
        self.write(root / "data.txt", "dirty")
        self.write(root / "untracked.txt", "untracked")
        self.write(root / "build" / "leak.txt", "main build artifact")
        self.write(root / "sub" / ".output" / "bin" / "leak.bin", "sub build artifact")
        sha_b = self.advance_submodule(root)
        self.assertNotEqual(sha_a, sha_b)

        capture = self.tmp / "capture.txt"
        result = self.run_check(
            "--commit",
            pinned,
            FRESH_TEST_CAPTURE=str(capture),
            EXPECT_PRESENT="data.txt sub/source.txt",
            EXPECT_ABSENT="untracked.txt build/leak.txt sub/.output/bin/leak.bin",
            EXPECT_CONTENT="data.txt|committed sub/source.txt|A",
            EXPECT_GITLINK=f"sub:{sha_a}",
        )
        self.assert_ok(result)
        manifest = capture.read_text(encoding="utf-8")
        self.assertIn(f"160000 commit {sha_a}\tsub", manifest)

    def test_commit_mode_retains_exact_git_identity_for_build_metadata(self):
        fixture = self.make_fixture()
        root = fixture['root']
        pinned = self.git(root, 'rev-parse', 'HEAD').stdout.strip()
        self.assert_ok(self.run_check('--commit', pinned, EXPECT_HEAD=pinned))

    def test_submodule_retains_its_own_git_identity(self):
        fixture = self.make_fixture()
        self.assert_ok(self.run_check(EXPECT_PRESENT='sub/.git',
                                      EXPECT_SUBMODULE_HEAD=fixture['sha_a']))

    def test_commit_mode_rejects_an_unresolvable_ref(self) -> None:
        self.make_fixture()
        result = self.run_check("--commit", "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not resolve to a commit", result.stderr)

    def test_candidate_mode_rejects_dirty_tracked_submodule_changes(self) -> None:
        fixture = self.make_fixture()
        self.write(fixture["root"] / "sub" / "source.txt", "dirty")
        result = self.run_check()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("tracked changes", result.stderr)
        self.assertIn("sub", result.stderr)

    def test_commit_mode_rejects_an_uninitialised_submodule(self) -> None:
        fixture = self.make_fixture()
        shutil.rmtree(fixture["root"] / "sub")
        result = self.run_check("--commit", "HEAD")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("uninitialized", result.stderr)
        self.assertIn("sub", result.stderr)

    def test_commit_mode_rejects_a_gitlink_missing_from_local_objects(self) -> None:
        fixture = self.make_fixture()
        root = fixture["root"]
        missing = "deadbeef" * 5
        self.git(root, "update-index", "--add", "--cacheinfo", f"160000,{missing},sub")
        self.git(root, "commit", "-qm", "gitlink without local object")
        result = self.run_check("--commit", "HEAD")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing object", result.stderr)
        self.assertIn(missing, result.stderr)

    def test_source_index_and_head_are_never_modified(self) -> None:
        fixture = self.make_fixture()
        root = fixture["root"]

        def state() -> tuple[str, str, str, str]:
            return (
                self.git(root, "rev-parse", "HEAD").stdout,
                self.git(root, "ls-files", "--stage").stdout,
                self.git(root / "sub", "rev-parse", "HEAD").stdout,
                self.git(root / "sub", "ls-files", "--stage").stdout,
            )

        before = state()
        self.assert_ok(self.run_check(EXPECT_PRESENT="data.txt"))
        self.assert_ok(self.run_check("--commit", "HEAD", EXPECT_PRESENT="data.txt"))
        self.assertEqual(state(), before)

    def test_submodules_are_expanded_recursively_in_both_modes(self) -> None:
        fixture = self.make_fixture(nested=True)
        root = fixture["root"]
        self.write(root / "sub" / "nested" / ".output" / "leak.bin", "nested artifact")

        result = self.run_check(
            EXPECT_PRESENT="sub/nested/nested.txt",
            EXPECT_ABSENT="sub/nested/.output/leak.bin",
            EXPECT_CONTENT="sub/nested/nested.txt|N",
        )
        self.assert_ok(result)

        result = self.run_check(
            "--commit",
            "HEAD",
            EXPECT_PRESENT="sub/nested/nested.txt",
            EXPECT_ABSENT="sub/nested/.output/leak.bin",
            EXPECT_CONTENT="sub/nested/nested.txt|N",
        )
        self.assert_ok(result)


if __name__ == "__main__":
    unittest.main()
