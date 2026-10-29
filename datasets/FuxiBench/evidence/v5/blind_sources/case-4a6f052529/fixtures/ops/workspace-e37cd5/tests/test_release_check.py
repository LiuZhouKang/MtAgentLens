import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import release_check  # noqa: E402

COMMITS = (
    "aaaaaaa\t2026-10-01\tA. Tester\tfeat(core): add a feature\n"
    "bbbbbbb\t2026-10-02\tA. Tester\tfix(cli): fix a defect\n"
    "ccccccc\t2026-10-03\tA. Tester\trefactor(core)!: drop the old reader\n"
    "ddddddd\t2026-10-04\tA. Tester\tchore: tidy the tree\n"
)

CLEAN_CHANGELOG = """# Changelog

## [Unreleased]

## [3.0.0] - 2026-10-20

### Added

- Add a feature (aaaaaaa)

### Changed

- **Breaking:** Drop the old reader (ccccccc)

### Fixed

- Fix a defect (bbbbbbb)
"""


def build_workspace(root, version="3.0.0", changelog=CLEAN_CHANGELOG, commits=COMMITS):
    root = Path(root)
    (root / "release").mkdir(parents=True)
    (root / "VERSION").write_text(version + "\n", encoding="utf-8")
    (root / "release" / "last-release.txt").write_text("2.7.3\n", encoding="utf-8")
    (root / "release" / "commits.tsv").write_text(commits, encoding="utf-8")
    (root / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    for name, module in (("core", "ledger_core"), ("cli", "ledger_cli")):
        package = root / "packages" / name
        (package / "src" / module).mkdir(parents=True)
        (package / "pyproject.toml").write_text(
            f'[project]\nname = "ledger-{name}"\nversion = "{version}"\n', encoding="utf-8"
        )
        (package / "src" / module / "__init__.py").write_text(
            f'"""Package."""\n\n__version__ = "{version}"\n', encoding="utf-8"
        )
    return root


def run_gate(root):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        status = release_check.main(["--root", str(root)])
    return status, buffer.getvalue()


class ReleaseLevelTests(unittest.TestCase):
    def test_breaking_wins(self):
        commits = release_check.read_commits(self._commits())
        self.assertEqual(release_check.release_level(commits), "major")

    def test_feat_beats_fix(self):
        text = "aaaaaaa\t2026-10-01\tA\tfeat(core): x\nbbbbbbb\t2026-10-02\tA\tfix(cli): y\n"
        self.assertEqual(release_check.release_level(release_check.read_commits(self._commits(text))), "minor")

    def test_fix_only(self):
        text = "bbbbbbb\t2026-10-02\tA\tfix(cli): y\n"
        self.assertEqual(release_check.release_level(release_check.read_commits(self._commits(text))), "patch")

    def test_nothing_to_release(self):
        text = "ddddddd\t2026-10-04\tA\tchore: tidy\n"
        self.assertIsNone(release_check.release_level(release_check.read_commits(self._commits(text))))

    def test_bump(self):
        self.assertEqual(release_check.bump((2, 7, 3), "major"), (3, 0, 0))
        self.assertEqual(release_check.bump((2, 7, 3), "minor"), (2, 8, 0))
        self.assertEqual(release_check.bump((2, 7, 3), "patch"), (2, 7, 4))

    def _commits(self, text=COMMITS):
        handle = tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False, encoding="utf-8")
        try:
            handle.write(text)
        finally:
            handle.close()
        self.addCleanup(os.unlink, handle.name)
        return Path(handle.name)


class ChangelogParsingTests(unittest.TestCase):
    def test_sections_groups_and_entries(self):
        sections = release_check.parse_changelog(CLEAN_CHANGELOG)
        self.assertEqual([section.label for section in sections], ["Unreleased", "3.0.0"])
        self.assertEqual(sections[1].date, "2026-10-20")
        self.assertEqual(sections[1].entries[0], ("Added", "- Add a feature (aaaaaaa)"))


class GateTests(unittest.TestCase):
    def test_clean_release_passes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = build_workspace(temp)
            status, output = run_gate(root)
        self.assertEqual(status, 0, output)
        self.assertIn("all", output)

    def test_unbumped_version_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = build_workspace(temp, version="2.7.3")
            status, output = run_gate(root)
        self.assertEqual(status, 1)
        self.assertIn("3.0.0", output)

    def test_wrong_level_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = build_workspace(temp, version="2.8.0")
            status, output = run_gate(root)
        self.assertEqual(status, 1)
        self.assertIn("version-matches-release-level", output)

    def test_missing_entry_fails(self):
        changelog = CLEAN_CHANGELOG.replace("- Fix a defect (bbbbbbb)\n", "")
        with tempfile.TemporaryDirectory() as temp:
            root = build_workspace(temp, changelog=changelog)
            status, output = run_gate(root)
        self.assertEqual(status, 1)
        self.assertIn("changelog-complete", output)

    def test_citing_skipped_commit_fails(self):
        changelog = CLEAN_CHANGELOG.replace(
            "- Fix a defect (bbbbbbb)\n", "- Fix a defect (bbbbbbb)\n- Tidy the tree (ddddddd)\n"
        )
        with tempfile.TemporaryDirectory() as temp:
            root = build_workspace(temp, changelog=changelog)
            status, output = run_gate(root)
        self.assertEqual(status, 1)
        self.assertIn("changelog-only-release-worthy", output)

    def test_misgrouped_entry_fails(self):
        changelog = CLEAN_CHANGELOG.replace(
            "- Add a feature (aaaaaaa)\n", ""
        ).replace(
            "- **Breaking:** Drop the old reader (ccccccc)",
            "- **Breaking:** Drop the old reader (ccccccc)\n- Add a feature (aaaaaaa)",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = build_workspace(temp, changelog=changelog)
            status, output = run_gate(root)
        self.assertEqual(status, 1)
        self.assertIn("changelog-grouping", output)

    def test_duplicate_citation_fails(self):
        changelog = CLEAN_CHANGELOG.replace(
            "- Fix a defect (bbbbbbb)\n", "- Fix a defect (bbbbbbb)\n- Fix it again (bbbbbbb)\n"
        )
        with tempfile.TemporaryDirectory() as temp:
            root = build_workspace(temp, changelog=changelog)
            status, output = run_gate(root)
        self.assertEqual(status, 1)
        self.assertIn("changelog-no-duplicates", output)


if __name__ == "__main__":
    unittest.main()
