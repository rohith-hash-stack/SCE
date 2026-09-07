"""SCE command-line interface: `sce index`, `sce query`."""
from __future__ import annotations

import json
import os

import click

from sce.graph.concrete_builder import ConcreteGraphBuilder
from sce.graph.metamodel import SemanticMetamodel
from sce.graph.symbol_table import GlobalSymbolTable
from sce.parser.tree_sitter_loader import EXTENSION_LANGUAGE_MAP
from sce.serializers.json_debug import render_json_debug
from sce.serializers.markdown import render_markdown
from sce.slicer.distance import DistanceConfig, DistanceEngine
from sce.slicer.knapsack import ContextKnapsackPacker
from sce.tagger.engine import TaggingEngine

IGNORED_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    ".mypy_cache", ".pytest_cache", ".tox", "site-packages", ".sce_cache",
}


def discover_files(repo_root: str) -> list[str]:
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")]
        for filename in filenames:
            if any(filename.endswith(ext) for ext in EXTENSION_LANGUAGE_MAP):
                files.append(os.path.join(dirpath, filename))
    return sorted(files)


def build_pipeline(repo_path: str) -> tuple[ConcreteGraphBuilder, dict[str, set[str]]]:
    """Run Stages 1-3 (parse, link, tag) end to end for one repository."""
    repo_root = os.path.abspath(repo_path)
    files = discover_files(repo_root)
    symbol_table = GlobalSymbolTable()
    builder = ConcreteGraphBuilder(repo_root, symbol_table)
    builder.pass1_collect_definitions(files)
    builder.pass2_resolve_calls(files)
    tag_matrix = TaggingEngine().tag_graph(builder)
    return builder, tag_matrix


@click.group()
@click.version_option(package_name="semantic-context-engine")
def main() -> None:
    """Semantic Context Engine: deterministic, offline repo context extraction."""


@main.command()
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False))
@click.option("--debug-json", is_flag=True, help="Emit the full dual-layer graph as JSON instead of a summary.")
def index(repo_path: str, debug_json: bool) -> None:
    """Parse REPO_PATH and summarize the dual-layer semantic graph."""
    builder, tag_matrix = build_pipeline(repo_path)

    if debug_json:
        click.echo(render_json_debug(builder, tag_matrix))
        return

    tag_counts: dict[str, int] = {}
    for tags in tag_matrix.values():
        for tag in tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    click.echo(f"Repository: {os.path.abspath(repo_path)}")
    click.echo(f"Symbols indexed: {len(builder.symbol_table)}")
    click.echo(f"CALLS edges: {builder.graph.number_of_edges()}")
    click.echo("Tag distribution:")
    if not tag_counts:
        click.echo("  (none)")
    for tag, count in sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        click.echo(f"  {tag}: {count}")


@main.command()
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False))
@click.argument("symbol")
@click.option("--budget", type=int, default=4000, show_default=True, help="Token budget for the packed context.")
@click.option(
    "--lambda-weight", "lambda_weight", type=float, default=0.7, show_default=True,
    help="Structural (G_C) vs. semantic (G_T) distance balance.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the packed context as JSON instead of Markdown.")
@click.option("-o", "--output", type=click.Path(dir_okay=False), help="Write the context package to a file instead of stdout.")
def query(repo_path: str, symbol: str, budget: int, lambda_weight: float, as_json: bool, output: str | None) -> None:
    """Extract a variable-resolution context package for SYMBOL (a fully
    qualified name, e.g. `src.controllers.checkout.process_checkout`)."""
    builder, tag_matrix = build_pipeline(repo_path)

    if symbol not in builder.symbol_table:
        click.echo(f"error: symbol '{symbol}' not found in {repo_path}", err=True)
        click.echo("hint: run `sce index REPO_PATH --debug-json` to list known symbols.", err=True)
        raise SystemExit(1)

    metamodel = SemanticMetamodel()
    distance_engine = DistanceEngine(metamodel, tag_matrix, DistanceConfig(lambda_weight=lambda_weight))
    packer = ContextKnapsackPacker(token_budget=budget)
    result = packer.pack(symbol, builder, tag_matrix, distance_engine)

    if as_json:
        payload = {
            "seed": result.seed,
            "budget": result.budget,
            "allocated_tokens": result.allocated_tokens,
            "preserved_semantics": result.preserved_semantics,
            "items": [
                {"symbol": i.symbol, "resolution": i.resolution, "language": i.language_id, "content": i.content}
                for i in result.items
            ],
        }
        text = json.dumps(payload, indent=2)
    else:
        text = render_markdown(result, tag_matrix)

    if output:
        with open(output, "w") as f:
            f.write(text)
        click.echo(f"Wrote context package to {output}")
    else:
        click.echo(text)


if __name__ == "__main__":
    main()
