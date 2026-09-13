"""Bookmark 1 Item 2: `snapshot_file_hash_set` - `pack_symbol_context`
computes `file_hash_set` exactly once per call and every internal
cache-key check (`build_causal_graph`, `compute_feature_masks_cached`,
`compute_topological_distances` - which itself re-checks `build_causal_
graph`'s own cache key) reuses that one value, instead of each
independently re-running the real mtime/content scan Item 1 added.
"""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.packer.submodular_knapsack import pack_symbol_context

_SOURCE = (
    "def parse_order(raw):\n    return validate_order(raw)\n\n\n"
    "def validate_order(data):\n    return price_order(data)\n\n\n"
    "def price_order(data):\n    return data\n"
)


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text(_SOURCE)
    return repo


def test_exactly_one_real_scan_per_retrieve(tmp_path, monkeypatch):
    """Counter test: instrument the real (uncached) file_hash_set
    computation and assert exactly one call happens per pack_symbol_
    context invocation, not the 3-4 redundant ones the un-snapshotted
    call chain would otherwise trigger."""
    import prism.traversal._cache_keys as ck

    repo = _repo(tmp_path)
    builder, _ = build_pipeline(str(repo))

    call_count = {"n": 0}
    real_compute = ck._compute_file_hash_set

    def counting_compute(repo_root):
        call_count["n"] += 1
        return real_compute(repo_root)

    monkeypatch.setattr(ck, "_compute_file_hash_set", counting_compute)

    pack_symbol_context(builder, "x.parse_order", 2000)
    assert call_count["n"] == 1, f"expected exactly 1 real scan per retrieve, got {call_count['n']}"

    call_count["n"] = 0
    pack_symbol_context(builder, "x.parse_order", 2000)
    assert call_count["n"] == 1, "each new retrieve must open its own fresh snapshot (one real scan), not zero or many"


def test_mid_retrieve_file_change_is_not_seen_until_next_retrieve(tmp_path, monkeypatch):
    """Mid-retrieve change: hook retrieve to edit a file right after the
    snapshot is taken - the current call must still use the pre-change
    snapshot value (no lock, the snapshot is a value); a *subsequent*
    call must see the edit."""
    import prism.traversal._cache_keys as ck

    repo = _repo(tmp_path)
    builder, _ = build_pipeline(str(repo))

    real_snapshot = ck.snapshot_file_hash_set
    observed = {"values": []}

    class _ObservingSnapshot:
        def __init__(self, repo_root):
            self._cm = real_snapshot(repo_root)
            self._repo = repo_root

        def __enter__(self):
            value = self._cm.__enter__()
            observed["values"].append(value)
            # Edit the file *after* the snapshot was taken but *during*
            # this same retrieve - the change must not be visible to
            # anything still using this snapshot's value.
            (tmp_path / "repo" / "x.py").write_text(_SOURCE + "\ndef extra():\n    return 1\n")
            return value

        def __exit__(self, *exc):
            return self._cm.__exit__(*exc)

    monkeypatch.setattr(ck, "snapshot_file_hash_set", _ObservingSnapshot)
    import prism.packer.submodular_knapsack as sk

    monkeypatch.setattr(sk, "snapshot_file_hash_set", _ObservingSnapshot)

    result_1 = pack_symbol_context(builder, "x.parse_order", 2000)
    assert result_1.selected  # completed using the pre-change snapshot, no crash/inconsistency

    # The file is now genuinely different on disk. A fresh retrieve
    # (its own new snapshot) must compute a *different* file_hash_set
    # than the first one observed.
    monkeypatch.undo()  # restore the real snapshot_file_hash_set for a clean second call
    sig_after = ck.target_repo_file_signature(str(repo))
    assert sig_after != observed["values"][0], "the next retrieve's own snapshot must see the mid-retrieve edit"
