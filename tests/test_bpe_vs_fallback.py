"""Issue A2: the fail-closed regex fallback (Issue A1) must never
under-count relative to real `cl100k_base` BPE on representative source
code - proven for real, against a real encoding, not asserted from
memory.

This test only runs when a real tiktoken encoding is actually loadable -
either because this environment has network access to tiktoken's remote
vocab blob, or because `TIKTOKEN_CACHE_DIR` points at a pre-populated
cache (see `tests/fixtures/tokens/README.md`). When neither is true it
skips with an explicit reason rather than silently passing on a
fabricated comparison - `prism.slicer.tokenizer.is_exact()` is the same
signal the packer itself uses to know which backend produced a count, so
this test is asking the exact question the production code path would
ask.
"""
from __future__ import annotations

import pytest

from prism.slicer.tokenizer import _fallback_count_tokens, is_exact

pytestmark = pytest.mark.skipif(
    not is_exact(),
    reason=(
        "Real tiktoken cl100k_base encoding unavailable in this "
        f"environment ({__name__} needs it to compare against - see "
        "tests/fixtures/tokens/README.md for how to populate an offline "
        "TIKTOKEN_CACHE_DIR cache)."
    ),
)

# Representative snippets across the three languages the audit named -
# real code shapes (long identifiers, camelCase/snake_case, multi-digit
# literals, operator-dense lines), not synthetic worst-cases.
_PYTHON_SNIPPET = """\
def calculate_total_order_amount(order_processing_handler, item_count=12345):
    subtotal = 0
    for lineItemIndex in range(item_count):
        subtotal += order_processing_handler.getLineItemPrice(lineItemIndex)
    return subtotal * 1.0825
"""

_TYPESCRIPT_SNIPPET = """\
function calculateTotalOffset(orderProcessingHandler: OrderHandler, itemCount: number = 12345): number {
    let subtotal = 0;
    for (let lineItemIndex = 0; lineItemIndex < itemCount; lineItemIndex++) {
        subtotal += orderProcessingHandler.getLineItemPrice(lineItemIndex);
    }
    return subtotal * 1.0825;
}
"""

_GO_SNIPPET = """\
func CalculateTotalOrderAmount(orderProcessingHandler *OrderHandler, itemCount int) float64 {
    subtotal := 0.0
    for lineItemIndex := 0; lineItemIndex < itemCount; lineItemIndex++ {
        subtotal += orderProcessingHandler.GetLineItemPrice(lineItemIndex)
    }
    return subtotal * 1.0825
}
"""


@pytest.mark.parametrize(
    "snippet",
    [_PYTHON_SNIPPET, _TYPESCRIPT_SNIPPET, _GO_SNIPPET],
    ids=["python", "typescript", "go"],
)
def test_fallback_conservatively_over_counts_relative_to_real_bpe(snippet: str) -> None:
    from prism.slicer.tokenizer import _STATE  # real encoding, guarded by skipif above

    real_bpe_tokens = len(_STATE.encoding.encode(snippet, disallowed_special=()))
    fallback_tokens = _fallback_count_tokens(snippet)
    assert fallback_tokens >= real_bpe_tokens, (
        f"fallback={fallback_tokens} real_bpe={real_bpe_tokens}: the fail-closed "
        "fallback must never under-count relative to real BPE (Issue A1's whole "
        "point - the knapsack's admission check trusts this count)"
    )
