"""Parsing records from text."""

SEPARATOR = ","


def normalize(line):
    """Strip whitespace and lower-case one line."""
    return line.strip().lower()


def tokenize(line):
    """Split one normalized line into fields."""
    return [field for field in normalize(line).split(SEPARATOR) if field]


def parse(text):
    """Parse text into a list of records; blank text gives an empty list."""
    if text is None:
        raise ValueError("text is required")
    if not text.strip():
        return []
    return [tokenize(line) for line in text.splitlines() if line.strip()]
