# PRISM known bugs (deferred)

Bugs in the engine under test, found during the harness work. They are
deferred so that the benchmark's subject does not change mid-study.

## 1. Demoted overload stub loses its def-node on a fresh build

- **Found:** M4 pre-run, while investigating
  `tests/test_index_cache_consistency.py::test_cache_hit_rehydrates_every_def_node`.
  The test is marked `xfail(strict=True)` with this reference.
- **Status:** fix deferred to post-M4.

`symbol_table.add()` renames a demoted overload stub to `#N` but does not
move its def-node entry. The steps:

1. A stub (`pass`/`...` body, role INTERFACE) is registered first under the
   canonical name.
2. A later real definition with the same qualified name promotes into the
   canonical slot (`src/prism/graph/symbol_table.py:212`, `227–229`). The
   stub is renamed to `<name>#2`.
3. `ConcreteGraphBuilder` then writes `self._def_nodes[key] = node` with the
   key `add()` returned, which is the canonical one
   (`src/prism/graph/concrete_builder.py:602–603`).

On a fresh build, the stub's def-node is overwritten under the canonical
key. The real definition ends up with its own, correct node, and the stub
`#2` has no entry. The cache rebuild (`src/prism/runtime/index_cache.py`,
lines 415 and 485) replays the already-suffixed keys and is correct; the
fresh build is not.

Reproduction (10 lines, `pkg/mod.py` in an otherwise empty package):

```python
def a():
    class R:
        def new(self):
            pass


def b():
    class R:
        def new(self):
            return 1
```

On a fresh build, `pkg.mod.R.new#2` (lines 3–4) is in the symbol table but
not in `_def_nodes`. On a cache hit it is in both.

- **Django instance:** `tests.deprecation.tests.Renamed.new`. Line 83's
  `pass` body is demoted to `#2` by line 116's real body.
- **Impact:** not in any task's gold set; no M4 impact.
