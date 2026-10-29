"""Notification-service workload: render templates, simulate a token-bucket
rate limiter and de-duplicate notification batches.

Standard library only; all recipients are synthetic and generated at import
time. The workload performs no I/O and no network access.
"""

from __future__ import annotations

TEMPLATE = "Hello {name}, your {kind} report for {period} is ready. Reference: {ref}."
KINDS = ("weekly", "monthly", "incident")

RECIPIENTS = [
    {
        "name": f"user{index % 50:03d}",
        "kind": KINDS[index % len(KINDS)],
        "period": f"2026-W{index % 40 + 1:02d}",
        "ref": f"RPT-{index:04d}",
    }
    for index in range(200)
]


class TokenBucket:
    def __init__(self, capacity, refill_per_second):
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.tokens = float(capacity)
        self.updated = 0.0

    def allow(self, now, cost=1.0):
        elapsed = max(0.0, now - self.updated)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
        self.updated = now
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False


def render(recipient):
    return TEMPLATE.format(**recipient)


def render_all(recipients=RECIPIENTS):
    return [render(recipient) for recipient in recipients]


def simulate_bucket(steps=500, interval=0.1):
    bucket = TokenBucket(capacity=20, refill_per_second=5.0)
    accepted = 0
    now = 0.0
    for _ in range(steps):
        if bucket.allow(now):
            accepted += 1
        now += interval
    return accepted


def dedupe(recipients):
    seen = set()
    unique = []
    for recipient in recipients:
        key = (recipient["name"], recipient["period"])
        if key not in seen:
            seen.add(key)
            unique.append(recipient)
    return unique


def check():
    message = render(RECIPIENTS[0])
    if RECIPIENTS[0]["name"] not in message or RECIPIENTS[0]["ref"] not in message:
        raise AssertionError("rendered message is missing recipient fields")
    accepted = simulate_bucket()
    if not 200 < accepted < 320:
        raise AssertionError(f"token bucket accepted an implausible count: {accepted}")
    unique = dedupe(RECIPIENTS * 2)
    if len(unique) != len(RECIPIENTS):
        raise AssertionError("dedupe did not collapse repeated batches")
    return True


CASES = {
    "render_recipients_100x": lambda: [render_all() for _ in range(100)],
    "bucket_simulation_100x": lambda: [simulate_bucket() for _ in range(100)],
    "dedupe_recipients_1000x": lambda: [dedupe(RECIPIENTS * 2) for _ in range(1000)],
}
