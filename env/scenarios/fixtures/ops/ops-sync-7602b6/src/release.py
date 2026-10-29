def render_summary(rows: list[dict]) -> str:
    return "\n".join(f"{row['name']}: {row['status']}" for row in rows)
