"""Clones a real, public Git repository and runs SCE's full pipeline
against it: proves the cross-file linker builds `G_C` without crashing on
real-world Python (decorators, type hints, relative imports, complex
`__init__.py` barrels), then reports the same compression/coverage/
validity metrics `run_benchmark.py` computes on the synthetic fixtures -
against a target you name or one SCE auto-selects.

Usage:
    python -m benchmarks.clone_eval --repo https://github.com/encode/starlette.git \
        --target "starlette.applications.Starlette.__call__" --budget 4000

    python -m benchmarks.clone_eval --repo https://github.com/pallets/flask.git --live
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _ensure_sce_importable() -> None:
    try:
        import sce  # noqa: F401
    except ImportError:
        src_path = str(PROJECT_ROOT / "src")
        if src_path not in sys.path:
            sys.path.insert(0, src_path)


_ensure_sce_importable()

from sce.cli import build_pipeline  # noqa: E402
from sce.graph.concrete_builder import ConcreteGraphBuilder  # noqa: E402
from sce.graph.metamodel import SemanticMetamodel  # noqa: E402
from sce.serializers.markdown import render_markdown  # noqa: E402
from sce.slicer.distance import DistanceConfig, DistanceEngine  # noqa: E402
from sce.slicer.knapsack import ContextKnapsackPacker  # noqa: E402

from benchmarks.openai_client import LLMClient, OpenAIClientError  # noqa: E402
from benchmarks.raw_context import RawContextError, build_raw_context  # noqa: E402
from benchmarks.run_benchmark import BenchmarkError, run_single_benchmark_from_pipeline  # noqa: E402
from benchmarks.tokenizer import count_tokens  # noqa: E402

DEFAULT_CACHE_DIR = PROJECT_ROOT / ".benchmarks" / "clones"
DEFAULT_BUDGET = 4000
CLONE_TIMEOUT_SECONDS = 300

ARCHITECTURE_EXPLANATION_SYSTEM_PROMPT = (
    "You are a senior software engineer doing an architecture review. You will be given a "
    "context package describing part of a real, open-source codebase. Explain what the target "
    "function/method does, what it depends on, and name any architectural invariant you notice "
    "(for example, a database write that should be preceded by an authentication check). Be "
    "concise: at most one short paragraph plus a bullet list of dependencies."
)
ARCHITECTURE_EXPLANATION_USER_TEMPLATE = """CONTEXT PACKAGE:
{context}

TASK:
Explain the architecture of `{target}` based only on the context above."""


class CloneError(Exception):
    """Raised when a repository can't be cloned or found in the cache."""


class TargetSelectionError(Exception):
    """Raised when no usable target entrypoint could be found or auto-selected."""


def derive_repo_name(url: str) -> str:
    """`https://github.com/encode/starlette.git` -> `starlette`;
    `git@github.com:pallets/flask.git` -> `flask`; a bare local path's own
    directory name works too, so tests can pass a throwaway local repo.
    """
    trimmed = url.rstrip("/")
    if trimmed.endswith(".git"):
        trimmed = trimmed[: -len(".git")]
    if "://" in trimmed or trimmed.startswith("git@"):
        # Handle both URL and scp-like (git@host:org/repo) forms.
        tail = trimmed.split(":")[-1] if trimmed.startswith("git@") else urlparse(trimmed).path
        name = tail.rstrip("/").rsplit("/", 1)[-1]
    else:
        name = Path(trimmed).name
    if not name:
        raise CloneError(f"could not derive a repository name from '{url}'")
    return name


def clone_repo(url: str, cache_dir: Path = DEFAULT_CACHE_DIR, force: bool = False) -> Path:
    """Clone `url` with `--depth 1` into `cache_dir/<repo_name>`, reusing an
    existing clone unless `force` is set. Accepts anything `git clone`
    accepts, including a local path - useful for hermetic tests.
    """
    repo_name = derive_repo_name(url)
    dest = cache_dir / repo_name

    if dest.exists():
        if not force:
            print(f"Using cached clone at {dest} (pass --force-clone to re-clone)", file=sys.stderr)
            return dest
        import shutil

        shutil.rmtree(dest)

    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", url, str(dest)],
            capture_output=True,
            text=True,
            timeout=CLONE_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise CloneError("git is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise CloneError(f"cloning '{url}' timed out after {CLONE_TIMEOUT_SECONDS}s") from exc

    if result.returncode != 0:
        raise CloneError(f"git clone failed for '{url}':\n{result.stderr.strip()}")

    return dest


def auto_select_target(builder: ConcreteGraphBuilder, tag_matrix: dict[str, set[str]]) -> str:
    """Pick a reasonable entrypoint when the caller doesn't name one: prefer
    a `#route_handler`-tagged symbol (an obvious "core method" of a web
    framework), else fall back to the function/method with the highest
    call-graph degree (in + out edges) as a proxy for "central to the
    codebase".
    """
    route_handlers = sorted(
        qname
        for qname, tags in tag_matrix.items()
        if "#route_handler" in tags and builder.symbol_table.get(qname) is not None
    )
    if route_handlers:
        return route_handlers[0]

    candidates = [
        symbol.qualified_name for symbol in builder.symbol_table if symbol.kind in ("function", "method")
    ]
    if not candidates:
        raise TargetSelectionError(
            "no function or method symbols were found to auto-select a target from - pass --target explicitly"
        )

    def degree(qname: str) -> int:
        if qname not in builder.graph:
            return 0
        return builder.graph.in_degree(qname) + builder.graph.out_degree(qname)

    candidates.sort(key=lambda q: (-degree(q), q))
    return candidates[0]


def run_live_architecture_query(
    builder: ConcreteGraphBuilder,
    tag_matrix: dict[str, set[str]],
    target: str,
    budget: int,
    model: str,
    client: LLMClient,
) -> dict[str, dict]:
    """Sends both the raw-dump and SCE-sliced context for `target` to the
    model with an architecture-explanation prompt, and returns both
    responses plus usage - a qualitative side-by-side, not an automated
    pass/fail grade (unlike the live_eval.py tasks, "does this explanation
    correctly reason about the architecture" doesn't reduce to a cheap,
    trustworthy static check).
    """
    metamodel = SemanticMetamodel()
    distance_engine = DistanceEngine(metamodel, tag_matrix, DistanceConfig())
    pack_result = ContextKnapsackPacker(token_budget=budget).pack(target, builder, tag_matrix, distance_engine)
    sce_text = render_markdown(pack_result, tag_matrix)

    try:
        raw_text = build_raw_context(builder, target).text
    except RawContextError as exc:
        raise CloneError(str(exc)) from exc

    outputs: dict[str, dict] = {}
    for variant, context_text in (("raw", raw_text), ("sce", sce_text)):
        user_prompt = ARCHITECTURE_EXPLANATION_USER_TEMPLATE.format(context=context_text, target=target)
        call = client.complete(model=model, system=ARCHITECTURE_EXPLANATION_SYSTEM_PROMPT, user=user_prompt)
        outputs[variant] = {
            "context_tokens": count_tokens(context_text),
            "response": call.content,
            "prompt_tokens": call.prompt_tokens,
            "completion_tokens": call.completion_tokens,
            "cost_usd": call.cost_usd,
            "latency_seconds": call.latency_seconds,
        }
    return outputs


# --------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------- #
def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.clone_eval",
        description="Clone a real repository and benchmark SCE's context packing against it.",
    )
    parser.add_argument("--repo", required=True, help="Git URL (or local path) to clone.")
    parser.add_argument("--target", default=None, help="Fully qualified target symbol (default: auto-select).")
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help=f"SCE token budget (default: {DEFAULT_BUDGET}).")
    parser.add_argument("--k-hops", type=int, default=3, dest="k_hops", help="Ground-truth subgraph hop radius (default: 3).")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR), help=f"Clone cache directory (default: {DEFAULT_CACHE_DIR}).")
    parser.add_argument("--force-clone", action="store_true", help="Re-clone even if a cached copy already exists.")
    parser.add_argument("--live", action="store_true", help="Also send both contexts to OpenAI for an architectural explanation.")
    parser.add_argument("--model", default="gpt-4o-mini", help="OpenAI model for --live (default: gpt-4o-mini).")
    parser.add_argument("--report", default=None, help="Write full results as JSON to this path.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        repo_path = clone_repo(args.repo, cache_dir=Path(args.cache_dir), force=args.force_clone)
    except CloneError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Indexing {repo_path} ...", file=sys.stderr)
    index_start = time.perf_counter()
    try:
        builder, tag_matrix = build_pipeline(str(repo_path))
    except Exception as exc:  # noqa: BLE001 - this *is* the crash-survival assertion
        print(f"error: SCE's pipeline crashed while indexing '{repo_path}': {exc!r}", file=sys.stderr)
        return 1
    index_elapsed = time.perf_counter() - index_start

    num_symbols = len(builder.symbol_table)
    num_edges = builder.graph.number_of_edges()
    print(
        f"Indexed successfully in {index_elapsed:.2f}s: {num_symbols} symbols, {num_edges} CALLS edges "
        "(the cross-file linker did not crash on this real-world codebase)."
    )
    if num_symbols == 0:
        print("error: zero symbols were indexed - is this a Python/JS/TS/Go repository?", file=sys.stderr)
        return 1

    target = args.target
    if target is None:
        try:
            target = auto_select_target(builder, tag_matrix)
        except TargetSelectionError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"Auto-selected target: {target}")
    elif target not in builder.symbol_table:
        print(f"error: target symbol '{target}' was not found in {repo_path}", file=sys.stderr)
        print(f"hint: run `sce index {repo_path} --debug-json` to list known symbols.", file=sys.stderr)
        return 1

    try:
        result = run_single_benchmark_from_pipeline(
            builder, tag_matrix, str(repo_path), target, args.budget, k_hops=args.k_hops
        )
    except BenchmarkError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    from benchmarks.run_benchmark import render_detail_section

    print()
    print(render_detail_section(result))

    live_outputs = None
    if args.live:
        try:
            client = LLMClient()
        except OpenAIClientError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"\nRunning live architectural-explanation query with {args.model} ...", file=sys.stderr)
        try:
            live_outputs = run_live_architecture_query(builder, tag_matrix, target, args.budget, args.model, client)
        except (OpenAIClientError, CloneError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        for variant in ("raw", "sce"):
            out = live_outputs[variant]
            print(f"\n--- [{variant}] context={out['context_tokens']} tok, prompt={out['prompt_tokens']} tok, "
                  f"completion={out['completion_tokens']} tok, cost=${out['cost_usd']}, latency={out['latency_seconds']}s ---")
            print(out["response"])

    if args.report:
        import dataclasses
        import json

        payload = {"target": target, "benchmark": dataclasses.asdict(result), "live": live_outputs}
        output_path = Path(args.report)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2))
        print(f"\nWrote clone-eval report to {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
