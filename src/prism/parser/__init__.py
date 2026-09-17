"""Prism AST parser interface with error recovery."""
import ast
from dataclasses import dataclass, field


@dataclass
class ParseResult:
    symbols: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def parse_source(source_text: str, filename: str = "<unknown>") -> ParseResult:
    """Parse code safely, returning diagnostic errors on syntax failures."""
    if not source_text.strip():
        return ParseResult(symbols=[], errors=[])

    try:
        tree = ast.parse(source_text, filename=filename)
        extracted: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                extracted.append(node.name)
        return ParseResult(symbols=extracted, errors=[])
    except SyntaxError as e:
        return ParseResult(
            symbols=[],
            errors=[f"SyntaxError on line {e.lineno}: {e.msg}"],
        )
