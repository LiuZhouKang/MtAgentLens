# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Per-run fixture materialisation.

Scenario fixtures used to be read in place from ``env/scenarios/fixtures``.
That has three defects for measurement:

1. Documents cannot refer to their own directory portably: several fixtures
   hard-coded ``/home/<operator>/...`` paths, so a run on another machine read a
   path that did not exist while the scenario still "succeeded".
2. Agents can write into the fixture tree (a scenario step may legitimately
   overwrite a config), so one run contaminates every later run.
3. The fixture directory name leaks the scenario identity into the visible
   filesystem path, which changes model behaviour between runs.

This module copies the fixture tree into the run directory, rewrites
``{{FIXTURE_DIR}}`` (and the absolute source prefix) in text files to the copy,
and points the agent tasks at the copy. Markers and handoff files then live in
a per-run directory that can be deleted with the run.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

FIXTURE_PLACEHOLDER = "{{FIXTURE_DIR}}"

# Files above this size are copied verbatim; they are data, not instructions.
MAX_REWRITE_BYTES = 2 * 1024 * 1024


@dataclass
class FixtureCopy:
    """Where the per-run fixture tree lives."""

    source: Path
    destination: Path | None

    @property
    def available(self) -> bool:
        return self.destination is not None


def materialize_fixtures(config, run_dir: Path) -> FixtureCopy:
    """Copy ``config.fixtures_dir`` into ``run_dir/fixtures`` and rewrite paths.

    Returns a :class:`FixtureCopy`. When the source directory does not exist
    (Type A scenarios may not need one), nothing is copied and agent tasks are
    left untouched.
    """
    source = Path(config.fixtures_dir)
    if not source.is_dir():
        return FixtureCopy(source=source, destination=None)

    destination = Path(run_dir) / "fixtures"
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, symlinks=False)

    _rewrite_tree(destination, source, destination)

    for agent in config.agents:
        agent.task = _rewrite_text(agent.task, source, destination)

    return FixtureCopy(source=source, destination=destination)


def _rewrite_tree(root: Path, source: Path, destination: Path) -> None:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > MAX_REWRITE_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rewritten = _rewrite_text(text, source, destination)
        if rewritten != text:
            path.write_text(rewritten, encoding="utf-8")


def _rewrite_text(text: str, source: Path, destination: Path) -> str:
    """Point placeholder and absolute source paths at the per-run copy."""
    text = text.replace(FIXTURE_PLACEHOLDER, str(destination))
    source_str = str(source)
    if source_str in text:
        text = text.replace(source_str, str(destination))
    return text
