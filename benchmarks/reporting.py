"""Small ASCII-table reporting helpers shared by every benchmark CLI."""
from __future__ import annotations


def shorten(text: str, max_len: int = 52) -> str:
    if len(text) <= max_len:
        return text
    keep = max_len - 1
    return "…" + text[-keep:]


def format_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt_row(cells: list[str]) -> str:
        return "| " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)) + " |"

    separator = "+-" + "-+-".join("-" * w for w in widths) + "-+"
    lines = [separator, fmt_row(headers), separator]
    lines.extend(fmt_row(row) for row in rows)
    lines.append(separator)
    return "\n".join(lines)
