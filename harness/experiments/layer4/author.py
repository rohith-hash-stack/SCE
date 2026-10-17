"""Author Layer 4 tasks from `benchmarks/layer4/specs.py`: trace, derive
gold, run gates, validate, write.

    PYTHONPATH=. python -m harness.experiments.layer4.author \\
        --python fastapi=<venv>/bin/python --python django=<venv>/bin/python \\
        [--names-dir <dir>] [--only l4_fwd_django_01,...]

Writes, per task, under `benchmarks/layer4/`:
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
from harness.experiments.layer4.trace_node import RUNTIME, map_manifest, run_traced

SCE = Path(__file__).resolve().parents[3]
OUT = SCE / "benchmarks" / "layer4"
PINNED = json.loads((SCE / "benchmarks" / "corpora" / "pinned_commits.json").read_text())
BACKWARD_ORACLE_DEPTH = 1
#: a clean traced FastAPI suite takes ~1 min; its websocket tests deadlock
#: under the profiler now and then, so a stuck run is killed and rerun
TRACE_TIMEOUT_S = 600
TRACE_ATTEMPTS = 3
MAX_GOLD = T.MAX_ANSWER_SYMBOLS


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


def expand(cmd: list[str], root: str, python: str | None) -> list[str]:
    out = []
    for part in cmd:
        part = part.replace("{runtime}", str(RUNTIME))
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
    return [p for p in cmd if p.startswith(("tests/", "test/")) and p.endswith((".py", ".js"))]


def django_label_paths(labels: list[str]) -> list[str]:
    return sorted({"tests/" + label.split(".")[0] + "/" for label in labels})


def signature_of(root: str, file: str, line: int) -> tuple[str, str, int, str]:
    """(signature, definition, first line, language) of the function at file:line."""
    span = locate(os.path.join(root, file), line)
    source = Path(span.path).read_bytes()
    line_start = source.rfind(b"\n", 0, span.start) + 1
    prefix = source[line_start:span.start].decode()
    if span.language != "python" and "=" in prefix:  # `res.format = function(obj)`: name the assignment too
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
    proc = subprocess.run(cmd, cwd=root, env=dict(os.environ, PYTHONPATH=str(SCE), PYTHONDONTWRITEBYTECODE="1"),
                          capture_output=True, text=True, timeout=3600)
    m = json.loads(out.read_text())
    m["command"] = ["{python}", "-m", "harness.experiments.layer4.trace_django", "--in-tests-only", "--root", ".",
                    "--focal", focal, "--", *labels]
    m["output_tail"] = (proc.stdout + proc.stderr)[-1500:]
    return m


def trace_express(root: str, file: str, line: int, focal: str, cmd: list[str], out: Path, builder) -> dict:
    full = expand(cmd, root, None)
    full = full[:1] + ["--require", str(RUNTIME)] + full[1:]
    raw = run_traced(root, file, line, full, str(out))
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


def author(spec: dict, pythons: dict, names_dir: str | None, builders: dict, sce_commit: str) -> dict:
    corpus, qname, family = spec["corpus"], spec["qname"], spec["family"]
    root = corpus_root(corpus)
    names = load_names(corpus, names_dir)
    from prism.graph.entry_points import example_zone
    # gold and oracle names: application functions - not test files, not the
    # example zone (docs_src/, examples/, ...: the rule the breadcrumbs keep)
    known = {r[0] for r in names if not r[4] and not example_zone(r[1])}
    row = next((r for r in names if r[0] == qname), None)
    if row is None or row[0] not in known:
        raise SystemExit(f"{spec['task_id']}: {qname} is not an indexed application function")
    file = row[1].replace(os.sep, "/")
    signature, definition, line, language = signature_of(root, file, row[2])
    short = qname.rsplit(".", 1)[-1]
    tid = spec["task_id"]
    python = pythons.get(corpus)
    tmp = Path(tempfile.mkdtemp(prefix=f"l4_{tid}_"))

    def trace(cmd_or_labels):
        if corpus == "django":
            return trace_django(root, python, qname, cmd_or_labels, tmp / "m.json")
        if corpus == "fastapi":
            return trace_python(root, python, qname, cmd_or_labels, tmp / "m.json")
        return trace_express(root, file, line, qname, cmd_or_labels, tmp / "raw.json", builders[corpus])

    task = {
        "schema_version": "layer4.task.v1", "task_id": tid, "family": family, "corpus": corpus,
        "pinned_commit": PINNED[corpus]["pinned_commit"],
        "focal": {"qname": qname, "file": file, "line": line, "index_file": file, "language": language,
                  "signature": signature, "is_async": signature.startswith("async "),
                  "definition_sha256": hashlib.sha256(definition.encode()).hexdigest()},
        "prompt": prompt_for(spec, short, file, signature),
        "provenance": {"tracer": {"django": "trace_django", "fastapi": "trace_py", "express": "trace_node"}[corpus],
                       "sce_commit": sce_commit, "authored_at": datetime.date.today().isoformat()},
    }
    gates_record = None
    if family == "forward_blast":
        m = trace(spec.get("trace_labels") or spec["trace"])
        gold = T.gold_from_manifest(m, "upstream", min_count=spec.get("min_count", 1), known=known, focal=qname,
                                    max_depth=spec["max_depth"])
        if not gold:
            raise SystemExit(f"{tid}: empty gold")
        if len(gold) > MAX_GOLD:
            raise SystemExit(f"{tid}: {len(gold)} gold symbols > {MAX_GOLD}; lower max_depth")
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
                        "trace_manifest": f"benchmarks/layer4/manifests/{tid}.json", "trace_command": m["command"],
                        "min_count": spec.get("min_count", 1), "max_depth": spec["max_depth"]}
    else:
        if corpus == "django":
            cmd = ["{python}", "tests/runtests.py", "--parallel=1", *spec["test_labels"]]
            excluded = django_label_paths(spec["test_labels"])
        else:
            cmd = spec["tests"]
            excluded = test_files(expand(cmd, root, None), corpus)
        run_cmd = expand(cmd, root, python)
        g = S.gates(S.SpliceTask(tid, root, file, line, run_cmd, name=short, timeout=1800,
                                 env={"PYTHONDONTWRITEBYTECODE": "1"}))
        gates_record = g
        if not g["valid"]:
            raise SystemExit(f"{tid}: gates invalid: positive {g['positive']['passed']} "
                             f"negative {g['negative']['passed']}\n{g['positive']['output_tail'][-800:]}")
        m = trace(spec["test_labels"] if corpus == "django" else spec["tests"])
        callers = T.gold_from_manifest(m, "upstream", known=known, focal=qname, max_depth=BACKWARD_ORACLE_DEPTH)
        callees = oracle_callees(m, known, qname)
        m["oracle_callers"], m["oracle_callees"] = callers, callees
        task["answer_contract"] = "backward_code_v1"
        task["arm_view"] = {"seed_symbol": qname, "task_type": "debug", "oracle_symbols": list(dict.fromkeys([qname, *callers, *callees]))[:MAX_GOLD],
                            "oracle_required_context": [], "root_imports": [],
                            "mask": {"symbols": [qname], "mode": "body"}, "exclude_paths": excluded}
        task["gold"] = {"source": "test_execution", "test_command": cmd, "cwd": ".", "timeout_s": 1800,
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


def main(argv=None) -> int:
    from benchmarks.layer4.specs import SPECS
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--python", action="append", default=[], help="corpus=interpreter (fastapi, django)")
    p.add_argument("--names-dir", help="cache of the corpora's indexed names (<corpus>_names.json)")
    p.add_argument("--only", help="comma-separated task ids")
    args = p.parse_args(argv)
    pythons = dict(x.split("=", 1) for x in args.python)
    wanted = set(args.only.split(",")) if args.only else None
    sce_commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=SCE, capture_output=True,
                                text=True).stdout.strip()
    builders = {}
    for spec in SPECS:
        if wanted and spec["task_id"] not in wanted:
            continue
        if spec["corpus"] == "express" and "express" not in builders:
            from prism.engine import PrismEngine
            builders["express"] = PrismEngine.from_repo(corpus_root("express")).builder
        t = author(spec, pythons, args.names_dir, builders, sce_commit)
        gold = t["gold"].get("affected_symbols") or t["arm_view"]["oracle_symbols"][1:]
        print(f"[ok] {t['task_id']}: {len(gold)} {'gold' if t['family'] == 'forward_blast' else 'oracle symbols'}"
              f"{', tables ' + str(t['gold']['tables']) if t['gold'].get('tables') else ''}"
              f"{', effects ' + str(t['gold']['side_effects']) if t['gold'].get('side_effects') else ''}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
