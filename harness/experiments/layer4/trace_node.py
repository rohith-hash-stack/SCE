"""Runtime ground truth for a focal JS/TS function: instrument it on disk
(`source_edit.instrument_js`), run a test command with the tracer runtime
(`trace_node_runtime.cjs`), restore the file, and map the recorded
`relpath:line:name` locations to Prism qualified names (the innermost
indexed function or method whose line range holds the line).

Mocha (Express): `node_modules/.bin/mocha --require <runtime> test/res.json.js`.
Vitest 0.32 (tRPC): `vitest --run --no-threads` (child-process forks, so
the same-thread inspector session can read coverage) with the runtime in
NODE_OPTIONS (`--require <runtime> --enable-source-maps`) and `multi=True`:
each process that ran the focal function writes its own file and
`run_traced` merges them; see LAYER4.md.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from harness.experiments.layer4.source_edit import edited, instrument_js, locate

RUNTIME = Path(__file__).with_name("trace_node_runtime.cjs")
ENTER = "(globalThis.__l4 ? globalThis.__l4.enter() : null)"
EXIT = "(globalThis.__l4 ? globalThis.__l4.exit : () => {})"


def merge(parts: list[dict]) -> dict:
    """One raw manifest from per-process ones: counts summed, tests united,
    the first raw stack kept."""
    out = {"focal_calls": 0, "tests": set(), "ancestors": {}, "descendants": {}, "async_window": False,
           "raw_stack": None, "source_mapping": {"mapped": 0, "unmapped": 0, "files": {}}, "processes": len(parts)}
    for part in parts:
        out["focal_file"] = part.get("focal_file", "")
        out["focal_calls"] += part.get("focal_calls", 0)
        out["tests"].update(part.get("tests", []))
        out["async_window"] |= bool(part.get("async_window"))
        out["raw_stack"] = out["raw_stack"] or part.get("raw_stack")
        for key in ("ancestors", "descendants"):
            for loc, n in part.get(key, {}).items():
                out[key][loc] = out[key].get(loc, 0) + n
        sm = part.get("source_mapping", {})
        out["source_mapping"]["mapped"] += sm.get("mapped", 0)
        out["source_mapping"]["unmapped"] += sm.get("unmapped", 0)
        for f, n in sm.get("files", {}).items():
            out["source_mapping"]["files"][f] = out["source_mapping"]["files"].get(f, 0) + n
    out["tests"] = sorted(out["tests"])
    for key in ("ancestors", "descendants"):
        out[key] = dict(sorted(out[key].items()))
    out["source_mapping"]["files"] = dict(sorted(out["source_mapping"]["files"].items()))
    return out


def run_traced(repo_root: str, focal_file: str, focal_line: int, command: list[str], out_path: str,
               env: dict | None = None, timeout: int = 1800, cwd: str = ".", multi: bool = False) -> dict:
    """Instrument `focal_file:focal_line`, run `command` in `repo_root/cwd`
    (it must load `RUNTIME`), restore the file, and return the raw manifest
    with the command's exit status. `multi`: the runner forks, so each
    process writes `<out_path>.<pid>.json` and they are merged."""
    focal_abs = os.path.join(repo_root, focal_file)
    span = locate(focal_abs, focal_line)
    source = Path(focal_abs).read_bytes()
    is_async = source[span.start:span.body_start].lstrip().startswith(b"async") or b"async " in source[
        max(0, span.start - 6):span.start]
    focal_line = source[:span.start].count(b"\n") + 1  # the definition's own first line, as V8 reports it
    run_env = dict(os.environ, **(env or {}), L4_ROOT=repo_root, L4_OUT=out_path, L4_FOCAL_FILE=focal_file,
                   L4_FOCAL_LINE=str(focal_line), L4_FOCAL_ASYNC="1" if is_async else "0",
                   L4_MULTI="1" if multi else "0")
    out = Path(out_path)
    for old in [out, *out.parent.glob(out.name + ".*.json")]:
        old.unlink(missing_ok=True)
    with edited(focal_abs):
        instrument_js(span, ENTER, EXIT)
        proc = subprocess.run(command, cwd=os.path.join(repo_root, cwd), env=run_env, capture_output=True, text=True,
                              timeout=timeout)
    if multi:
        raw = merge([json.loads(p.read_text()) for p in sorted(out.parent.glob(out.name + ".*.json"))])
    else:
        raw = json.loads(out.read_text()) if out.exists() else {}
    raw["exit_code"] = proc.returncode
    raw["output_tail"] = (proc.stdout + proc.stderr)[-2000:]
    return raw


class LineMap:
    """`relpath:line` -> innermost Prism function/method qualified name."""

    def __init__(self, builder):
        root = builder.repo_root
        self.by_file: dict[str, list] = {}
        for s in builder.symbol_table:
            if s.kind in ("function", "method"):
                rel = os.path.relpath(s.file, root) if os.path.isabs(s.file) else s.file
                self.by_file.setdefault(rel.replace(os.sep, "/"), []).append(s)

    def name(self, rel: str, line: int) -> str | None:
        inside = [s for s in self.by_file.get(rel, ()) if s.line_range[0] <= line <= s.line_range[1]]
        return min(inside, key=lambda s: s.line_range[1] - s.line_range[0]).qualified_name if inside else None

    def dist_name(self, rel: str, name: str) -> str | None:
        """A function in a workspace package's build output without source
        maps (`packages/server/dist/observable/index.mjs`, rollup with no
        `sourcemap`): the one same-named function under that package's
        `src/`, else None (absent or ambiguous)."""
        if not (rel.startswith("dist/") or "/dist/" in rel) or not name or name.startswith("<"):
            return None
        # `dist/...`: the index is rooted at the package's src/ (see `rebase`)
        src = "" if rel.startswith("dist/") else rel.split("/dist/", 1)[0] + "/src/"
        hits = {s.qualified_name for f, syms in self.by_file.items() if f.startswith(src)
                for s in syms if s.qualified_name.rsplit(".", 1)[-1] == name}
        return hits.pop() if len(hits) == 1 else None


def rebase(raw: dict, prefix: str) -> dict:
    """The raw manifest with locations relative to a corpus indexed below
    the clone root (tRPC: Prism indexes `packages/server/src`, the tracer
    records clone-relative paths): `prefix` stripped; the same package's
    build output (`packages/server/dist/...` for `packages/server/src`) is
    kept as `dist/...` for `LineMap.dist_name`; other locations are moved to
    `outside_<key>` (other workspace packages)."""
    prefix = prefix.rstrip("/") + "/"
    dist = prefix.rsplit("/", 2)[0] + "/dist/" if prefix.rstrip("/").endswith("/src") else None
    out = dict(raw)
    for key in ("ancestors", "descendants"):
        inside, outside = {}, {}
        for loc, n in raw.get(key, {}).items():
            if loc.startswith(prefix):
                inside[loc.removeprefix(prefix)] = n
            elif dist and loc.startswith(dist):
                inside["dist/" + loc.removeprefix(dist)] = n
            else:
                outside[loc] = n
        out[key], out[f"outside_{key}"] = inside, outside
    return out


def map_manifest(raw: dict, builder, focal_qname: str) -> dict:
    """The raw manifest with ancestors/descendants as qualified names
    (counts summed; unmappable locations kept under `unmapped`). Locations
    in an unmapped build output are resolved by name to the package's
    source (`LineMap.dist_name`) and listed under `resolved_by_name`, so
    task authors can review them."""
    lines = LineMap(builder)
    out = {"focal": [focal_qname], "focal_calls": raw.get("focal_calls", 0), "tests": raw.get("tests", []),
           "async_window": raw.get("async_window", False), "exit_code": raw.get("exit_code")}
    for key in ("ancestors", "descendants"):
        mapped: dict[str, int] = {}
        unmapped: dict[str, int] = {}
        for loc, count in raw.get(key, {}).items():
            rel, line, name = loc.split(":", 2)
            q = lines.name(rel, int(line))
            if q is None and (q := lines.dist_name(rel, name)) is not None:
                out.setdefault("resolved_by_name", {})[loc] = q
            if q is None:
                unmapped[loc] = unmapped.get(loc, 0) + count
            elif q != focal_qname:
                mapped[q] = mapped.get(q, 0) + count
        out[key] = dict(sorted(mapped.items()))
        out[f"{key}_unmapped"] = dict(sorted(unmapped.items()))
    return out
