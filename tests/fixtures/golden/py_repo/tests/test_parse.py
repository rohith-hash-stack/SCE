from shelf import parse
from tests.helpers import clean


def test_parse_two_lines():
    assert parse("a,b\nc") == [["a", "b"], ["c"]]


def test_clean_reaches_normalize_through_a_helper():
    assert clean("  A ") == "a"
