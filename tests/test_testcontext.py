"""Context for writing tests (`prism.impact.testcontext`)."""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.impact.testcontext import TestContextBuilder, render

LIB = '''class PortError(ValueError):
    pass


def parse_port(text):
    if not text.isdigit():
        raise PortError(text)
    return int(text)


def build_url(host, port):
    return f"http://{host}:{parse_port(port)}"
'''

TESTS = '''import pytest

from app.lib import PortError, parse_port, build_url


def test_parse_port_number():
    assert parse_port("80") == 80


def test_parse_port_rejects_words():
    with pytest.raises(PortError):
        parse_port("http")


@pytest.mark.parametrize("host", ["a", "b"])
def test_build_url(host, tmp_path):
    assert build_url(host, "80").endswith(":80")


def test_unrelated(tmp_path):
    assert 1 + 1 == 2
'''


def test_test_context_has_target_callers_closest_tests_and_conventions(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "lib.py").write_text(LIB)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_lib.py").write_text(TESTS)
    b = build_pipeline(str(tmp_path), use_cache=False)[0]
    ctx = TestContextBuilder(b, str(tmp_path)).build("app.lib.parse_port", "tests/test_lib.py")
    text = render(ctx)
    assert "raise PortError" in ctx.target_code and "app.lib.build_url" in text
    assert [q.rsplit(".", 1)[-1] for q, _c in ctx.examples] == ["test_parse_port_number", "test_parse_port_rejects_words"]
    assert any("pytest.raises" in c for c in ctx.conventions) and any("parametrize" in c for c in ctx.conventions)
    assert any("`tmp_path` (2)" in c for c in ctx.conventions)
