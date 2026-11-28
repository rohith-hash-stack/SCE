"""Rendering records as text."""


def render(records, width=20):
    """Render records as fixed-width lines."""
    return "\n".join(" ".join(field.ljust(width) for field in record) for record in records)
