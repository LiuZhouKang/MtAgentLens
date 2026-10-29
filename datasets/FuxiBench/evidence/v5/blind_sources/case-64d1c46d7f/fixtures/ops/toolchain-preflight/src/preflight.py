"""Risk classification for candidate sudoers templates.

The preflight audit reads a drop-in template line by line and labels each
active directive so the report can cite Severity values without hand review.
Labels follow the audit procedure in ``docs.md``:

- ``critical`` -- sudoedit with glob arguments (CVE-2023-22809 class), or an
  env_keep entry that lets the caller inject an editor or loader variable.
- ``high``     -- NOPASSWD granting an unrestricted command list.
- ``medium``   -- NOPASSWD scoped to concrete commands.
- ``low``      -- every other active directive.
- ``ignore``   -- blank lines and comments.
"""

import re

RISKY_ENV = (
    "EDITOR",
    "VISUAL",
    "SUDO_EDITOR",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "PYTHONPATH",
)

_SUDOEDIT = re.compile(r"\bsudoedit\b")
_GLOB = re.compile(r"[*?]")
_ENV_KEEP = re.compile(r"env_keep\s*[+-]?=\s*\"?([^\"]*)\"?")


def _env_keep_hits(directive):
    """Return risky variables named in an env_keep list, longest match first."""
    match = _ENV_KEEP.search(directive)
    if not match:
        return []
    names = match.group(1).replace(",", " ").split()
    hits = []
    for name in RISKY_ENV:
        if any(token == name or token.startswith(name) for token in names):
            hits.append(name)
    return hits


def classify(directive):
    """Return the risk label for one sudoers directive line."""
    stripped = directive.strip()
    if not stripped or stripped.startswith("#"):
        return "ignore"

    if stripped.startswith("Defaults") and "env_keep" in stripped:
        return "critical" if _env_keep_hits(stripped) else "low"

    if _SUDOEDIT.search(stripped) and _GLOB.search(stripped):
        return "critical"

    if "NOPASSWD" in stripped:
        spec = stripped.split("=", 1)[-1]
        if "ALL" in spec:
            return "high"
        return "medium"

    return "low"


def summarise_template(text):
    """Count active directives per risk label."""
    counts = {}
    for line in text.splitlines():
        label = classify(line)
        if label == "ignore":
            continue
        counts[label] = counts.get(label, 0) + 1
    return counts
