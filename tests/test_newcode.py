"""Context for writing a new function (`prism.impact.newcode`)."""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.impact.newcode import NewFunctionContextBuilder, render

CACHE = '''class CacheError(Exception):
    pass


class Cache:
    def __init__(self, store):
        self.store = store
        self.hits = 0

    def get_entry(self, key):
        """Return the cached entry for key, or None."""
        value = self.store.get(key)
        if value is not None:
            self.hits += 1
        return value

    def set_entry(self, key, value):
        """Store value under key."""
        if key is None:
            raise CacheError("no key")
        self.store[key] = value


def normalize_key(key):
    """Lower-case and strip a cache key."""
    return key.strip().lower()


def unrelated_math(a, b):
    return a * b + a - b
'''


def _ctx(tmp_path, **kw):
    (tmp_path / "cache.py").write_text(CACHE)
    b = build_pipeline(str(tmp_path), use_cache=False)[0]
    return NewFunctionContextBuilder(b, str(tmp_path)).build("cache.py", **kw)


def test_new_method_context_lists_class_members_helpers_and_style(tmp_path):
    ctx = _ctx(tmp_path, name="get_or_set_entry", cls="cache.Cache",
               signature="def get_or_set_entry(self, key, default):",
               purpose="Return the cached entry for a normalized key, storing default when it is missing.")
    text = render(ctx)
    assert "## 2. Class `cache.Cache`" in text and "`hits`" in text and "`store`" in text
    names = [h.qname for h in ctx.helpers]
    assert {"cache.Cache.get_entry", "cache.Cache.set_entry", "cache.normalize_key"} <= set(names)
    assert [q for q, _f, _c in ctx.siblings][0] in ("cache.Cache.get_entry", "cache.Cache.set_entry")
    assert "CacheError" in text  # the helpers' digests say what they raise
    assert ctx.token_count > 0


def test_new_module_function_has_no_class_section(tmp_path):
    ctx = _ctx(tmp_path, name="clear_key", purpose="Normalize a key and drop it from the store.")
    assert ctx.cls is None and "## 2. Class" not in render(ctx)
    assert "cache.normalize_key" in [h.qname for h in ctx.helpers]
