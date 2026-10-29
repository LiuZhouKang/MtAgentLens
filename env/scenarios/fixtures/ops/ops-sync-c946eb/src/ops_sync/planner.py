"""Ordered release steps for reviewed changes."""


def plan_steps(review_ids):
    """Return review and verification steps interleaved per review id."""
    steps = []
    for review_id in review_ids:
        steps.append(f"review-{review_id}")
        steps.append(f"verify-{review_id}")
    return steps


def summarise(steps):
    """Return a short human-readable summary of a step list."""
    if not steps:
        return "no steps"
    return f"{len(steps)} steps: {', '.join(steps)}"
