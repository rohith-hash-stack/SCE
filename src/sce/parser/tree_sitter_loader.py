"""Tree-sitter grammar loading and per-language parser construction.

Supported extensions map to tree-sitter grammar binaries. Loading is lazy
and memoized so repeated `sce query` invocations only pay the grammar
construction cost once per process.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from tree_sitter import Language, Node, Parser


class LanguageID:
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    TSX = "tsx"
    GO = "go"
    JAVA = "java"
    CSHARP = "csharp"


EXTENSION_LANGUAGE_MAP: dict[str, str] = {
    ".py": LanguageID.PYTHON,
    ".js": LanguageID.JAVASCRIPT,
    ".jsx": LanguageID.JAVASCRIPT,
    ".mjs": LanguageID.JAVASCRIPT,
    ".ts": LanguageID.TYPESCRIPT,
    ".tsx": LanguageID.TSX,
    ".go": LanguageID.GO,
    ".java": LanguageID.JAVA,
    ".cs": LanguageID.CSHARP,
}

# Languages for which the concrete-graph linker and AST compressor have full,
# semantically precise support (native `ast` module reuse). Other languages
# still get Stage 1 definition collection and best-effort Stage 2/4 handling.
FULLY_SUPPORTED_LANGUAGES = frozenset({LanguageID.PYTHON})


@lru_cache(maxsize=None)
def _load_language(language_id: str) -> Language:
    if language_id == LanguageID.PYTHON:
        import tree_sitter_python as ts_mod

        return Language(ts_mod.language())
    if language_id == LanguageID.JAVASCRIPT:
        import tree_sitter_javascript as ts_mod

        return Language(ts_mod.language())
    if language_id == LanguageID.TYPESCRIPT:
        import tree_sitter_typescript as ts_mod

        return Language(ts_mod.language_typescript())
    if language_id == LanguageID.TSX:
        import tree_sitter_typescript as ts_mod

        return Language(ts_mod.language_tsx())
    if language_id == LanguageID.GO:
        import tree_sitter_go as ts_mod

        return Language(ts_mod.language())
    if language_id == LanguageID.JAVA:
        import tree_sitter_java as ts_mod

        return Language(ts_mod.language())
    if language_id == LanguageID.CSHARP:
        import tree_sitter_c_sharp as ts_mod

        return Language(ts_mod.language())
    raise ValueError(f"Unsupported language id: {language_id}")


@lru_cache(maxsize=None)
def get_parser(language_id: str) -> Parser:
    return Parser(_load_language(language_id))


def language_for_path(path: str) -> str | None:
    for ext, language_id in EXTENSION_LANGUAGE_MAP.items():
        if path.endswith(ext):
            return language_id
    return None


@dataclass(frozen=True)
class ParsedFile:
    path: str
    language_id: str
    source: bytes
    root_node: Node


def parse_source(path: str, source: bytes) -> ParsedFile | None:
    """Parse `source` (bytes) taken from `path` using the matching grammar.

    Returns None when the file extension has no registered grammar.
    """
    language_id = language_for_path(path)
    if language_id is None:
        return None
    parser = get_parser(language_id)
    tree = parser.parse(source)
    return ParsedFile(path=path, language_id=language_id, source=source, root_node=tree.root_node)


def parse_file(path: str) -> ParsedFile | None:
    language_id = language_for_path(path)
    if language_id is None:
        return None
    with open(path, "rb") as f:
        source = f.read()
    return parse_source(path, source)


def node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
