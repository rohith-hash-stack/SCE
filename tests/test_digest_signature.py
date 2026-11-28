"""The digest records the parameter list: a signature change with an unchanged body is a digest change (A2 fix)."""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.summaries.digest import DigestBuilder


def _digest(tmp_path, name: str, src: str, qname: str):
    root = tmp_path / name
    root.mkdir()
    file = "m.py" if "def " in src else "m.ts"
    (root / file).write_text(src)
    b = build_pipeline(str(root), use_cache=False)[0]
    return DigestBuilder(b).digest(qname)


def _facts(d):
    return {(f.kind, f.text, f.when) for f in d.facts}


def test_signature_change_with_the_same_body_changes_the_digest(tmp_path):
    body = "    if size:\n        return size\n    raise ValueError(size)\n"
    before = _digest(tmp_path, "a", "def finish(self, size: int, streaming: bool) -> None:\n" + body, "m.finish")
    after = _digest(tmp_path, "b", "def finish(self, size: int) -> None:\n" + body, "m.finish")
    assert _facts(before) != _facts(after)
    assert _facts(before) - _facts(after) == {("signature", "parameters (self, size: int, streaming: bool) -> None", ())}
    assert _facts(after) - _facts(before) == {("signature", "parameters (self, size: int) -> None", ())}


def test_defaults_and_optional_markers_are_part_of_the_signature(tmp_path):
    a = _digest(tmp_path, "c", "export function f(a: number, b?: string): void { return; }\n", "m.f")
    b = _digest(tmp_path, "d", "export function f(a: number, b: string = 'x'): void { return; }\n", "m.f")
    sig = lambda d: [f.text for f in d.facts if f.kind == "signature"]
    assert sig(a) == ["parameters (a: number, b?: string) -> void"]
    assert sig(b) == ["parameters (a: number, b: string = 'x') -> void"]


def test_the_signature_survives_the_fact_budget(tmp_path):
    body = "".join(f"    x{i} = g{i}()\n" for i in range(60))
    root = tmp_path / "e"
    root.mkdir()
    (root / "m.py").write_text("def big(a, b=1):\n" + body)
    b = build_pipeline(str(root), use_cache=False)[0]
    d = DigestBuilder(b, max_facts=5).digest("m.big")
    assert d.facts[0].kind == "signature" and len(d.facts) == 6 and d.omitted  # 5 body facts + the signature
