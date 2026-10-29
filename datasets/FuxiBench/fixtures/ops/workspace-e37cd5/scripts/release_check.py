#!/usr/bin/env python3
"""Release gate for the ledger monorepo.

Implements the rules in ``docs/release-runbook.md``:

* the version in ``VERSION`` is a valid semantic version;
* the version is exactly the bump implied by ``release/commits.tsv`` applied to
  the previous release in ``release/last-release.txt``;
* every package manifest and package module carries the same version;
* ``CHANGELOG.md`` keeps an empty Unreleased section and gains one released
  section for the new version, grouped and cited according to the runbook.

Exit status is 0 when every check passes and 1 otherwise. Usage::

    python3 scripts/release_check.py [--root PATH]
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HASH_RE = re.compile(r"^[0-9a-f]{7}$")
COMMIT_RE = re.compile(
    r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?: (?P<summary>.+)$"
)
SECTION_RE = re.compile(r"^## \[(?P<label>[^\]]+)\](?: - (?P<date>\S+))?\s*$")
GROUP_RE = re.compile(r"^### (?P<name>.+?)\s*$")
ENTRY_RE = re.compile(r"^- (?P<text>.+?) \((?P<hash>[0-9a-f]{7})\)\s*$")

ALLOWED_GROUPS = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")
REQUIRED_GROUPS = {"feat": "Added", "fix": "Fixed"}
PYPROJECT_NAME_RE = re.compile(r'^name\s*=\s*"([^"]+)"\s*$', re.MULTILINE)
PYPROJECT_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"\s*$', re.MULTILINE)
MODULE_VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"\s*$', re.MULTILINE)


@dataclass(frozen=True)
class Commit:
    hash: str
    date: str
    author: str
    subject: str
    type: str
    breaking: bool
    summary: str

    @property
    def release_worthy(self) -> bool:
        return self.breaking or self.type in ("feat", "fix")


@dataclass
class ChangelogSection:
    label: str
    date: str | None
    groups: dict
    entries: list


class Gate:
    """Collects check results and decides the exit status."""

    def __init__(self) -> None:
        self.failures: list = []
        self.passes: list = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        if ok:
            self.passes.append(name)
        else:
            self.failures.append((name, detail or name))
        return ok

    def fail(self, name: str, detail: str) -> None:
        self.failures.append((name, detail))

    def report(self) -> int:
        for name in self.passes:
            print(f"[ OK ] {name}")
        for name, detail in self.failures:
            print(f"[FAIL] {name}: {detail}")
        total = len(self.passes) + len(self.failures)
        print()
        if self.failures:
            print(f"release gate: {len(self.failures)} of {total} checks failed")
            return 1
        print(f"release gate: all {total} checks passed")
        return 0


def parse_version(text: str) -> tuple:
    return tuple(int(part) for part in text.split("."))


def bump(version: tuple, kind: str) -> tuple:
    major, minor, patch = version
    if kind == "major":
        return (major + 1, 0, 0)
    if kind == "minor":
        return (major, minor + 1, 0)
    return (major, minor, patch + 1)


def release_level(commits: list) -> str | None:
    if any(commit.breaking for commit in commits):
        return "major"
    if any(commit.type == "feat" for commit in commits):
        return "minor"
    if any(commit.type == "fix" for commit in commits):
        return "patch"
    return None


def read_commits(path: Path) -> list:
    commits = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 4:
            raise ValueError(f"{path.name}:{number}: expected 4 tab-separated columns")
        commit_hash, date, author, subject = (field.strip() for field in fields)
        if not HASH_RE.match(commit_hash):
            raise ValueError(f"{path.name}:{number}: bad hash {commit_hash!r}")
        if not DATE_RE.match(date):
            raise ValueError(f"{path.name}:{number}: bad date {date!r}")
        match = COMMIT_RE.match(subject)
        if not match:
            raise ValueError(f"{path.name}:{number}: not a Conventional Commit: {subject!r}")
        breaking = bool(match.group("bang"))
        commit_type = match.group("type")
        commits.append(
            Commit(
                hash=commit_hash,
                date=date,
                author=author,
                subject=subject,
                type=commit_type,
                breaking=breaking,
                summary=match.group("summary"),
            )
        )
    return commits


def parse_changelog(text: str) -> list:
    sections = []
    current = None
    group = None
    for line in text.splitlines():
        heading = SECTION_RE.match(line)
        if heading:
            current = ChangelogSection(
                label=heading.group("label"),
                date=heading.group("date"),
                groups={},
                entries=[],
            )
            sections.append(current)
            group = None
            continue
        if current is None:
            continue
        group_heading = GROUP_RE.match(line)
        if group_heading:
            group = group_heading.group("name")
            current.groups.setdefault(group, [])
            continue
        if line.startswith("- ") or line.startswith("* "):
            current.entries.append((group, line.strip()))
    return sections


def collect_versions(root: Path, gate: Gate) -> str | None:
    version_file = root / "VERSION"
    if not gate.check("version-file", version_file.is_file(), "VERSION is missing"):
        return None
    raw_version = version_file.read_text(encoding="utf-8").strip()
    if not gate.check(
        "version-format", bool(VERSION_RE.match(raw_version)), f"VERSION holds {raw_version!r}"
    ):
        return None

    previous_file = root / "release" / "last-release.txt"
    if not gate.check(
        "previous-release-file", previous_file.is_file(), "release/last-release.txt is missing"
    ):
        return None
    raw_previous = previous_file.read_text(encoding="utf-8").strip()
    if not gate.check(
        "previous-release-format",
        bool(VERSION_RE.match(raw_previous)),
        f"last-release.txt holds {raw_previous!r}",
    ):
        return None

    try:
        commits = read_commits(root / "release" / "commits.tsv")
    except (OSError, ValueError) as error:
        gate.fail("commit-log", str(error))
        return None
    gate.check("commit-log", len(commits) > 0, "release/commits.tsv has no commits")

    level = release_level(commits)
    if not gate.check(
        "release-level",
        level is not None,
        "no feat, fix or breaking commit; there is nothing to release",
    ):
        return None
    previous = parse_version(raw_previous)
    expected = bump(previous, level)
    expected_text = ".".join(str(part) for part in expected)
    print(f"previous release: {raw_previous}")
    print(f"release level from commits.tsv: {level}")
    print(f"expected version: {expected_text}")
    print()

    gate.check(
        "version-bumped",
        parse_version(raw_version) > previous,
        f"VERSION is still {raw_version}; expected {expected_text} ({level} bump)",
    )
    gate.check(
        "version-matches-release-level",
        parse_version(raw_version) == expected,
        f"VERSION is {raw_version}; commits imply a {level} bump to {expected_text}",
    )

    for manifest in sorted((root / "packages").glob("*/pyproject.toml")):
        text = manifest.read_text(encoding="utf-8")
        name_match = PYPROJECT_NAME_RE.search(text)
        version_match = PYPROJECT_VERSION_RE.search(text)
        label = f"manifest:{manifest.parent.name}"
        if not version_match:
            gate.fail(label, f"{manifest} has no version field")
            continue
        found = version_match.group(1)
        gate.check(
            label,
            found == raw_version,
            f"{name_match.group(1) if name_match else manifest.parent.name} pins {found}, "
            f"VERSION is {raw_version}",
        )

    for module in sorted((root / "packages").glob("*/src/*/__init__.py")):
        text = module.read_text(encoding="utf-8")
        version_match = MODULE_VERSION_RE.search(text)
        label = f"module:{module.parent.name}"
        if not version_match:
            gate.fail(label, f"{module} has no __version__")
            continue
        gate.check(
            label,
            version_match.group(1) == raw_version,
            f"{module.parent.name} declares {version_match.group(1)}, VERSION is {raw_version}",
        )
    return expected_text


def collect_changelog(root: Path, gate: Gate, version: str) -> None:
    changelog = root / "CHANGELOG.md"
    if not gate.check("changelog-file", changelog.is_file(), "CHANGELOG.md is missing"):
        return
    sections = parse_changelog(changelog.read_text(encoding="utf-8"))

    unreleased = [section for section in sections if section.label == "Unreleased"]
    if gate.check(
        "changelog-unreleased", len(unreleased) == 1, "CHANGELOG.md needs exactly one Unreleased section"
    ):
        gate.check(
            "changelog-unreleased-empty",
            not unreleased[0].entries,
            "the Unreleased section must stay empty once the release is written",
        )

    released = [
        (index, section)
        for index, section in enumerate(sections)
        if section.label not in ("Unreleased", "Unreleased]")
    ]
    matching = [(index, section) for index, section in released if section.label == version]
    if not gate.check(
        "changelog-version-section",
        len(matching) == 1,
        f"CHANGELOG.md needs exactly one '## [{version}]' section",
    ):
        return
    index, section = matching[0]
    if released and released[0][0] != index:
        gate.fail(
            "changelog-order",
            f"'## [{version}]' must be the first released section, below Unreleased",
        )
    else:
        gate.check("changelog-order", True)

    gate.check(
        "changelog-date",
        bool(section.date and DATE_RE.match(section.date)),
        f"'## [{version}]' needs a ' - YYYY-MM-DD' date, found {section.date!r}",
    )

    bad_groups = sorted({group for group, _ in section.entries if group not in ALLOWED_GROUPS})
    gate.check(
        "changelog-groups",
        not bad_groups,
        f"entries must sit under one of {', '.join(ALLOWED_GROUPS)}; found {', '.join(bad_groups)}",
    )

    try:
        commits = read_commits(root / "release" / "commits.tsv")
    except (OSError, ValueError) as error:
        gate.fail("changelog-commit-log", str(error))
        return
    by_hash = {commit.hash: commit for commit in commits}

    cited = []
    malformed = []
    unknown = []
    for group, line in section.entries:
        match = ENTRY_RE.match(line)
        if not match:
            malformed.append(line)
            continue
        commit_hash = match.group("hash")
        if commit_hash not in by_hash:
            unknown.append(commit_hash)
            continue
        cited.append((group, by_hash[commit_hash]))

    gate.check(
        "changelog-citation-format",
        not malformed,
        "every entry must end with its hash in parentheses, e.g. '- Text (a1b2c3d)': "
        + "; ".join(malformed[:3]),
    )
    gate.check(
        "changelog-citation-known",
        not unknown,
        "cited hashes missing from release/commits.tsv: " + ", ".join(sorted(set(unknown))),
    )

    seen = {}
    for group, commit in cited:
        seen.setdefault(commit.hash, []).append(group)
    duplicates = sorted(commit_hash for commit_hash, groups in seen.items() if len(groups) > 1)
    gate.check("changelog-no-duplicates", not duplicates, "cited twice: " + ", ".join(duplicates))

    missing = [
        f"{commit.hash} ({commit.subject})"
        for commit in commits
        if commit.release_worthy and commit.hash not in seen
    ]
    gate.check(
        "changelog-complete",
        not missing,
        "release-worthy commits not recorded: " + ", ".join(missing),
    )

    skipped = [
        f"{commit.hash} ({commit.subject})"
        for commit_hash, groups in seen.items()
        for commit in [by_hash[commit_hash]]
        if not commit.release_worthy
    ]
    gate.check(
        "changelog-only-release-worthy",
        not skipped,
        "only feat, fix and breaking commits are recorded; cited: " + "; ".join(sorted(skipped)),
    )

    misgrouped = []
    for group, commit in cited:
        if commit.breaking:
            required = "Changed"
        else:
            required = REQUIRED_GROUPS.get(commit.type)
        if required and group != required:
            misgrouped.append(f"{commit.hash} ({commit.type}) under {group}, expected {required}")
    gate.check(
        "changelog-grouping",
        not misgrouped,
        "; ".join(misgrouped),
    )


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Release gate for the ledger monorepo")
    parser.add_argument("--root", default=".", help="workspace root (default: .)")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    gate = Gate()
    if not (root / "packages").is_dir():
        gate.fail("workspace", f"{root} does not look like the ledger release workspace")
    elif not (root / "VERSION").is_file():
        gate.fail("workspace", f"{root}/VERSION is missing")
    else:
        target_version = collect_versions(root, gate)
        if target_version is not None:
            collect_changelog(root, gate, target_version)
    return gate.report()


if __name__ == "__main__":
    sys.exit(main())
