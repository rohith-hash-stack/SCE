"""The block a definition is nested in (Phase 3, the delete-05 fix; `harness/experiments/r4/PHASE2_RESULTS.md`).

A function defined inside a conditional block is only part of the code a change touches:

    if sys.version_info >= (3, 9):
        GenericAlias = types.GenericAlias
    else:

        def GenericAlias(cls):
            return cls

Shown as its own two lines (`def GenericAlias` ... `return cls`), a deletion leaves `else:` with no statements: the
file stops parsing. `enclosing_block` finds the innermost compound statement (`if`/`elif`/`else`, `try`/`except`/
`else`/`finally`, `with`, `for`, `while`, `match`) whose branch holds the definition directly, and says which branch
and whether the definition is the only statement in it. `prism_read_code` and `prism_change_context` show that block.
"""
from __future__ import annotations

import ast

#: A block longer than this is described, not shown in full.
MAX_BLOCK_LINES = 40


def _branches(node):
    """(name, statements) for each branch of a compound statement."""
    if isinstance(node, ast.If):
        out = [("if", node.body)]
        orelse = node.orelse
        if len(orelse) == 1 and isinstance(orelse[0], ast.If) and orelse[0].col_offset == node.col_offset:
            out.append(("elif", orelse))                            # `elif`: a nested If in the else slot
        elif orelse:
            out.append(("else", orelse))
        return out
    if isinstance(node, (ast.Try, getattr(ast, "TryStar", ast.Try))):
        return [("try", node.body)] + [("except", h.body) for h in node.handlers] + \
            ([("else", node.orelse)] if node.orelse else []) + ([("finally", node.finalbody)] if node.finalbody else [])
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return [("with", node.body)]
    if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
        return [("loop", node.body)] + ([("else", node.orelse)] if node.orelse else [])
    if hasattr(ast, "Match") and isinstance(node, ast.Match):
        return [("case", c.body) for c in node.cases]
    return []


_KIND = {ast.If: "if/else", ast.Try: "try", ast.With: "with", ast.AsyncWith: "with", ast.For: "for", ast.AsyncFor: "for",
         ast.While: "while"}


def enclosing_block(source: str, def_line: int) -> dict | None:
    """The innermost compound statement with a branch whose direct statements include the definition starting at
    `def_line` (its `def`/`class` line or its first decorator's): `{lo, hi, kind, branch, only_statement}`, or None."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return None
    best = None
    for node in ast.walk(tree):
        for branch, stmts in _branches(node):
            for s in stmts:
                if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                first = min([s.lineno] + [d.lineno for d in s.decorator_list])
                if first <= def_line <= s.lineno:
                    span = (node.lineno, node.end_lineno or node.lineno)
                    if best is None or span[1] - span[0] < best["hi"] - best["lo"]:
                        best = {"lo": span[0], "hi": span[1], "kind": _KIND.get(type(node), type(node).__name__.lower()),
                                "branch": branch, "only_statement": len(stmts) == 1}
    return best


def describe(block: dict, name: str) -> str:
    """One factual sentence: where the definition sits, and whether it is its branch's only statement."""
    where = {"if": "`if` branch", "elif": "`elif` branch", "else": "`else:` branch", "try": "`try:` body",
             "except": "`except` branch", "finally": "`finally:` branch", "with": "`with` body", "loop": "loop body",
             "case": "`case` branch"}.get(block["branch"], f"`{block['branch']}` branch")
    text = f"`{name}` is defined inside the {where} of the {block['kind']} block at lines {block['lo']}-{block['hi']}"
    if block["only_statement"]:
        text += f"; it is the only statement in that branch"
    return text + "."
