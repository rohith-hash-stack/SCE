"""Phase F: envelope layer - dynamic schema versioning, conditional
<causal_path> rendering, XML hygiene, and serialization determinism.

Scope note and deviations from the brief, documented here rather than
silently (see the phase-f-* commit messages for the full evidence):

  - The brief's own file names (`prism/surface/render.py`, `prism/
    surface/envelope.py`) and root element (`<context_package>`) do not
    exist in this codebase. The real, already-mature, already-tested
    renderer/parser pair lives in `prism.surface.renderer`/`prism.
    surface.parser`, its root element is `<prism_context>`, and
    `ContextPackage.schema_version` is already a real `int` field (1 or
    2), already round-tripped by `prism.surface.parser.parse_context`.
    Renaming any of this would be a large, breaking rewrite for no
    functional benefit and real regression risk to 4 existing test
    files (tests/surface/test_renderer_unit.py, test_renderer_
    properties.py, test_mcp_surface.py, tests/test_envelope_
    invariants.py) - per explicit user direction, this phase hardens the
    real module in place instead. `schema_version=1`/`2` are used
    throughout below, not the brief's string `"1.0"`/`"2.0"`.
  - Issues #27 (conditional <causal_path>), #28 (no internal-attribute
    leakage), and #30 (CDATA escaping, attribute sorting) were already
    fully implemented and already covered by the existing test files
    named above *before* this phase - confirmed by reading the code and
    running that suite (48/48 passing) before making any change. This
    phase's own tests below re-verify those properties end-to-end
    through the real `build_context_package` -> `render` pipeline
    (integration-level), rather than duplicate that existing lower-level
    unit/property coverage.
  - The brief's Invariant 4 canonical node-ordering key
    (`file_path, line_start, name`) is NOT implemented - the renderer's
    existing, deliberate key (seed first, then `distance ASC, id ASC`)
    orders nodes in causal/narrative order for an LLM reader, which the
    brief's own file-position key would regress with no stated benefit.
    Determinism itself (byte-identical output for identical input) is
    unaffected by *which* deterministic key is used and is verified
    directly below.
  - The brief's `<causal_path><step rank="..." id="..."/></causal_path>`
    shape does not match the real, already-implemented, already-parsed
    `<causal_path><stage order="..." symbol="..." distance="..."
    role="..."/></causal_path>` shape (`prism.surface.parser.
    _parse_causal_path` already depends on exactly this shape). This
    phase verifies the real element/attribute names, not the brief's.
"""
from __future__ import annotations

import os
import subprocess
import sys
import xml.etree.ElementTree as ET

from prism.cli import build_pipeline
from prism.surface.build import build_context_package
from prism.surface.models import CausalPath, CausalPathStage
from prism.surface.parser import parse_context
from prism.surface.renderer import RenderOptions, render

_SOURCE = (
    "def leaf():\n    return 1\n\n\n"
    "def seed():\n    return leaf()\n"
)


def _build_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(_SOURCE)
    builder, _ = build_pipeline(str(repo))
    return builder, repo


def _pkg(tmp_path, task_type="debug"):
    builder, repo = _build_repo(tmp_path)
    return build_context_package(builder, "mod.seed", str(repo), 4000, task_type=task_type)


# ============================================================
# Invariant 1: dynamic schema versioning
# ============================================================

def test_render_schema_version_1(tmp_path):
    """schema_version=1 keeps the pre-Phase-F root shape exactly - no
    budget_total/budget_consumed/task_type, whatever pkg.task_type is."""
    pkg = _pkg(tmp_path)
    xml = render(pkg, RenderOptions(schema_version=1))
    root_line = xml.splitlines()[0]
    assert 'schema_version="1"' in root_line
    assert "budget_total" not in root_line
    assert "budget_consumed" not in root_line
    assert "task_type" not in root_line


def test_render_schema_version_2(tmp_path):
    """schema_version=2 (the default) adds real, new root metadata:
    budget_total (the requested budget), budget_consumed (the actual
    rendered token count - cross-checked against <trailer token_count>),
    and task_type (round-tripped from build_context_package's own
    parameter)."""
    pkg = _pkg(tmp_path, task_type="debug")
    xml = render(pkg, RenderOptions(schema_version=2))
    root = ET.fromstring(xml)
    assert root.attrib["schema_version"] == "2"
    assert root.attrib["budget_total"] == "4000"
    assert root.attrib["task_type"] == "debug"
    trailer = root.find("trailer")
    assert root.attrib["budget_consumed"] == trailer.attrib["token_count"]


def test_task_type_omitted_when_none(tmp_path):
    pkg = _pkg(tmp_path, task_type=None)
    xml = render(pkg, RenderOptions(schema_version=2))
    root_line = xml.splitlines()[0]
    assert "task_type" not in root_line


def test_schema_version_2_round_trips_task_type(tmp_path):
    pkg = _pkg(tmp_path, task_type="chain")
    xml = render(pkg, RenderOptions(schema_version=2))
    parsed = parse_context(xml)
    assert parsed.task_type == "chain"


# ============================================================
# Invariant 2: conditional <causal_path> rendering
# ============================================================

def test_causal_path_omitted_when_none(tmp_path):
    pkg = _pkg(tmp_path, task_type="blast")  # blast bypasses causal_path (Phase D)
    assert pkg.causal_path is None
    xml = render(pkg)
    assert "<causal_path" not in xml


def test_causal_path_omitted_when_stages_empty(tmp_path):
    """Defensive coverage: causal_path.stages is never empty via the real
    production path (compute_causal_path_stages always returns >= 1
    stage), but a hand-constructed CausalPath with stages=[] must still
    omit the tag entirely rather than render an empty container - the
    renderer's own contract, made explicit and testable per Phase F
    Invariant 2, not just an accident of production data shape."""
    pkg = _pkg(tmp_path)
    pkg = pkg.model_copy(update={"causal_path": CausalPath(seed="mod.seed", stages=[])})
    xml = render(pkg)
    assert "<causal_path" not in xml


def test_causal_path_rendered_correctly(tmp_path):
    """A real 3-stage path renders 3 <stage> elements, in rank order -
    real element/attribute names (stage/order/symbol), not the brief's
    step/rank/id (see this module's own docstring for why)."""
    pkg = _pkg(tmp_path)
    stages = [
        CausalPathStage(order=1, symbol="mod.seed", distance=0.0, role="entry"),
        CausalPathStage(order=2, symbol="mod.mid", distance=1.0, role="transform"),
        CausalPathStage(order=3, symbol="mod.leaf", distance=2.0, role="sink"),
    ]
    pkg = pkg.model_copy(update={"causal_path": CausalPath(seed="mod.seed", stages=stages)})
    xml = render(pkg)
    root = ET.fromstring(xml)
    causal_path_elem = root.find("causal_path")
    assert causal_path_elem is not None
    stage_elems = causal_path_elem.findall("stage")
    assert len(stage_elems) == 3
    assert [s.attrib["order"] for s in stage_elems] == ["1", "2", "3"]
    assert [s.attrib["symbol"] for s in stage_elems] == ["mod.seed", "mod.mid", "mod.leaf"]


# ============================================================
# Invariant 3: no internal attribute leakage, CDATA / escaping hygiene
# ============================================================

def test_no_internal_runtime_attributes_leaked(tmp_path):
    pkg = _pkg(tmp_path)
    xml = render(pkg, RenderOptions(schema_version=2))
    for leaked in ("_SUBSTANCE_SINK_MASK", "frontier_score", "_is_direct_successor", "dist_w=", "covered_mask"):
        assert leaked not in xml, f"internal attribute {leaked!r} leaked into public XML"


def test_cdata_escaping_with_special_characters(tmp_path):
    """A symbol body containing <, >, &, and an embedded ]]> sequence
    parses cleanly with xml.etree.ElementTree - no hand-rolled parsing,
    the real stdlib parser a real consumer would use."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(
        "def seed():\n"
        "    # a < b > c & d ]]> e\n"
        "    return 1 if 1 < 2 else 0\n"
    )
    builder, _ = build_pipeline(str(repo))
    pkg = build_context_package(builder, "mod.seed", str(repo), 4000)
    xml = render(pkg)
    root = ET.fromstring(xml)  # raises ParseError on malformed XML
    body_text = root.find("nodes").find("node").find("body").text
    assert "a < b > c & d ]]> e" in body_text


def test_file_path_is_posix_normalized(tmp_path):
    pkg = _pkg(tmp_path)
    xml = render(pkg)
    root = ET.fromstring(xml)
    seed_file = root.find("metadata").find("seed").attrib["file"]
    assert "\\" not in seed_file


# ============================================================
# Invariant 4: serialization determinism
# ============================================================

_DETERMINISM_SCRIPT = """
import sys
sys.path.insert(0, ".")
from prism.cli import build_pipeline
from prism.surface.build import build_context_package
from prism.surface.renderer import render

builder, _ = build_pipeline(sys.argv[1])
pkg = build_context_package(builder, "mod.seed", sys.argv[1], 4000, task_type="debug")
print(render(pkg))
"""


def test_xml_serialization_determinism(tmp_path):
    """Same (pkg, options) - two separate processes, PYTHONHASHSEED 0 vs
    42 - byte-identical XML. Attribute order and node/edge order are
    already explicit sorts, never Python's own (hash-seed-dependent) set
    or dict iteration order."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(_SOURCE)

    env0 = dict(os.environ, PYTHONHASHSEED="0")
    env42 = dict(os.environ, PYTHONHASHSEED="42")
    out0 = subprocess.run(
        [sys.executable, "-c", _DETERMINISM_SCRIPT, str(repo)], cwd="/home/user/SCE", env=env0, capture_output=True, text=True, check=True
    ).stdout
    out42 = subprocess.run(
        [sys.executable, "-c", _DETERMINISM_SCRIPT, str(repo)], cwd="/home/user/SCE", env=env42, capture_output=True, text=True, check=True
    ).stdout
    assert out0 == out42
    assert out0 != ""


def test_attributes_sorted_alphabetically_on_new_root_attrs(tmp_path):
    """The 3 new schema_version=2 root attributes (budget_consumed,
    budget_total, task_type) join the existing sort, not appended
    unsorted after it."""
    pkg = _pkg(tmp_path, task_type="debug")
    xml = render(pkg, RenderOptions(schema_version=2, include_timestamp=False, include_run_id=False))
    root_line = xml.splitlines()[0]
    names = []
    for chunk in root_line[len("<prism_context "):-1].split('" '):
        name = chunk.split("=")[0].strip()
        if name:
            names.append(name)
    assert names == sorted(names)
