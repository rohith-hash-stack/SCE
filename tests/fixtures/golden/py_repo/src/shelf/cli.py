"""Command-line entry point (pyproject.toml: shelf = "shelf.cli:main")."""
import sys

from shelf import parse
from shelf.core.parse import parse as p
from shelf.core.fmt import render


def main(argv=None):
    """Parse standard input and print it rendered."""
    argv = sys.argv[1:] if argv is None else argv
    records = parse(sys.stdin.read())
    print(render(records))
    return 0


def check(text):
    """Parse text through the alias import; True when it has records."""
    return bool(p(text))
