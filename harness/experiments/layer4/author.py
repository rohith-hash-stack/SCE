"""Author Layer 4 tasks from `benchmarks/layer4/specs.py`: trace, derive
gold, run gates, validate, write.

    PYTHONPATH=. python -m harness.experiments.layer4.author \\
        --python fastapi=<venv>/bin/python --python django=<venv>/bin/python \\
        [--node <node20>] [--names-dir <dir>] [--only l4_fwd_django_01,...] [--corpus trpc]

P3b (retrieval_pivot/P3.md): `--specs benchmarks/layer4/specs_p3b.py --eligibility p3b
--out benchmarks/layer4_p3b --corpus <c>` traces the drawn specs in draw order and keeps the first
`--per-corpus` (8) eligible - clean trace exit, the focal runs, 2..15 gold within `max_depth` 2 - and
logs every decision to `OUT/authoring_log_<c>.json` (`author_p3b`).

Writes, per task, under `benchmarks/layer4/` (or `--out`):
- `tasks/<task_id>.json` - the `layer4.task.v1` record (validated);
- `manifests/<task_id>.json` - the mapped trace manifest the gold or the
  oracle callers came from (raw Node locations kept alongside);
- `gates/<task_id>.json` - backward tasks: both gate runs.

Gold never comes from Prism. Prism's index is used only to keep gold names
that an arm can retrieve at all (functions and methods outside test files)
and to place the focal function (file, line).

Forward gold: upstream ancestors within `max_depth` call hops
(`ancestor_depth`, every frame counted), named in the index outside test
files and the example zone (`prism.graph.entry_points.example_zone`:
docs_src/, examples/, ...), at most 15.
Django forward tasks also get tables and side-effect tags from the SQL
logger, after dropping tables of the test apps (`tests/<app>/` - no arm sees
them) and SQLite's own `sqlite_master` introspection (backend-specific);
tags are derived from the remaining statements. Other corpora: `null`.

Backward: gates with the `STUB_IMPLEMENTATION` stub (both required, the
stub seen in the failing output), then a trace of the same tests for the
oracle slice: the focal (masked), its callers within one hop and the
functions it calls (`oracle_callees`) - what an implementer needs.
"""
from __future__ import annotations

import argparse
import datetime
import glob
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from harness.experiments.layer4 import splice as S
from harness.experiments.layer4 import tasks as T
from harness.experiments.layer4.source_edit import locate
from harness.experiments.layer4.trace_node import RUNTIME, map_manifest, rebase, run_traced

SCE = Path(__file__).resolve().parents[3]
OUT = SCE / "benchmarks" / "layer4"
PINNED = json.loads((SCE / "benchmarks" / "corpora" / "pinned_commits.json").read_text())
BACKWARD_ORACLE_DEPTH = 1
#: a clean traced FastAPI suite takes ~1 min; its websocket tests deadlock
#: under the profiler now and then, so a stuck run is killed and rerun
TRACE_TIMEOUT_S = 600
TRACE_ATTEMPTS = 3
#: tRPC: Prism indexes packages/server/src; tests run from packages/tests (Vitest root: packages/)
TRPC_PREFIX = "packages/server/src"
TRPC_CWD = "packages/tests"
NODE = {"bin": "node"}  # --node: the Node 20 the tRPC workspace needs
MAX_GOLD = T.MAX_ANSWER_SYMBOLS
#: P3b eligibility (harness/experiments/retrieval_pivot/P3.md, amendment 1): a forward spec yields a
#: task only if its trace exits 0, the focal runs, and it has P3B_MIN_GOLD..MAX_GOLD gold callers
#: within the spec's max_depth (2); specs are taken per corpus in draw order until P3B_PER_CORPUS are
#: kept. P3B_MIN_GOLD was 3 in the pilot run (9 of 96 traced specs eligible).
P3B_MIN_GOLD = 2
P3B_PER_CORPUS = 8
#: environment faults tolerated per corpus before authoring aborts (the environment is broken)
P3B_MAX_ENV_FAULTS = 5
#: rejection reason codes, matched on the SystemExit text the checks raise
REASON_CODES = (("environment fault", "env_fault"), ("timed out", "env_fault"), ("trace exited", "trace_exit_nonzero"),
                ("never ran", "focal_not_run"), ("empty gold", "gold_below_min"), ("gold symbols <", "gold_below_min"),
                ("gold symbols >", "gold_above_max"), ("not an indexed", "not_indexed"), ("invalid task", "invalid_task"))
#: reasons that are a property of the drawn candidate (the spec is skipped and the next one traced)
ELIGIBILITY_REASONS = {"trace_exit_nonzero", "focal_not_run", "gold_below_min", "gold_above_max", "not_indexed"}
#: reasons that are the environment's, not the candidate's: the spec is set aside (`env_fault`), not
#: counted as ineligible, and the next one traced. Anything else (an invalid task, an unknown error)
#: is a tooling fault and aborts, so a bug never skips a draw.
ENVIRONMENT_REASONS = {"env_fault"}
#: tRPC source aliases: a trace-only Vitest config next to the corpus's own (`trpc_source_config`)
TRPC_SOURCE_CONFIG = "vitest.l4-source.config.ts"
TRPC_PACKAGES = ("server", "client", "react-query", "next")
#: the focal of a control run: a name no corpus defines, so the profiler runs and records nothing
CONTROL_FOCAL = "__l4_control__.none"


def corpus_root(corpus: str) -> str:
    from benchmarks.corpora.resolver import resolve
    return str(resolve(corpus))


def load_names(corpus: str, names_dir: str | None) -> list[list]:
    """[qname, relpath, start, end, in_test_file] for every indexed function/method."""
    if names_dir and (p := Path(names_dir) / f"{corpus}_names.json").exists():
        return json.loads(p.read_text())
    from prism.engine import PrismEngine
    b = PrismEngine.from_repo(corpus_root(corpus)).builder
    rows = []
    for s in b.symbol_table:
        if s.kind in ("function", "method"):
            rel = os.path.relpath(s.file, b.repo_root) if os.path.isabs(s.file) else s.file
            rows.append([s.qualified_name, rel, s.line_range[0], s.line_range[1], b._in_test_file(s.file)])
    if names_dir:
        Path(names_dir).mkdir(parents=True, exist_ok=True)
        (Path(names_dir) / f"{corpus}_names.json").write_text(json.dumps(rows))
    return rows


def clone_root(corpus: str) -> str:
    """The clone's top level (tRPC is indexed below it, at TRPC_PREFIX)."""
    root = corpus_root(corpus)
    return root[: -len(TRPC_PREFIX) - 1] if corpus == "trpc" and root.endswith(TRPC_PREFIX) else root


def vitest_bin(clone: str) -> str:
    pkg = json.loads(Path(clone, TRPC_CWD, "node_modules", "vitest", "package.json").read_text())
    rel = pkg["bin"]["vitest"] if isinstance(pkg["bin"], dict) else pkg["bin"]
    return str(Path(clone, TRPC_CWD, "node_modules", "vitest", rel).resolve())


def trpc_source_aliases(clone: str) -> list[tuple[str, str]]:
    """(import specifier, source file) for every `exports` entry of the tRPC packages: `@trpc/server`,
    `@trpc/server/observable`, `@trpc/client/links/httpLink`, ... -> `packages/<pkg>/src/...`.
    The tests' own config aliases only `@trpc/<pkg>/src/`; every other import resolves through
    `exports` to the build in `dist/`, where the instrumented source never runs."""
    out = []
    for pkg in TRPC_PACKAGES:
        base = Path(clone, "packages", pkg)
        for key, target in json.loads((base / "package.json").read_text()).get("exports", {}).items():
            built = target.get("import") if isinstance(target, dict) else target
            if key == "./package.json" or not isinstance(built, str) or not built.startswith("./dist/"):
                continue
            stem = built[len("./dist/"):].rsplit(".", 1)[0]  # adapters/aws-lambda/index, observable/index
            src = next((base / "src" / (stem + ext) for ext in (".ts", ".tsx") if (base / "src" / (stem + ext)).exists()),
                       None)
            if src is None:
                raise SystemExit(f"trpc: no source for @trpc/{pkg} export {key} ({built})")
            out.append((f"@trpc/{pkg}" + key[1:], str(src)))
    return out


def trpc_source_config(clone: str) -> Path:
    """Write the trace-only Vitest config: the corpus's own `packages/tests/vitest.config.ts` plus
    exact-match aliases from each package export to its source file, so a traced focal runs as
    source however a test imports it. Returns its path (in packages/tests, beside the original)."""
    aliases = ",\n".join(f"    {{ find: /^{re_escape(spec)}$/, replacement: {json.dumps(src)} }}"
                         for spec, src in trpc_source_aliases(clone))
    path = Path(clone, TRPC_CWD, TRPC_SOURCE_CONFIG)
    path.write_text("// generated by harness/experiments/layer4/author.py (trpc_source_config): trace-only\n"
                    "import { mergeConfig } from 'vitest/config';\nimport base from './vitest.config';\n\n"
                    f"export default mergeConfig(base, {{ resolve: {{ alias: [\n{aliases},\n  ] }} }});\n")
    return path


def re_escape(spec: str) -> str:
    """An import specifier as a JS regex body (`/` and `.` and `-` escaped)."""
    return "".join("\\" + ch if ch in "/.-+*?()[]{}|^$\\" else ch for ch in spec)


def expand(cmd: list[str], root: str, python: str | None) -> list[str]:
    out = []
    for part in cmd:
        part = part.replace("{runtime}", str(RUNTIME))
        if "{node}" in part:
            part = part.replace("{node}", NODE["bin"])
        if "{vitest}" in part:
            part = part.replace("{vitest}", vitest_bin(root))
        if python:
            part = part.replace("{python}", python)
        if "*" in part:
            out += sorted(os.path.relpath(p, root) for p in glob.glob(os.path.join(root, part)))
        else:
            out.append(part)
    return out


def test_files(cmd: list[str], corpus: str) -> list[str]:
    """The gating test files a command names (excluded from every arm)."""
    if corpus == "django":
        return []  # labels; set from the label's directory below
    if corpus == "trpc":  # Vitest filters are relative to its root, packages/
        return ["packages/" + p for p in cmd if p.endswith((".test.ts", ".test.tsx"))]
    return [p for p in cmd if p.startswith(("tests/", "test/")) and p.endswith((".py", ".js"))]


def django_label_paths(labels: list[str]) -> list[str]:
    return sorted({"tests/" + label.split(".")[0] + "/" for label in labels})


def signature_of(root: str, file: str, line: int) -> tuple[str, str, int, str]:
    """(signature, definition, first line, language) of the function at file:line."""
    span = locate(os.path.join(root, file), line)
    source = Path(span.path).read_bytes()
    line_start = source.rfind(b"\n", 0, span.start) + 1
    prefix = source[line_start:span.start].decode()
    if span.language != "python" and ("=" in prefix or prefix.strip() in ("export", "export default")):
        # `res.format = function(obj)` / `export function f()`: keep what precedes the function on its line
        head_start = line_start + len(prefix) - len(prefix.lstrip())
    else:
        head_start = span.start
    head = source[head_start:span.body_start].decode().rstrip().rstrip("{").rstrip()
    sig = " ".join(line.strip() for line in head.splitlines())
    lang = "python" if span.language == "python" else span.language
    return sig, source[span.start:span.end].decode(), source[:span.start].count(b"\n") + 1, lang


def test_app_tables(root: str) -> list[str]:
    apps = [p.name for p in Path(root, "tests").iterdir() if p.is_dir()]
    return sorted(apps, key=len, reverse=True)


def filter_sql(sql: dict, apps: list[str]) -> tuple[dict, list[str]]:
    kept, dropped = {}, []
    for table, verbs in sql.items():
        if table == "sqlite_master" or any(table.startswith(app + "_") for app in apps):
            dropped.append(table)
        else:
            kept[table] = verbs
    return kept, sorted(dropped)


# ---------------------------------------------------------------- tracing
def trace_python(root: str, python: str, focal: str, cmd: list[str], out: Path) -> dict:
    env = dict(os.environ, L4_ROOT=root, L4_FOCAL=focal, L4_OUT=str(out), PYTHONPATH=str(SCE),
               PYTHONDONTWRITEBYTECODE="1")
    full = expand(cmd, root, python)
    full = full[:3] + ["-p", "harness.experiments.layer4.trace_py"] + full[3:]  # {python} -m pytest -p plugin ...
    for attempt in range(1, TRACE_ATTEMPTS + 1):
        try:
            proc = subprocess.run(full, cwd=root, env=env, capture_output=True, text=True, timeout=TRACE_TIMEOUT_S)
            break
        except subprocess.TimeoutExpired:  # an intermittent deadlock under the profiler: rerun the whole trace
            print(f"[retry] traced run timed out after {TRACE_TIMEOUT_S}s (attempt {attempt})", flush=True)
    else:
        raise SystemExit(f"traced run timed out {TRACE_ATTEMPTS} times: {' '.join(cmd)}")
    m = json.loads(out.read_text())
    m["exit_code"], m["command"], m["trace_attempts"] = proc.returncode, cmd, attempt
    m["output_tail"] = (proc.stdout + proc.stderr)[-1500:]
    return m


def trace_django(root: str, python: str, focal: str, labels: list[str], out: Path) -> dict:
    cmd = [python, "-m", "harness.experiments.layer4.trace_django", "--in-tests-only", "--root", root, "--focal", focal,
           "--out", str(out), "--", *labels]
    try:
        proc = subprocess.run(cmd, cwd=root, env=dict(os.environ, PYTHONPATH=str(SCE), PYTHONDONTWRITEBYTECODE="1"),
                              capture_output=True, text=True, timeout=3600)
    except subprocess.TimeoutExpired:
        raise SystemExit(f"traced run timed out after 3600 s: {' '.join(labels)}") from None
    m = json.loads(out.read_text())
    m["command"] = ["{python}", "-m", "harness.experiments.layer4.trace_django", "--in-tests-only", "--root", ".",
                    "--focal", focal, "--", *labels]
    m["output_tail"] = (proc.stdout + proc.stderr)[-1500:]
    return m


def trace_trpc(clone: str, file: str, line: int, focal: str, cmd: list[str], out: Path, builder,
               source: bool = False, control: bool = False) -> dict:
    """Vitest 0.32 in fork mode (`--no-threads`), the runtime in NODE_OPTIONS,
    one output per process merged; locations rebased to the index root.
    `source`: under `trpc_source_config` (every `@trpc/*` import as source). `control`: the
    focal is not instrumented (the environment check of a failed trace)."""
    full = expand(cmd, clone, None)
    config = trpc_source_config(clone) if source else None
    if config:
        full = full[:2] + ["--config", str(config)] + full[2:]  # {node} {vitest} --config ... --run ...
    try:
        for attempt in range(1, TRACE_ATTEMPTS + 1):
            try:
                raw = run_traced(clone, file, line, full, str(out), cwd=TRPC_CWD, multi=True, timeout=TRACE_TIMEOUT_S,
                                 env={"NODE_OPTIONS": f"--require {RUNTIME} --enable-source-maps"}, instrument=not control)
                break
            except subprocess.TimeoutExpired:
                print(f"[retry] traced run timed out after {TRACE_TIMEOUT_S}s (attempt {attempt})", flush=True)
        else:
            raise SystemExit(f"traced run timed out {TRACE_TIMEOUT_S} s x {TRACE_ATTEMPTS}: {' '.join(cmd)}")
    finally:
        if config:
            config.unlink(missing_ok=True)
    m = map_manifest(rebase(raw, TRPC_PREFIX), builder, focal)
    m["command"], m["trace_attempts"] = cmd, attempt
    m["vitest_config"] = f"{TRPC_CWD}/{TRPC_SOURCE_CONFIG}" if config else None
    m["raw"] = {k: raw.get(k) for k in ("ancestors", "chains", "descendants", "source_mapping", "processes")}
    m["output_tail"] = raw.get("output_tail", "")
    return m


def trace_express(root: str, file: str, line: int, focal: str, cmd: list[str], out: Path, builder,
                  control: bool = False) -> dict:
    full = expand(cmd, root, None)
    full = full[:1] + ["--require", str(RUNTIME)] + full[1:]
    raw = run_traced(root, file, line, full, str(out), instrument=not control)
    m = map_manifest(raw, builder, focal)
    m["command"] = cmd
    m["raw"] = {k: raw.get(k) for k in ("ancestors", "chains", "descendants", "source_mapping")}
    return m


def oracle_callees(m: dict, known: set[str], focal: str) -> list[str]:
    """What the focal function uses, for the backward oracle: Python - repo
    functions it calls directly (`descendant_depth` 1); Node - coverage has
    no call order, so every repo function that ran while it was on the
    stack. Most-called first, at most `MAX_GOLD` - 1 with the callers."""
    desc = m.get("descendants", {})
    depth = m.get("descendant_depth")
    names = [n for n in desc if n in known and n != focal and (depth is None or depth.get(n) == 1)]
    return sorted(names, key=lambda n: (-desc[n], n))


# ---------------------------------------------------------------- one task
def prompt_for(spec: dict, short: str, file: str, signature: str) -> str:
    corpus = spec["corpus"]
    if spec["family"] == "forward_blast":
        hops = spec["max_depth"]
        text = (f"In the {corpus} repository, `{spec['qname']}` (in `{file}`) is about to change: "
                f"{spec['change']['description']} Which functions and methods of the repository are affected - "
                f"the code that calls `{short}` at runtime, directly or through at most {hops} call"
                f"{'s' if hops > 1 else ''} in between counting the direct call as one, and would have to be "
                f"reviewed or adapted?")
        if corpus == "django":
            text += " Also name the database side effects that happen while it runs and the tables it touches."
        return text
    return (f"In the {corpus} repository, the body of `{spec['qname']}` (in `{file}`) has been removed. "
            f"Implement `{short}` again so that the code that calls it keeps working and the repository behaves as "
            f"before. Keep its signature: `{signature}`.")


LAST_TRACE: dict[str, dict] = {}  # task id -> the latest trace, saved with a rejected candidate


def check_trace(tid: str, m: dict) -> None:
    """A trace is usable only if the run passed and the focal function ran."""
    calls = m.get("focal_calls", 0)
    calls = sum(calls.values()) if isinstance(calls, dict) else calls
    if m.get("exit_code") != 0:
        raise SystemExit(f"{tid}: trace exited {m.get('exit_code')}\n{m.get('output_tail', '')[-2500:]}")
    if not calls:
        raise SystemExit(f"{tid}: the focal function never ran in the traced tests\n{m.get('output_tail', '')[-1500:]}")


def diagnostics(m: dict | None) -> dict:
    """What a rejected candidate's trace showed (saved under rejected/)."""
    if not m:
        return {}
    chains = sorted((m.get("raw") or {}).get("chains", m.get("chains", {})).items(), key=lambda kv: -kv[1])[:8]
    return {"exit_code": m.get("exit_code"), "control_exit_code": m.get("control_exit_code"),
            "env_skipped": m.get("env_skipped"), "vitest_config": m.get("vitest_config"), "focal_calls": m.get("focal_calls"), "n_tests": len(m.get("tests", [])),
            "tests_sample": m.get("tests", [])[:5], "ancestors": m.get("ancestors"),
            "ancestors_unmapped": m.get("ancestors_unmapped"), "ancestor_depth": m.get("ancestor_depth"),
            "outside_ancestors": m.get("outside_ancestors"), "top_chains": chains,
            "descendants": m.get("descendants"), "output_tail": (m.get("output_tail") or "")[-3000:]}


def author(spec: dict, pythons: dict, names_dir: str | None, builders: dict, sce_commit: str) -> dict:
    corpus, qname, family = spec["corpus"], spec["qname"], spec["family"]
    root = clone_root(corpus)
    prefix = TRPC_PREFIX + "/" if corpus == "trpc" else ""
    names = load_names(corpus, names_dir)
    from prism.graph.entry_points import example_zone
    # gold and oracle names: application functions - not test files, not the
    # example zone (docs_src/, examples/, ...: the rule the breadcrumbs keep)
    known = {r[0] for r in names if not r[4] and not example_zone(r[1])}
    row = next((r for r in names if r[0] == qname), None)
    if row is None or row[0] not in known:
        raise SystemExit(f"{spec['task_id']}: {qname} is not an indexed application function")
    index_file = row[1].replace(os.sep, "/")
    file = prefix + index_file  # relative to the clone root
    signature, definition, line, language = signature_of(root, file, row[2])
    short = qname.rsplit(".", 1)[-1]
    tid = spec["task_id"]
    python = pythons.get(corpus)
    tmp = Path(tempfile.mkdtemp(prefix=f"l4_{tid}_"))

    def trace(cmd_or_labels, control=False):
        """`control`: the same run with nothing traced (Python: a focal that never exists; Node: the
        focal not instrumented) - a failure there too is the environment's, not the candidate's."""
        focal = CONTROL_FOCAL if control else qname
        if corpus == "django":
            return trace_django(root, python, focal, cmd_or_labels, tmp / "m.json")
        if corpus == "fastapi":
            return trace_python(root, python, focal, cmd_or_labels, tmp / "m.json")
        if corpus == "trpc":
            return trace_trpc(root, file, line, qname, cmd_or_labels, tmp / "raw.json", builders[corpus],
                              source=spec.get("trpc_source", False), control=control)
        return trace_express(root, file, line, qname, cmd_or_labels, tmp / "raw.json", builders[corpus], control=control)

    task = {
        "schema_version": "layer4.task.v1", "task_id": tid, "family": family, "corpus": corpus,
        "pinned_commit": PINNED[corpus]["pinned_commit"],
        "focal": {"qname": qname, "file": file, "line": line, "index_file": index_file, "language": language,
                  "signature": signature, "is_async": signature.startswith("async "),
                  "definition_sha256": hashlib.sha256(definition.encode()).hexdigest()},
        "prompt": prompt_for(spec, short, file, signature),
        "provenance": {"tracer": {"django": "trace_django", "fastapi": "trace_py", "express": "trace_node",
                                   "trpc": "trace_node"}[corpus],
                       "sce_commit": sce_commit, "authored_at": datetime.date.today().isoformat()},
    }
    gates_record = None
    if family == "forward_blast":
        m = trace(spec.get("trace_labels") or spec["trace"])
        LAST_TRACE[tid] = m
        if m.get("exit_code") != 0 and spec.get("control_on_failure"):
            c = trace(spec.get("trace_labels") or spec["trace"], control=True)
            m["control_exit_code"] = c.get("exit_code")
            if c.get("exit_code") != 0:
                raise SystemExit(f"{tid}: environment fault: the tests fail with nothing traced too (traced exit "
                                 f"{m.get('exit_code')}, control exit {c.get('exit_code')})\n"
                                 f"{c.get('output_tail', '')[-2000:]}")
        check_trace(tid, m)
        # the shallowest depth from max_depth up to max_depth_limit that yields gold
        depth = spec["max_depth"]
        while True:
            gold = T.gold_from_manifest(m, "upstream", min_count=spec.get("min_count", 1), known=known, focal=qname,
                                        max_depth=depth)
            if gold or depth >= spec.get("max_depth_limit", spec["max_depth"]):
                break
            depth += 1
        if not gold:
            raise SystemExit(f"{tid}: empty gold within {depth} call hops ({len(m.get('ancestors', {}))} traced "
                             f"ancestor locations, {len(m.get('ancestor_depth', {}))} with a depth)")
        if len(gold) > MAX_GOLD:
            raise SystemExit(f"{tid}: {len(gold)} gold symbols > {MAX_GOLD}; lower max_depth")
        if len(gold) < spec.get("min_gold", 1):
            raise SystemExit(f"{tid}: {len(gold)} gold symbols < {spec['min_gold']} within {depth} call hops")
        side_effects = tables = None
        if corpus == "django":
            kept, dropped = filter_sql(m.get("sql", {}), test_app_tables(root))
            m["sql_dropped_tables"] = dropped
            side_effects, tables = T.side_effects_from_sql(kept), T.tables_from_sql(kept)
        task["change"] = spec["change"]
        task["answer_contract"] = "forward_json_v1"
        task["arm_view"] = {"seed_symbol": qname, "task_type": "blast", "oracle_symbols": [qname, *gold],
                            "oracle_required_context": [], "root_imports": [], "mask": {"symbols": [], "mode": "none"},
                            "exclude_paths": []}
        task["gold"] = {"source": "runtime_trace", "direction": "upstream", "affected_symbols": gold,
                        "side_effects": side_effects, "tables": tables,
                        "trace_manifest": f"{out_label()}/manifests/{tid}.json", "trace_command": m["command"],
                        "min_count": spec.get("min_count", 1), "max_depth": depth}
    else:
        if corpus == "django":
            cmd = ["{python}", "tests/runtests.py", "--parallel=1", *spec["test_labels"]]
            excluded = django_label_paths(spec["test_labels"])
        else:
            cmd = spec["tests"]
            excluded = test_files(expand(cmd, root, None), corpus)
        run_cmd = expand(cmd, root, python)
        cwd = TRPC_CWD if corpus == "trpc" else "."
        g = S.gates(S.SpliceTask(tid, root, file, line, run_cmd, cwd=cwd, name=short, timeout=1800,
                                 env={"PYTHONDONTWRITEBYTECODE": "1"}))
        gates_record = g
        if not g["valid"]:
            raise SystemExit(f"{tid}: gates invalid: positive {g['positive']['passed']} "
                             f"negative {g['negative']['passed']}\n{g['positive']['output_tail'][-800:]}")
        m = trace(spec["test_labels"] if corpus == "django" else spec["tests"])
        LAST_TRACE[tid] = m
        check_trace(tid, m)
        callers = T.gold_from_manifest(m, "upstream", known=known, focal=qname, max_depth=BACKWARD_ORACLE_DEPTH)
        callees = oracle_callees(m, known, qname)
        m["oracle_callers"], m["oracle_callees"] = callers, callees
        task["answer_contract"] = "backward_code_v1"
        task["arm_view"] = {"seed_symbol": qname, "task_type": "debug", "oracle_symbols": list(dict.fromkeys([qname, *callers, *callees]))[:MAX_GOLD],
                            "oracle_required_context": [], "root_imports": [],
                            "mask": {"symbols": [qname], "mode": "body"}, "exclude_paths": excluded}
        task["gold"] = {"source": "test_execution", "test_command": cmd, "cwd": cwd, "timeout_s": 1800,
                        "stub_message": "STUB_IMPLEMENTATION", "tests": m.get("tests", [])}
    task["validity"] = {"trace_exit_code": m["exit_code"], "async_window": bool(m.get("async_window")),
                        "contamination": {"arm0_score": None, "flagged": None, "seeds": []}}
    if gates_record:
        task["validity"]["positive_gate"] = {"passed": gates_record["positive"]["passed"],
                                             "seconds": gates_record["positive"]["seconds"]}
        task["validity"]["negative_gate"] = {"passed": gates_record["negative"]["passed"],
                                             "exit_code": gates_record["negative"]["exit_code"],
                                             "stub_seen": gates_record["negative"]["stub_seen"]}
    if m["exit_code"] != 0:
        raise SystemExit(f"{tid}: trace exited {m['exit_code']}\n{m.get('output_tail', '')[-1500:]}")
    errors = T.validate_task(task, known=known)
    if errors:
        raise SystemExit(f"{tid}: invalid task:\n  " + "\n  ".join(errors))
    for sub in ("tasks", "manifests", "gates"):
        (OUT / sub).mkdir(parents=True, exist_ok=True)
    m.pop("output_tail", None)
    (OUT / "manifests" / f"{tid}.json").write_text(json.dumps(m, indent=1, sort_keys=True) + "\n")
    (OUT / "tasks" / f"{tid}.json").write_text(json.dumps(task, indent=1) + "\n")
    if gates_record:
        (OUT / "gates" / f"{tid}.json").write_text(json.dumps(gates_record, indent=1, sort_keys=True) + "\n")
    return task


def author_with_fallbacks(spec: dict, pythons: dict, names_dir: str | None, builders: dict, sce_commit: str) -> dict:
    """A spec may list `alternatives` (overrides of the spec: another focal
    function with its own tests or trace, or the same focal with another
    trace): the first that yields a valid task is kept; the rejected ones and
    why are recorded in the task's manifest, with their traces under
    rejected/."""
    rejected = []
    for n, candidate in enumerate([spec, *({**spec, **alt} for alt in spec.get("alternatives", []))]):
        candidate = {k: v for k, v in candidate.items() if k != "alternatives"}
        LAST_TRACE.pop(spec["task_id"], None)
        try:
            task = author(candidate, pythons, names_dir, builders, sce_commit)
        except SystemExit as exc:
            print(f"[reject] {spec['task_id']}: {candidate['qname']}: {str(exc)[:1500]}", flush=True)
            rejected.append({"qname": candidate["qname"], "candidate": n, "reason": str(exc)[:2000]})
            diag = OUT / "rejected" / f"{spec['task_id']}__{n}__{candidate['qname']}.json"
            diag.parent.mkdir(parents=True, exist_ok=True)
            diag.write_text(json.dumps({"task_id": spec["task_id"], "qname": candidate["qname"], "reason": str(exc),
                                        "trace": diagnostics(LAST_TRACE.get(spec["task_id"]))},
                                       indent=1, sort_keys=True, default=str) + "\n")
            continue
        if rejected:
            path = OUT / "manifests" / f"{task['task_id']}.json"
            m = json.loads(path.read_text())
            m["rejected_candidates"] = rejected
            path.write_text(json.dumps(m, indent=1, sort_keys=True) + "\n")
        return task
    raise SystemExit(f"{spec['task_id']}: every candidate rejected: {[r['qname'] for r in rejected]}")


def out_label() -> str:
    """OUT as recorded in a task: repository-relative when inside the repository."""
    return OUT.relative_to(SCE).as_posix() if OUT.is_relative_to(SCE) else OUT.as_posix()


def reason_code(message: str) -> str:
    return next((code for needle, code in REASON_CODES if needle in message), "other")


def load_specs(path: str | None) -> list[dict]:
    """`SPECS` of a spec module: benchmarks/layer4/specs.py by default, or the file given."""
    if not path:
        from benchmarks.layer4.specs import SPECS
        return SPECS
    import importlib.util
    spec = importlib.util.spec_from_file_location("layer4_specs_override", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SPECS


def author_p3b(specs: list[dict], pythons: dict, names_dir: str | None, builders: dict, sce_commit: str,
               per_corpus: int = P3B_PER_CORPUS, log_path: Path | None = None) -> dict:
    """P3b eligibility: per corpus, specs in their (draw) order, each authored with the
    P3B_MIN_GOLD..MAX_GOLD rule, until `per_corpus` are kept; every decision is appended to
    `log_path` (resumable: a decided spec is not traced again). Returns the log.

    Decisions: `kept`; `skipped` (an ELIGIBILITY_REASONS reason: the candidate's property);
    `env_fault` (the run fails with nothing traced too, or times out: the environment's - set aside,
    not counted as ineligible; more than P3B_MAX_ENV_FAULTS in a corpus aborts). Any other failure
    aborts with nothing logged for the spec. Each spec runs with `control_on_failure` (a failed
    trace is re-run untraced to tell the two apart) and, for tRPC, `trpc_source`."""
    log_path = log_path or OUT / "authoring_log.json"
    log = json.loads(log_path.read_text()) if log_path.exists() else {"decisions": {}}
    decisions = log["decisions"]
    for corpus in dict.fromkeys(s["corpus"] for s in specs):
        ordered = sorted((s for s in specs if s["corpus"] == corpus), key=lambda s: s.get("draw_rank", 0))
        for spec in ordered:
            mine = [decisions[s["task_id"]] for s in ordered if s["task_id"] in decisions]
            if sum(d["status"] == "kept" for d in mine) >= per_corpus:
                break
            if sum(d["status"] == "env_fault" for d in mine) > P3B_MAX_ENV_FAULTS:
                raise SystemExit(f"{corpus}: more than {P3B_MAX_ENV_FAULTS} environment faults - the test "
                                 f"environment is broken; fix it and resume")
            if spec["task_id"] in decisions:
                continue
            if corpus in ("express", "trpc") and corpus not in builders:
                from prism.engine import PrismEngine
                builders[corpus] = PrismEngine.from_repo(corpus_root(corpus)).builder
            LAST_TRACE.pop(spec["task_id"], None)
            base = {"corpus": corpus, "qname": spec["qname"], "draw_rank": spec.get("draw_rank")}
            try:
                task = author({**spec, "min_gold": P3B_MIN_GOLD, "control_on_failure": True,
                               "trpc_source": corpus == "trpc"}, pythons, names_dir, builders, sce_commit)
                decisions[spec["task_id"]] = {"status": "kept", **base, "gold": len(task["gold"]["affected_symbols"])}
                print(f"[keep] {spec['task_id']} {spec['qname']}: {len(task['gold']['affected_symbols'])} gold", flush=True)
            except SystemExit as exc:
                msg, code = str(exc), reason_code(str(exc))
                if code not in ELIGIBILITY_REASONS | ENVIRONMENT_REASONS:
                    raise SystemExit(f"{spec['task_id']}: not an eligibility failure - fix the tooling, then "
                                     f"resume (nothing logged for it):\n{msg}") from exc
                status = "env_fault" if code in ENVIRONMENT_REASONS else "skipped"
                decisions[spec["task_id"]] = {"status": status, **base, "reason": code, "detail": msg[:600],
                                              "trace": diagnostics(LAST_TRACE.get(spec["task_id"]))}
                print(f"[{'env' if status == 'env_fault' else 'skip'}] {spec['task_id']} {spec['qname']}: {code}", flush=True)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(json.dumps(log, indent=1, sort_keys=True, default=str) + "\n")
    return log


def main(argv=None) -> int:
    global OUT
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--python", action="append", default=[], help="corpus=interpreter (fastapi, django)")
    p.add_argument("--names-dir", help="cache of the corpora's indexed names (<corpus>_names.json)")
    p.add_argument("--only", help="comma-separated task ids")
    p.add_argument("--corpus", help="only this corpus's specs")
    p.add_argument("--node", default="node", help="Node binary for tRPC (Node 20)")
    p.add_argument("--specs", help="a spec module other than benchmarks/layer4/specs.py (e.g. specs_p3b.py)")
    p.add_argument("--out", help="output directory for tasks/, manifests/, gates/ (default benchmarks/layer4)")
    p.add_argument("--eligibility", choices=("p3b",), help="p3b: 2..15 gold, first --per-corpus kept per corpus, "
                   "in draw order, every decision logged to OUT/authoring_log.json")
    p.add_argument("--per-corpus", type=int, default=P3B_PER_CORPUS)
    args = p.parse_args(argv)
    if args.out:
        OUT = Path(args.out).resolve()
    SPECS = load_specs(args.specs)
    NODE["bin"] = args.node
    pythons = dict(x.split("=", 1) for x in args.python)
    wanted = set(args.only.split(",")) if args.only else None
    sce_commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=SCE, capture_output=True,
                                text=True).stdout.strip()
    builders = {}
    if args.eligibility == "p3b":
        chosen = [s for s in SPECS if (not wanted or s["task_id"] in wanted) and (not args.corpus or s["corpus"] == args.corpus)]
        # one log per corpus: the Kaggle cell authors corpora in parallel lanes
        log_path = OUT / (f"authoring_log_{args.corpus}.json" if args.corpus else "authoring_log.json")
        log = author_p3b(chosen, pythons, args.names_dir, builders, sce_commit, per_corpus=args.per_corpus,
                         log_path=log_path)
        kept = {}
        for d in log["decisions"].values():
            kept[d["corpus"]] = kept.get(d["corpus"], 0) + (d["status"] == "kept")
        print(f"[p3b] kept per corpus: {kept}", flush=True)
        return 0
    failed: list[str] = []
    for spec in SPECS:
        if (wanted and spec["task_id"] not in wanted) or (args.corpus and spec["corpus"] != args.corpus):
            continue
        if spec["corpus"] in ("express", "trpc") and spec["corpus"] not in builders:
            from prism.engine import PrismEngine
            builders[spec["corpus"]] = PrismEngine.from_repo(corpus_root(spec["corpus"])).builder
        try:
            t = author_with_fallbacks(spec, pythons, args.names_dir, builders, sce_commit)
        except SystemExit as exc:  # report it and go on with the other tasks
            print(f"[fail] {exc}", flush=True)
            failed.append(spec["task_id"])
            continue
        gold = t["gold"].get("affected_symbols") or t["arm_view"]["oracle_symbols"][1:]
        print(f"[ok] {t['task_id']}: {len(gold)} {'gold' if t['family'] == 'forward_blast' else 'oracle symbols'}"
              f"{', tables ' + str(t['gold']['tables']) if t['gold'].get('tables') else ''}"
              f"{', effects ' + str(t['gold']['side_effects']) if t['gold'].get('side_effects') else ''}", flush=True)
    if failed:
        print(f"[fail] not authored: {failed}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
