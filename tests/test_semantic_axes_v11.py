"""Tests for Prism v1.1's Four-Axis Semantic Model
(`prism.semantics.bitmask`/`substance`/`form`/`output`/`role`/
`extractor`) - the Substance/Form/Output/Role bitmask extraction that
feeds the causal-coupling submodular knapsack (`tests/
test_v11_invariants.py`, `test_pipeline_preservation.py`,
`test_redundancy_elimination.py`, `test_language_parity_v11.py` cover
those downstream consumers).
"""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.semantics.bitmask import (
    ALL_KNOWN_BITS,
    FORM_BITS,
    OUTPUT_BITS,
    ROLE_BITS,
    SUBSTANCE_BITS,
    FeatureBit,
    compose_mask,
    describe_mask,
)
from prism.semantics.extractor import compute_feature_masks
from prism.semantics.form import compute_form_bits
from prism.semantics.output import compute_output_bits
from prism.semantics.role import compute_role_bits
from prism.semantics.substance import compute_substance_bits


def _build(tmp_path, filename, source):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / filename).write_text(source)
    return build_pipeline(str(repo))


# --------------------------------------------------------------------- #
# bitmask.py
# --------------------------------------------------------------------- #
def test_every_feature_bit_fits_in_64_bits():
    for bit in FeatureBit:
        assert 0 <= int(bit) < 2**64


def test_axis_bit_groups_are_disjoint():
    assert not (SUBSTANCE_BITS & FORM_BITS)
    assert not (SUBSTANCE_BITS & OUTPUT_BITS)
    assert not (SUBSTANCE_BITS & ROLE_BITS)
    assert not (FORM_BITS & OUTPUT_BITS)
    assert not (FORM_BITS & ROLE_BITS)
    assert not (OUTPUT_BITS & ROLE_BITS)


def test_axis_bit_counts_match_spec():
    assert len(SUBSTANCE_BITS) == 7
    assert len(FORM_BITS) == 10
    assert len(OUTPUT_BITS) == 9
    assert len(ROLE_BITS) == 7
    assert len(ALL_KNOWN_BITS) == 33


def test_compose_mask_ors_axes_together():
    mask = compose_mask(FeatureBit.SINK_NETWORK_IO, FeatureBit.FORM_LINEAR)
    assert mask & int(FeatureBit.SINK_NETWORK_IO)
    assert mask & int(FeatureBit.FORM_LINEAR)
    assert not (mask & int(FeatureBit.FORM_PIPELINE))


def test_describe_mask_round_trips_names():
    mask = compose_mask(FeatureBit.SINK_DATABASE_IO, FeatureBit.ROLE_LEAF_SERVICE)
    assert set(describe_mask(mask)) == {"SINK_DATABASE_IO", "ROLE_LEAF_SERVICE"}


# --------------------------------------------------------------------- #
# substance.py
# --------------------------------------------------------------------- #
_SUBSTANCE_SOURCE = """import requests
import time
import random


def fetch_data(url):
    return requests.get(url)


def send(payload):
    return fetch_data(payload)


def wait_a_bit():
    time.sleep(1)


def pick_random():
    return random.choice([1, 2, 3])


def add(a, b):
    return a + b
"""


def test_direct_network_sink_detected(tmp_path):
    builder, _ = _build(tmp_path, "svc.py", _SUBSTANCE_SOURCE)
    bits = compute_substance_bits(builder)
    assert bits["svc.fetch_data"] & int(FeatureBit.SINK_NETWORK_IO)


def test_transitive_one_hop_wrapper_propagates_sink(tmp_path):
    builder, _ = _build(tmp_path, "svc.py", _SUBSTANCE_SOURCE)
    bits = compute_substance_bits(builder)
    assert bits["svc.send"] & int(FeatureBit.SINK_NETWORK_IO)


def test_time_and_randomness_sinks_detected(tmp_path):
    builder, _ = _build(tmp_path, "svc.py", _SUBSTANCE_SOURCE)
    bits = compute_substance_bits(builder)
    assert bits["svc.wait_a_bit"] & int(FeatureBit.SINK_TIME_IO)
    assert bits["svc.pick_random"] & int(FeatureBit.SINK_RANDOMNESS)


def test_pure_function_gets_pure_compute_bit(tmp_path):
    builder, _ = _build(tmp_path, "svc.py", _SUBSTANCE_SOURCE)
    bits = compute_substance_bits(builder)
    assert bits["svc.add"] == int(FeatureBit.SINK_PURE_COMPUTE)


def test_impure_non_io_function_is_not_marked_pure(tmp_path):
    builder, _ = _build(
        tmp_path, "svc.py",
        "class Counter:\n"
        "    def __init__(self):\n"
        "        self.count = 0\n"
        "\n"
        "    def bump(self):\n"
        "        self.count += 1\n",
    )
    bits = compute_substance_bits(builder)
    assert bits["svc.Counter.bump"] != int(FeatureBit.SINK_PURE_COMPUTE)


def test_database_sink_via_import_alias(tmp_path):
    builder, _ = _build(
        tmp_path, "svc.py",
        "import psycopg2 as pg\n\n\ndef save(row):\n    return pg.connect().execute(row)\n",
    )
    bits = compute_substance_bits(builder)
    assert bits["svc.save"] & int(FeatureBit.SINK_DATABASE_IO)


def test_filesystem_builtin_open_detected(tmp_path):
    builder, _ = _build(tmp_path, "svc.py", "def read(path):\n    return open(path).read()\n")
    bits = compute_substance_bits(builder)
    assert bits["svc.read"] & int(FeatureBit.SINK_FILESYSTEM_IO)


# --------------------------------------------------------------------- #
# form.py
# --------------------------------------------------------------------- #
_FORM_SOURCE = """def guard(x):
    if x < 0:
        return None
    return x * 2


def retry(items):
    for item in items:
        try:
            send(item)
        except Exception:
            send(item)


def send(item):
    return item


def pipeline(x):
    a = step1(x)
    b = step2(a)
    c = step3(b)
    return c


def step1(x):
    return x


def step2(x):
    return x


def step3(x):
    return x


def dispatch(kind):
    if kind == 1:
        return handle_a()
    if kind == 2:
        return handle_b()
    if kind == 3:
        return handle_c()
    if kind == 4:
        return handle_d()


def handle_a():
    return 1


def handle_b():
    return 2


def handle_c():
    return 3


def handle_d():
    return 4


def fact(n):
    if n <= 1:
        return 1
    return n * fact(n - 1)


def validate(x):
    if x is None:
        return False
    if x < 0:
        return False
    if not isinstance(x, int):
        return False
    return True


async def fetch_many():
    return await gather()


def gather():
    return None
"""


def test_guard_early_exit_shape(tmp_path):
    builder, _ = _build(tmp_path, "f.py", _FORM_SOURCE)
    bits = compute_form_bits(builder)
    assert bits["f.guard"] & int(FeatureBit.FORM_GUARD_EARLY_EXIT)


def test_retry_loop_shape(tmp_path):
    builder, _ = _build(tmp_path, "f.py", _FORM_SOURCE)
    bits = compute_form_bits(builder)
    assert bits["f.retry"] & int(FeatureBit.FORM_RETRY_LOOP)


def test_pipeline_shape(tmp_path):
    builder, _ = _build(tmp_path, "f.py", _FORM_SOURCE)
    bits = compute_form_bits(builder)
    assert bits["f.pipeline"] & int(FeatureBit.FORM_PIPELINE)


def test_branch_dispatch_shape_wins_over_guard_early_exit(tmp_path):
    """A dispatch function's first arm is itself an early-return `if` -
    FORM_BRANCH_DISPATCH's own stronger, more specific rule (>3 branches,
    >=2 distinct callees) must win over the weaker "first statement is a
    guard" signal, not the other way around."""
    builder, _ = _build(tmp_path, "f.py", _FORM_SOURCE)
    bits = compute_form_bits(builder)
    assert bits["f.dispatch"] & int(FeatureBit.FORM_BRANCH_DISPATCH)


def test_recursive_function_detected(tmp_path):
    builder, _ = _build(tmp_path, "f.py", _FORM_SOURCE)
    bits = compute_form_bits(builder)
    assert bits["f.fact"] & int(FeatureBit.FORM_RECURSIVE)


def test_validator_shape_detected_and_not_branch_dispatch(tmp_path):
    builder, _ = _build(tmp_path, "f.py", _FORM_SOURCE)
    bits = compute_form_bits(builder)
    assert bits["f.validate"] & int(FeatureBit.FORM_VALIDATOR)
    assert not (bits["f.validate"] & int(FeatureBit.FORM_BRANCH_DISPATCH))


def test_async_concurrent_detected(tmp_path):
    builder, _ = _build(tmp_path, "f.py", _FORM_SOURCE)
    bits = compute_form_bits(builder)
    assert bits["f.fetch_many"] & int(FeatureBit.FORM_ASYNC_CONCURRENT)


def test_default_linear_shape(tmp_path):
    builder, _ = _build(tmp_path, "f.py", "def add(a, b):\n    return a + b\n")
    bits = compute_form_bits(builder)
    assert bits["f.add"] & int(FeatureBit.FORM_LINEAR)


# --------------------------------------------------------------------- #
# output.py
# --------------------------------------------------------------------- #
_OUTPUT_SOURCE = """class Order:
    def __init__(self, id):
        self.id = id


def is_valid(x):
    return x > 0


def do_nothing(x):
    x.touch()


def make_order(id):
    return Order(id)


def bump(x):
    x = x + 1
    return x


def collect(items):
    return [i for i in items]


def builder_step(self):
    self.value = 1
    return self


async def fetch():
    return await get_data()


def get_data():
    return {}


def must_positive(x):
    if x <= 0:
        raise ValueError('bad')
    raise RuntimeError('unreachable')


def lookup(cache, key):
    return cache.get(key)


def bare_none():
    return None
"""


def test_predicate_output(tmp_path):
    builder, _ = _build(tmp_path, "o.py", _OUTPUT_SOURCE)
    bits = compute_output_bits(builder)
    assert bits["o.is_valid"] == int(FeatureBit.OUTPUT_PREDICATE)


def test_command_output_no_return_value(tmp_path):
    builder, _ = _build(tmp_path, "o.py", _OUTPUT_SOURCE)
    bits = compute_output_bits(builder)
    assert bits["o.do_nothing"] == int(FeatureBit.OUTPUT_COMMAND)


def test_command_output_bare_none(tmp_path):
    builder, _ = _build(tmp_path, "o.py", _OUTPUT_SOURCE)
    bits = compute_output_bits(builder)
    assert bits["o.bare_none"] == int(FeatureBit.OUTPUT_COMMAND)


def test_factory_output(tmp_path):
    builder, _ = _build(tmp_path, "o.py", _OUTPUT_SOURCE)
    bits = compute_output_bits(builder)
    assert bits["o.make_order"] == int(FeatureBit.OUTPUT_FACTORY)


def test_transformer_output(tmp_path):
    builder, _ = _build(tmp_path, "o.py", _OUTPUT_SOURCE)
    bits = compute_output_bits(builder)
    assert bits["o.bump"] == int(FeatureBit.OUTPUT_TRANSFORMER)


def test_aggregator_output(tmp_path):
    builder, _ = _build(tmp_path, "o.py", _OUTPUT_SOURCE)
    bits = compute_output_bits(builder)
    assert bits["o.collect"] == int(FeatureBit.OUTPUT_AGGREGATOR)


def test_fluent_output(tmp_path):
    builder, _ = _build(tmp_path, "o.py", _OUTPUT_SOURCE)
    bits = compute_output_bits(builder)
    assert bits["o.builder_step"] == int(FeatureBit.OUTPUT_FLUENT)


def test_async_deferred_output(tmp_path):
    builder, _ = _build(tmp_path, "o.py", _OUTPUT_SOURCE)
    bits = compute_output_bits(builder)
    assert bits["o.fetch"] & int(FeatureBit.OUTPUT_ASYNC_DEFERRED)


def test_guard_output_all_branches_raise(tmp_path):
    builder, _ = _build(tmp_path, "o.py", _OUTPUT_SOURCE)
    bits = compute_output_bits(builder)
    assert bits["o.must_positive"] == int(FeatureBit.OUTPUT_GUARD)


def test_query_output_default_fallback(tmp_path):
    builder, _ = _build(tmp_path, "o.py", _OUTPUT_SOURCE)
    bits = compute_output_bits(builder)
    assert bits["o.lookup"] == int(FeatureBit.OUTPUT_QUERY)


def test_go_error_slot_stripped_from_factory_payload(tmp_path):
    builder, _ = _build(
        tmp_path, "svc.go",
        "package main\n\n"
        "type Order struct {\n\tID int\n}\n\n"
        "func NewOrder(id int) *Order {\n\treturn &Order{ID: id}\n}\n\n"
        "func FetchOrder(id int) (*Order, error) {\n\treturn NewOrder(id), nil\n}\n",
    )
    bits = compute_output_bits(builder)
    assert bits["svc.FetchOrder"] == int(FeatureBit.OUTPUT_FACTORY)


def test_go_slice_literal_is_aggregator_not_factory(tmp_path):
    builder, _ = _build(
        tmp_path, "svc.go",
        "package main\n\nfunc ListOrders() []int {\n\treturn []int{1, 2}\n}\n",
    )
    bits = compute_output_bits(builder)
    assert bits["svc.ListOrders"] == int(FeatureBit.OUTPUT_AGGREGATOR)


# --------------------------------------------------------------------- #
# role.py
# --------------------------------------------------------------------- #
def test_orchestrator_role(tmp_path):
    builder, _ = _build(
        tmp_path, "r.py",
        "def main():\n    a()\n    b()\n    c()\n    d()\n\n\n"
        "def a():\n    return 1\n\n\ndef b():\n    return 1\n\n\n"
        "def c():\n    return 1\n\n\ndef d():\n    return 1\n",
    )
    substance = compute_substance_bits(builder)
    roles = compute_role_bits(builder, substance)
    assert roles["r.main"] & int(FeatureBit.ROLE_ORCHESTRATOR)


def test_leaf_utility_role(tmp_path):
    builder, _ = _build(
        tmp_path, "r.py",
        "def util(x):\n    return x + 1\n\n\n"
        "def c1():\n    return util(1)\n\n\ndef c2():\n    return util(2)\n\n\n"
        "def c3():\n    return util(3)\n\n\ndef c4():\n    return util(4)\n\n\n"
        "def c5():\n    return util(5)\n",
    )
    substance = compute_substance_bits(builder)
    roles = compute_role_bits(builder, substance)
    assert roles["r.util"] & int(FeatureBit.ROLE_LEAF_UTILITY)


def test_leaf_service_role(tmp_path):
    builder, _ = _build(
        tmp_path, "r.py",
        "import requests\n\n\ndef fetch():\n    return requests.get('http://x')\n",
    )
    substance = compute_substance_bits(builder)
    roles = compute_role_bits(builder, substance)
    assert roles["r.fetch"] & int(FeatureBit.ROLE_LEAF_SERVICE)


def test_public_api_and_entrypoint_roles(tmp_path):
    builder, _ = _build(tmp_path, "r.py", "def main():\n    return helper()\n\n\ndef helper():\n    return 1\n")
    substance = compute_substance_bits(builder)
    roles = compute_role_bits(builder, substance)
    assert roles["r.main"] & int(FeatureBit.ROLE_PUBLIC_API)
    assert roles["r.main"] & int(FeatureBit.ROLE_ENTRYPOINT)


def test_adapter_role_bridges_two_substance_domains(tmp_path):
    builder, _ = _build(
        tmp_path, "r.py",
        "import requests\nimport sqlalchemy\n\n\n"
        "def handler():\n    data = requests.get('http://x')\n    return adapter(data)\n\n\n"
        "def adapter(data):\n    return store(data)\n\n\n"
        "def store(x):\n    return sqlalchemy.create_engine('x')\n",
    )
    substance = compute_substance_bits(builder)
    roles = compute_role_bits(builder, substance)
    assert roles["r.adapter"] & int(FeatureBit.ROLE_ADAPTER)


def test_private_helper_is_not_public_api(tmp_path):
    builder, _ = _build(tmp_path, "r.py", "def _helper():\n    return 1\n")
    substance = compute_substance_bits(builder)
    roles = compute_role_bits(builder, substance)
    assert not (roles["r._helper"] & int(FeatureBit.ROLE_PUBLIC_API))


# --------------------------------------------------------------------- #
# extractor.py (composition)
# --------------------------------------------------------------------- #
def test_compute_feature_masks_composes_all_four_axes(tmp_path):
    builder, _ = _build(
        tmp_path, "e.py",
        "import requests\n\n\ndef fetch(url):\n    return requests.get(url)\n",
    )
    masks = compute_feature_masks(builder)
    names = set(describe_mask(masks["e.fetch"]))
    assert "SINK_NETWORK_IO" in names
    # Form/Output bits are also present (some FORM_* and some OUTPUT_*).
    assert any(n.startswith("FORM_") for n in names)
    assert any(n.startswith("OUTPUT_") for n in names)


def test_compute_feature_masks_survives_a_cache_hit(tmp_path):
    """Regression guard for the def_node()-after-cache-hit bug this
    module's development caught - see
    tests/test_index_cache.py::test_cache_hit_rehydrates_def_node_for_every_symbol."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "e.py").write_text("def add(a, b):\n    return a + b\n")
    build_pipeline(str(repo))
    builder, _ = build_pipeline(str(repo))  # cache hit
    masks = compute_feature_masks(builder)
    assert masks["e.add"] != 0


def test_every_symbol_gets_a_nonzero_mask(tmp_path):
    """Every real function/method gets *some* bit set on every axis it
    applies to - a bare 0 mask would mean an axis silently failed."""
    builder, _ = build_pipeline("tests/fixtures/python_repo")
    masks = compute_feature_masks(builder)
    assert masks
    for qname, mask in masks.items():
        assert mask != 0, f"{qname} got an all-zero feature mask"
