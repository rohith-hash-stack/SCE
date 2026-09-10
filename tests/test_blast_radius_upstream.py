"""Verification suite for Prism v1.1+'s Bidirectional Blast-Radius
Slicing (`prism.packer.blast_radius`, wired into `prism.packer.
submodular_knapsack`): a seed's *upstream* consumers - direct callers in
other files who rely on its return-value contract - must be surfaced as
real, weighted candidates, not just its downstream dependencies.

The spec's own scenario: `calculate_tax()` as the seed, with
`invoice_generator()` in a separate file doing `total =
calculate_tax(...)` - a real cross-file consumer that a forward-only
slicer would never show an agent editing `calculate_tax()`'s signature.
"""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.packer.blast_radius import CONTRACT_PRESERVATION_MULTIPLIER, compute_upstream_callers
from prism.packer.submodular_knapsack import pack_symbol_context


def _blast_radius_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tax.py").write_text("def calculate_tax(amount):\n    return amount * 0.2\n")
    (repo / "invoice.py").write_text(
        "from tax import calculate_tax\n\n\n"
        "def invoice_generator(amount):\n"
        "    total = calculate_tax(amount)\n"
        "    return total\n"
    )
    return repo


def test_invoice_generator_is_a_direct_upstream_caller_of_calculate_tax(tmp_path):
    repo = _blast_radius_repo(tmp_path)
    builder, _ = build_pipeline(str(repo))
    callers = compute_upstream_callers(builder, "tax.calculate_tax")
    assert "invoice.invoice_generator" in callers
    caller = callers["invoice.invoice_generator"]
    assert caller.unpacks_return is True
    assert caller.dist_w_upstream <= 1.5


def test_invoice_generator_is_pulled_into_packed_context_under_normal_budget(tmp_path):
    """The spec's own assertion: under a normal budget, the upstream
    blast-radius protectee is actually admitted into the packed
    context - not merely computed and discarded."""
    repo = _blast_radius_repo(tmp_path)
    builder, _ = build_pipeline(str(repo))
    result = pack_symbol_context(builder, "tax.calculate_tax", target_budget=200)
    assert "invoice.invoice_generator" in result.selected


def test_mandatory_upstream_protection_holds_at_the_tightest_viable_budget(tmp_path):
    """The packer's own "must guarantee" requirement, exercised at its
    tightest real edge: a budget sized to exactly the seed's own cost
    plus the caller's own cost (measured from a generous run first, so
    this isn't a guessed magic number) - the caller must still be
    admitted, not just likely to be."""
    repo = _blast_radius_repo(tmp_path)
    builder, _ = build_pipeline(str(repo))
    generous = pack_symbol_context(builder, "tax.calculate_tax", target_budget=500)
    seed_item = next(i for i in generous.items if i.symbol == "tax.calculate_tax")
    caller_item = next(i for i in generous.items if i.symbol == "invoice.invoice_generator")
    tight_budget = seed_item.cost + caller_item.cost

    result = pack_symbol_context(builder, "tax.calculate_tax", target_budget=tight_budget)
    assert "invoice.invoice_generator" in result.selected


def test_upstream_caller_omitted_when_budget_cannot_fit_even_the_seed_and_caller(tmp_path):
    """The guarantee is bounded by the budget itself, never overridden by
    it - one token below the tightest viable budget, the caller is
    correctly left out rather than admitted over budget."""
    repo = _blast_radius_repo(tmp_path)
    builder, _ = build_pipeline(str(repo))
    generous = pack_symbol_context(builder, "tax.calculate_tax", target_budget=500)
    seed_item = next(i for i in generous.items if i.symbol == "tax.calculate_tax")
    caller_item = next(i for i in generous.items if i.symbol == "invoice.invoice_generator")
    under_budget = seed_item.cost + caller_item.cost - 1

    result = pack_symbol_context(builder, "tax.calculate_tax", target_budget=under_budget)
    assert "invoice.invoice_generator" not in result.selected


def test_caller_that_discards_the_return_value_is_not_contract_preserving(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tax.py").write_text("def calculate_tax(amount):\n    return amount * 0.2\n")
    (repo / "invoice.py").write_text(
        "from tax import calculate_tax\n\n\ndef log_call(amount):\n    calculate_tax(amount)\n"
    )
    builder, _ = build_pipeline(str(repo))
    callers = compute_upstream_callers(builder, "tax.calculate_tax")
    assert "invoice.log_call" in callers
    assert callers["invoice.log_call"].unpacks_return is False
    # a bare identifier argument (amount) is still a real, non-literal
    # value - non-trivial by this module's own conservative definition.
    assert callers["invoice.log_call"].supplies_nontrivial_args is True


def test_caller_supplying_only_literal_arguments_is_not_flagged_nontrivial(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tax.py").write_text("def calculate_tax(amount):\n    return amount * 0.2\n")
    (repo / "invoice.py").write_text(
        "from tax import calculate_tax\n\n\ndef fixed_call():\n    calculate_tax(100)\n"
    )
    builder, _ = build_pipeline(str(repo))
    callers = compute_upstream_callers(builder, "tax.calculate_tax")
    assert callers["invoice.fixed_call"].supplies_nontrivial_args is False


def test_contract_preserving_upstream_candidate_outranks_a_non_preserving_one(tmp_path):
    """The Consumer Contract Scoring Boost actually changes ranking, not
    just bookkeeping: two callers with identical topology/cost, only one
    of which unpacks the return value, and the packer admits the
    return-unpacking one first when the budget can only fit one."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tax.py").write_text("def calculate_tax(amount):\n    return amount * 0.2\n")
    (repo / "a_binder.py").write_text(
        "from tax import calculate_tax\n\n\ndef a_binder(amount):\n    total = calculate_tax(amount)\n    return total\n"
    )
    (repo / "b_discarder.py").write_text(
        "from tax import calculate_tax\n\n\ndef b_discarder(amount):\n    calculate_tax(amount)\n"
    )
    builder, _ = build_pipeline(str(repo))
    callers = compute_upstream_callers(builder, "tax.calculate_tax")
    assert callers["a_binder.a_binder"].weight > callers["b_discarder.b_discarder"].weight
    assert CONTRACT_PRESERVATION_MULTIPLIER > 1.0


def test_no_upstream_callers_when_seed_is_never_called(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tax.py").write_text("def calculate_tax(amount):\n    return amount * 0.2\n")
    builder, _ = build_pipeline(str(repo))
    callers = compute_upstream_callers(builder, "tax.calculate_tax")
    assert callers == {}
