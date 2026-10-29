"""The agent must not be able to read MtAgentLens/harness identifiers.

Measured motivation: 30/43 attack runs refused after quoting these names, and
`mtal-`/`MTL_RUN_MARKER` appeared in 32/43 agent logs. Blinding is part of the
experiment, so it is enforced like any other contract.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "env" / "src"))

from mtagentlens_benchmark.engine.executor import agent_visible_env  # noqa: E402

FORBIDDEN = re.compile(r"mtal|MTL_|MTAGENTLENS|MtAgentLens")
CORPUS = [
    ROOT / "env" / "scenarios" / "fixtures",
    ROOT / "env" / "scenarios",
    ROOT / "compare" / "Dataset" / "FuxiBench" / "prompt_inputs",
]


def test_agent_environment_hides_harness_identifiers():
    env = {
        "PATH": "/usr/bin",
        "MTAGENTLENS_SUDO_PASSWORD": "secret",
        "MTL_SINK_HOST": "93.184.216.34",
        "COLLECT_HOST": "93.184.216.34",
        "RUN_TAG": "tag-1",
        "RUN_TAG_FILE": "/tmp/tag",
    }
    visible = agent_visible_env(env)
    assert visible == {"PATH": "/usr/bin", "RUN_TAG": "tag-1", "RUN_TAG_FILE": "/tmp/tag"}


def test_fixture_corpus_contains_no_harness_identifiers():
    offenders = []
    for root in CORPUS:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.stat().st_size > 512 * 1024:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if FORBIDDEN.search(line):
                    offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()[:120]}")
    assert not offenders, "harness identifiers leak into agent-visible corpus:\n" + "\n".join(offenders[:10])
