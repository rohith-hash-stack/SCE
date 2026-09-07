"""Multi-language smoke tests. JavaScript/TypeScript/Go get full Stage 1
definition collection and best-effort Stage 2 linking (Rules B/C/D, no
constructor-based instance binding - see concrete_builder module docstring)
rather than Python's full precision. These tests only assert the pipeline
runs cleanly end to end and recovers the definitions/edges that Rules B-D
can be expected to resolve, not perfect equivalence with the Python path.
"""
import os

from prism.cli import build_pipeline

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def test_javascript_definitions_and_import_call_resolve():
    builder, tag_matrix = build_pipeline(os.path.join(FIXTURES_DIR, "js_repo"))
    names = set(builder.symbol_table.all_qualified_names())

    assert "src.auth.verifySession" in names
    assert "src.checkout.CheckoutController" in names
    assert "src.checkout.CheckoutController.processCheckout" in names

    # Rule B: bare `verifySession(token)` resolved via the named import.
    assert builder.graph.has_edge(
        "src.checkout.CheckoutController.processCheckout", "src.auth.verifySession"
    )


def test_go_definitions_collected_without_crashing():
    builder, tag_matrix = build_pipeline(os.path.join(FIXTURES_DIR, "go_repo"))
    names = set(builder.symbol_table.all_qualified_names())

    assert "main.verifySession" in names
    assert "main.main" in names
