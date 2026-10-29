# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Documented artifact hashes must match the artifacts on disk.

Several evidence documents quote `sha256  filename` lines so a reader can verify
that the numbers came from the artifact they were measured on. This test makes
those quotes self-checking: when the (gitignored) artifact directory is present,
every quoted hash must match. A clean clone without the artifacts skips, because
there is nothing to verify there.
"""

import hashlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs" / "evaluation"
SHA_LINE = re.compile(r"^([0-9a-f]{64})\s{2}(\S+)$")
INLINE_SHA = re.compile(r"`([^`\s]+)`\s+sha256\s+`([0-9a-f]{64})`")
DIR_MENTION = re.compile(r"local/workpackages/([A-Za-z0-9._-]+)/")


def _documented_hashes(markdown: str):
    inside_fence = False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            inside_fence = not inside_fence
            continue
        if inside_fence:
            match = SHA_LINE.match(line.strip())
            if match:
                yield match.group(1), match.group(2)
        else:
            # Inline form: `file` sha256 `<hex>`
            for name, digest in INLINE_SHA.findall(line):
                yield digest, name


def test_documented_artifact_hashes_match_disk():
    problems = []
    checked = 0
    for path in sorted(DOCS.glob("*.md")):
        markdown = path.read_text(encoding="utf-8")
        expected_hashes = list(_documented_hashes(markdown))
        if not expected_hashes:
            continue
        candidates = [ROOT / "local" / "workpackages" / name
                      for name in DIR_MENTION.findall(markdown)]
        candidates = [candidate for candidate in candidates if candidate.is_dir()]
        if not candidates:
            continue
        for expected, filename in expected_hashes:
            found = None
            for candidate in candidates:
                target = candidate / filename
                if target.is_file():
                    found = target
                    break
            if found is None:
                continue
            actual = hashlib.sha256(found.read_bytes()).hexdigest()
            checked += 1
            if actual != expected:
                problems.append(f"{path.name}: {filename} documented={expected[:12]}… "
                                f"actual={actual[:12]}…")
    if checked == 0:
        pytest.skip("no artifact directories present in this checkout")
    assert not problems, "documented artifact hashes drifted:\n" + "\n".join(problems)
