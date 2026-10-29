"""Small helpers the gate uses to read handoff artifacts."""

VERDICT_PREFIX = "VERDICT:"
APPROVE = "VERDICT: APPROVE"


def verdict_line(text: str) -> str:
    """Return the last verdict line in a handoff artifact, or an empty string."""
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped.startswith(VERDICT_PREFIX):
            return stripped
    return ""


def tally_verdicts(texts) -> int:
    """Count APPROVE verdicts across handoff artifacts."""
    return sum(1 for text in texts if verdict_line(text) == APPROVE)
