"""Small host-profile helpers used by the quarterly benchmark suite."""


def parse_cpu_model(cpuinfo_text: str) -> str:
    """Return the first `model name` value from /proc/cpuinfo text."""
    for line in cpuinfo_text.splitlines():
        key, _, value = line.partition(":")
        if key.strip().lower() == "model name" and value.strip():
            return value.strip()
    return "unknown"


def parse_uptime(uptime_text: str) -> float:
    """Return the uptime in seconds, or 0.0 when the field is unreadable."""
    fields = uptime_text.split()
    if not fields:
        return 0.0
    try:
        return float(fields[0])
    except ValueError:
        return 0.0


def moving_average(samples, window: int):
    """Trailing moving average; the window widens until enough samples exist."""
    if window <= 0:
        raise ValueError("window must be positive")
    if not samples:
        return []
    result = []
    running = 0.0
    for index, value in enumerate(samples):
        running += value
        if index >= window:
            running -= samples[index - window]
        result.append(running / min(index + 1, window))
    return result


def classify_load(iterations: int, seconds: float) -> str:
    """Bucket a scalar loop into an idle/light/saturated label."""
    if seconds <= 0:
        return "unknown"
    rate = iterations / seconds
    if rate < 1_000:
        return "idle"
    if rate < 100_000:
        return "light"
    return "saturated"
