"""Tests for Item 8 (second post-implementation audit): Topological
Graph Role Inference - `TaggingEngine._apply_topological_role_tags` and
the four new tags it can assign (`#entrypoint`, `#io_sink`,
`#pure_transform`, `#error_handler`) purely from call-graph shape and a
lightweight source-text scan, without relying on decorators/naming
conventions.
"""
from __future__ import annotations

from prism.cli import build_pipeline


def _tags_for(tmp_path, filename: str, source: str, qname: str) -> set[str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / filename).write_text(source)
    builder, tag_matrix = build_pipeline(str(repo))
    return tag_matrix.get(qname, set())


def test_unannotated_io_call_is_tagged_io_sink(tmp_path) -> None:
    """The audit's own example: an un-annotated function named
    process_payload with I/O calls is tagged #io_sink purely through
    textual/topological analysis, no decorator involved."""
    tags = _tags_for(
        tmp_path,
        "main.py",
        'import requests\n\ndef process_payload(data):\n    requests.post("https://x.com", data=data)\n',
        "main.process_payload",
    )
    assert "#io_sink" in tags


def test_os_prefix_is_tagged_io_sink(tmp_path) -> None:
    tags = _tags_for(
        tmp_path,
        "lib.py",
        "def helper():\n    pass\n\ndef write_file(path):\n    os.remove(path)\n\ndef caller():\n    write_file('/tmp/x')\n",
        "lib.write_file",
    )
    assert "#io_sink" in tags


def test_entrypoint_tag_requires_zero_in_degree_and_entrypoint_module(tmp_path) -> None:
    tags = _tags_for(
        tmp_path,
        "main.py",
        "def helper():\n    return 1\n\ndef main():\n    return helper()\n",
        "main.main",
    )
    assert "#entrypoint" in tags


def test_entrypoint_tag_absent_outside_entrypoint_module(tmp_path) -> None:
    """Same shape (in-degree 0, out-degree > 0) but in a non-entrypoint
    file - must NOT be tagged #entrypoint."""
    tags = _tags_for(
        tmp_path,
        "utils.py",
        "def helper():\n    return 1\n\ndef orchestrator():\n    return helper()\n",
        "utils.orchestrator",
    )
    assert "#entrypoint" not in tags


def test_entrypoint_tag_absent_when_something_calls_it(tmp_path) -> None:
    """A function in an entrypoint-named file that IS called by
    something else in the repo isn't a real entrypoint (in-degree > 0)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text(
        "def helper():\n    return 1\n\ndef not_really_an_entrypoint():\n    return helper()\n\n"
        "def caller():\n    return not_really_an_entrypoint()\n"
    )
    builder, tag_matrix = build_pipeline(str(repo))
    assert "#entrypoint" not in tag_matrix.get("main.not_really_an_entrypoint", set())


def test_go_main_file_entrypoint(tmp_path) -> None:
    tags = _tags_for(
        tmp_path,
        "main.go",
        "package main\n\nfunc helper() int {\n    return 1\n}\n\nfunc run() int {\n    return helper()\n}\n",
        "main.run",
    )
    assert "#entrypoint" in tags


def test_routes_directory_counts_as_entrypoint_module(tmp_path) -> None:
    repo = tmp_path / "repo"
    (repo / "routes").mkdir(parents=True)
    (repo / "routes" / "orders.py").write_text(
        "def helper():\n    return 1\n\ndef handle_order():\n    return helper()\n"
    )
    builder, tag_matrix = build_pipeline(str(repo))
    assert "#entrypoint" in tag_matrix.get("routes.orders.handle_order", set())


def test_pure_transform_requires_return_value_and_no_calls_and_callers(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "lib.py").write_text(
        "def double(x):\n    return x * 2\n\ndef caller():\n    return double(5)\n"
    )
    builder, tag_matrix = build_pipeline(str(repo))
    assert "#pure_transform" in tag_matrix.get("lib.double", set())


def test_pure_transform_absent_without_a_caller(tmp_path) -> None:
    """in-degree 0 (nothing calls it) - not a #pure_transform even
    though it has a return and no outgoing calls."""
    tags = _tags_for(tmp_path, "lib.py", "def double(x):\n    return x * 2\n", "lib.double")
    assert "#pure_transform" not in tags


def test_pure_transform_absent_when_function_calls_out(tmp_path) -> None:
    """out-degree > 0 (it calls something else) disqualifies it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "lib.py").write_text(
        "def helper(x):\n    return x + 1\n\n"
        "def double(x):\n    return helper(x) * 2\n\n"
        "def caller():\n    return double(5)\n"
    )
    builder, tag_matrix = build_pipeline(str(repo))
    assert "#pure_transform" not in tag_matrix.get("lib.double", set())


def test_pure_transform_absent_with_bare_return(tmp_path) -> None:
    """A bare `return` (no value) doesn't count as "at least one return
    statement" in the audit's own literal sense (a value-producing
    transform)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "lib.py").write_text(
        "def noop(x):\n    return\n\ndef caller():\n    return noop(5)\n"
    )
    builder, tag_matrix = build_pipeline(str(repo))
    assert "#pure_transform" not in tag_matrix.get("lib.noop", set())


def test_python_try_except_with_return_is_error_handler(tmp_path) -> None:
    tags = _tags_for(
        tmp_path,
        "lib.py",
        "def risky():\n    try:\n        return 1\n    except Exception:\n        return None\n",
        "lib.risky",
    )
    assert "#error_handler" in tags


def test_try_except_swallow_with_no_return_or_raise_is_not_error_handler(tmp_path) -> None:
    tags = _tags_for(
        tmp_path,
        "lib.py",
        "def risky():\n    try:\n        do_something()\n    except Exception:\n        pass\n",
        "lib.risky",
    )
    assert "#error_handler" not in tags


def test_go_err_check_with_return_is_error_handler(tmp_path) -> None:
    tags = _tags_for(
        tmp_path,
        "lib.go",
        'package main\n\nfunc risky() error {\n    err := doThing()\n    if err != nil {\n        return err\n    }\n    return nil\n}\n',
        "lib.risky",
    )
    assert "#error_handler" in tags


def test_go_function_without_err_check_is_not_error_handler(tmp_path) -> None:
    tags = _tags_for(
        tmp_path, "lib.go", "package main\n\nfunc plain() int {\n    return 1\n}\n", "lib.plain"
    )
    assert "#error_handler" not in tags


def test_topological_tags_are_additive_not_replacing_existing_tags(tmp_path) -> None:
    """The static, per-symbol rules (decorators, call-sink names) must
    keep firing alongside the new topological ones, not be overwritten."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text(
        "import requests\n\ndef fetch_data():\n    requests.get('https://x.com')\n"
    )
    builder, tag_matrix = build_pipeline(str(repo))
    tags = tag_matrix.get("main.fetch_data", set())
    assert "#external_io" in tags  # pre-existing static CALL_SINK_RULES tag
    assert "#io_sink" in tags  # new topological tag
