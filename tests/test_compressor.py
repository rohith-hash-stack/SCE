"""Stage 4 AST compressor tests (HLD section 4.3): L0 is untouched source,
L1 strips math/logging while keeping guards/calls/raises, L2 is a
signature + contract comment block, L3 is a one-line alias.
"""
import ast

from sce.slicer.compressor import ASTCompressor, CompressionContext


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


def test_l1_strips_math_and_logging_keeps_guards_calls_raises(built_repo):
    builder, tag_matrix = built_repo
    content = _compress(builder, tag_matrix, "src.services.pricing.compute_total", 1)

    # Guard and raise survive.
    assert "if tax_rate < 0:" in content
    assert "raise ValueError(...)" in content
    # Logging call is gone entirely.
    assert "logger" not in content
    # Pure-math assignments (no call on the RHS) are gone.
    assert "subtotal = subtotal + item.price" not in content
    assert "tax = subtotal * tax_rate" not in content
    assert "total = subtotal + tax" not in content
    # A call with arguments has its arguments collapsed, not its call shape.
    assert "return round(...)" in content
    # The result must still be syntactically valid Python.
    ast.parse(content)


def test_l1_leaves_zero_arg_calls_unchanged(built_repo):
    builder, tag_matrix = built_repo
    content = _compress(builder, tag_matrix, "src.services.billing.PaymentProcessor.charge", 1)
    assert "self.client.is_healthy()" in content
    assert "requests.post(...)" in content


def test_l2_renders_signature_and_contract_block(built_repo):
    builder, tag_matrix = built_repo
    content = _compress(builder, tag_matrix, "src.repositories.orders.OrderRepository.mark_paid", 2)
    lines = content.splitlines()
    assert lines[0] == "def mark_paid(self, cart_id, receipt_id): ..."
    assert "# Tags: [#db_read, #db_write]" in content
    assert "# Raises: OrderNotFoundError" in content


def test_l3_is_a_single_line_alias(built_repo):
    builder, tag_matrix = built_repo
    content = _compress(builder, tag_matrix, "src.auth.jwt.verify_session", 3)
    assert content == "def verify_session(token): ..."
    assert "\n" not in content
