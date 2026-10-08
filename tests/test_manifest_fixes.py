"""Candidate-manifest fixes: full declaration headers (multi-line, capped,
pipe-safe), the `lines=N` field, and hop-count admission of downstream
candidates reached through best-effort call links."""
import textwrap

from prism.cli import build_pipeline
from prism.packer.candidate_index import (
    MANIFEST_SIGNATURE_MAX_TOKENS,
    SIGNATURE_TRUNCATED_MARK,
    _full_declaration,
    build_candidate_manifest,
)
from prism.slicer.tokenizer import count_tokens


def _repo(tmp_path, files: dict[str, str]):
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
    builder, _ = build_pipeline(str(tmp_path), use_cache=False)
    return builder


def _rows(manifest: str) -> dict[str, list[str]]:
    return {line.split("|")[0]: line.split("|") for line in manifest.split("\n")[1:-1]}


def test_multiline_python_signature_is_joined_whole(tmp_path):
    b = _repo(tmp_path, {"pkg/__init__.py": "", "pkg/m.py": """
        def seed(
            first: int,
            second: str = "x",
        ) -> dict:
            return helper(first)
        def helper(v):
            return v
    """})
    assert _full_declaration(b, "pkg.m.seed") == 'def seed( first: int, second: str = "x", ) -> dict:'


def test_multiline_typescript_signature_is_joined_and_pipe_safe(tmp_path):
    b = _repo(tmp_path, {"src/a.ts": """
        export function assertId(
          obj: unknown,
        ): asserts obj is number | string | null {
          if (obj === undefined) throw new Error('x');
        }
    """})
    sig = _full_declaration(b, "src.a.assertId")
    assert sig == "export function assertId( obj: unknown, ): asserts obj is number ¦ string ¦ null"
    manifest, _ = build_candidate_manifest(b, "src.a.assertId")
    row = _rows(manifest)["src.a.assertId"]
    assert len(row) == 6 and row[3] == sig            # the union's pipes never split the row


def test_over_cap_signature_is_cut_and_marked_after_collapsing_long_strings(tmp_path):
    params = ", ".join(f'p{i}: str = "a long default value number {i} that keeps going"' for i in range(60))
    b = _repo(tmp_path, {"pkg/__init__.py": "", "pkg/m.py": f"def big({params}):\n    return 1\n"})
    sig = _full_declaration(b, "pkg.m.big")
    assert sig.endswith(SIGNATURE_TRUNCATED_MARK)
    assert '"…"' in sig and "keeps going" not in sig
    assert count_tokens(sig[: -len(SIGNATURE_TRUNCATED_MARK)]) <= MANIFEST_SIGNATURE_MAX_TOKENS


def test_every_row_ends_with_lines_and_caller_flags_precede_it(tmp_path):
    b = _repo(tmp_path, {"pkg/__init__.py": "", "pkg/m.py": """
        def seed(x):
            return helper(x)
        def helper(x):
            y = x
            return y
        def caller():
            value = seed(1)
            return value
    """})
    manifest, _ = build_candidate_manifest(b, "pkg.m.seed")
    rows = _rows(manifest)
    assert rows["pkg.m.helper"][-1] == "lines=3" and len(rows["pkg.m.helper"]) == 6
    assert rows["pkg.m.seed"][-1] == "lines=2"
    caller = rows["pkg.m.caller"]
    assert caller[1] == "caller" and len(caller) == 8
    assert caller[5].startswith("binds_return=") and caller[6].startswith("nontrivial_args=") and caller[7] == "lines=3"


def test_hop_count_admission_keeps_a_stage_two_best_effort_hops_away(tmp_path):
    # `this.set(...)` on an untyped prototype object resolves only as a
    # best-effort (TENTATIVE_CALL) link; two of them cost more than the
    # weighted-distance cutoff, but the stage is two structural hops away.
    b = _repo(tmp_path, {"lib/app.js": """
        var app = exports = module.exports = {};
        app.init = function init() {
          this.defaultConfiguration();
        };
        app.defaultConfiguration = function defaultConfiguration() {
          this.set('etag', 'weak');
        };
        app.set = function set(setting, val) {
          return val;
        };
    """})
    edge = b.graph.get_edge_data("lib.app.defaultConfiguration", "lib.app.set") or {}
    assert edge.get("kind") == "TENTATIVE_CALL"
    _, universe = build_candidate_manifest(b, "lib.app.init")
    assert "lib.app.set" in universe


def test_signature_falls_back_to_the_declaration_line_without_a_body(tmp_path):
    b = _repo(tmp_path, {"pkg/__init__.py": "", "pkg/m.py": "LIMIT = 10\n\ndef use():\n    return LIMIT\n"})
    sym = next((s.qualified_name for s in b.symbol_table if s.kind == "attribute" and s.qualified_name.endswith("LIMIT")), None)
    assert sym is not None
    assert _full_declaration(b, sym) == "LIMIT = 10"
