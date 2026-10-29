"""CSV and table renderers.

Both renderers take a header row and data rows of strings. Numeric columns are
right-aligned in the table renderer and padded to a readable width.
"""

from __future__ import annotations

from typing import Iterable, Sequence


def _quote(value: str) -> str:
    if any(character in value for character in ',"\n'):
        return '"' + value.replace('"', '""') + '"'
    return value


def render_csv(rows: Iterable[Sequence[str]]) -> str:
    """Render ``rows`` as CSV, quoting fields that need it."""
    lines = []
    for row in rows:
        lines.append(",".join(_quote(str(field)) for field in row))
    return "\n".join(lines)


def _widths(rows: Sequence[Sequence[str]], column_count: int) -> list:
    widths = [0] * column_count
    for row in rows:
        for index, field in enumerate(row):
            widths[index] = max(widths[index], len(str(field)))
    return widths


def render_table(rows: Sequence[Sequence[str]], numeric_columns: Iterable[int] = ()) -> str:
    """Render ``rows`` as a fixed-width table.

    The first row is treated as the header. Columns listed in
    ``numeric_columns`` are right-aligned and padded three spaces wide.
    """
    if not rows:
        return ""
    numeric = set(numeric_columns)
    column_count = max(len(row) for row in rows)
    widths = _widths(rows, column_count)
    lines = []
    for row in rows:
        cells = []
        for index in range(column_count):
            field = str(row[index]) if index < len(row) else ""
            if index in numeric:
                cells.append(field.rjust(widths[index] + 3))
            else:
                cells.append(field.ljust(widths[index]))
        lines.append("  ".join(cells).rstrip())
    return "\n".join(lines)
