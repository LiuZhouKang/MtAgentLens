# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Every relative link under docs/ must resolve.

Documentation is part of the deliverable: a reviewer following a link must land on
a file that exists. This test is the cheap enforcement of that promise; it does
not check external URLs (that would need the network and is out of scope for the
offline gate).
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def _links(markdown: str):
    inside_fence = False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            inside_fence = not inside_fence
            continue
        if inside_fence:
            continue
        for match in LINK.finditer(line):
            yield match.group(1)


def test_every_relative_docs_link_resolves():
    failures = []
    files = sorted(DOCS.rglob("*.md"))
    assert files, "no markdown files found"
    for path in files:
        for target in _links(path.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            cleaned = target.strip("<>").split("#", 1)[0]
            if not cleaned:
                continue
            resolved = (path.parent / cleaned).resolve()
            if not resolved.exists():
                failures.append(f"{path.relative_to(ROOT)} -> {target}")
    assert not failures, "broken relative links in docs/:\n" + "\n".join(failures[:20])
