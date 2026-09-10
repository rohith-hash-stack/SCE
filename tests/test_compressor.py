"""Stage 4 AST compressor tests (HLD section 4.3 / Issue #10's 4-tier
model): L0 is untouched source, L1 (Pruned) strips math/logging/docstrings
while keeping guards/calls/raises *with their real arguments*, L2
(Skeleton) additionally collapses call arguments to `...` and appends a
signature + contract comment block, L3 (Interface) is a one-line alias.
"""
import ast

from prism.slicer.compressor import ASTCompressor, CompressionContext


def _compress(builder, tag_matrix, qualified_name, resolution):
    symbol = builder.symbol_table.get(qualified_name)
    parsed = builder.parsed_file(symbol.file)
    source = parsed.source.decode("utf-8")
    name = qualified_name.rsplit(".", 1)[-1]
    callees = sorted(builder.graph.successors(qualified_name)) if qualified_name in builder.graph else []
    context = CompressionContext(tags=tag_matrix.get(qualified_name, set()), callees=callees)
    return ASTCompressor().compress(symbol.language_id, source, name, symbol.line_range, resolution, context)


def test_l0_is_byte_identical_to_source_slice(built_repo):
    builder, tag_matrix = built_repo
    qname = "src.services.billing.PaymentProcessor.charge"
    content = _compress(builder, tag_matrix, qname, 0)
    assert "requests.post(" in content
    assert '"https://gateway/charge"' in content
    assert "json={" in content


def test_l1_pruned_strips_math_and_logging_but_keeps_real_arguments(built_repo):
    """Level 1 (Pruned, Issue #10): control flow, calls *with their real
    arguments*, and assignments survive; docstrings/logging/pure-math
    intermediates are stripped - the opposite of the pre-#10 behavior,
    which erased every call's arguments uniformly regardless of distance."""
    builder, tag_matrix = built_repo
    content = _compress(builder, tag_matrix, "src.services.pricing.compute_total", 1)

    # Guard and raise survive, with the raise's real message intact
    # (ast.unparse normalizes string literals to single quotes).
    assert "if tax_rate < 0:" in content
    assert "raise ValueError('tax rate cannot be negative')" in content
    # Logging call is gone entirely.
    assert "logger" not in content
    # Pure-math assignments (no call on the RHS) are gone.
    assert "subtotal = subtotal + item.price" not in content
    assert "tax = subtotal * tax_rate" not in content
    assert "total = subtotal + tax" not in content
    # A call's real arguments are preserved, not collapsed to `...`.
    assert "return round(total, 2)" in content
    # The result must still be syntactically valid Python.
    ast.parse(content)


def test_l1_pruned_preserves_real_arguments_on_multi_arg_calls(built_repo):
    builder, tag_matrix = built_repo
    content = _compress(builder, tag_matrix, "src.services.billing.PaymentProcessor.charge", 1)
    assert "self.client.is_healthy()" in content
    assert "requests.post('https://gateway/charge', json={'customer_id': customer_id, 'amount': amount})" in content
    assert "raise GatewayTimeoutError('gateway down')" in content
    ast.parse(content)


def test_l2_skeleton_strips_arguments_and_appends_contract_block(built_repo):
    """Level 2 (Skeleton, Issue #10): the arg-stripped control-flow shape
    (what pre-#10 Level 1 rendered) plus a trailing signature/tags/raises
    annotation - not just a bare signature line."""
    builder, tag_matrix = built_repo
    content = _compress(builder, tag_matrix, "src.repositories.orders.OrderRepository.mark_paid", 2)
    assert "def mark_paid(self, cart_id, receipt_id):" in content
    # Control-flow shape retained, arguments collapsed.
    assert "order = self.session.query(...)" in content
    assert "raise OrderNotFoundError(...)" in content
    assert "# Tags: [#db_read, #db_write]" in content
    assert "# Raises: OrderNotFoundError" in content
    ast.parse(content)


def test_l3_is_a_single_line_alias(built_repo):
    builder, tag_matrix = built_repo
    content = _compress(builder, tag_matrix, "src.auth.jwt.verify_session", 3)
    assert content == "def verify_session(token): ..."
    assert "\n" not in content
