"""Report formatting helpers for the ops-sync workspace."""


def summarise(records: list[dict]) -> str:
    """Render one status line per record, in the order given."""
    return "\n".join(f"{record['role']}: {record['status']}" for record in records)
