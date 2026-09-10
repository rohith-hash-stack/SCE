"""Roadmap Step 2: a universal, language-agnostic slicer operating directly
on Tree-sitter Concrete Syntax Trees via byte-range statement pruning.

`prism.slicer.compressor`'s L1 skeletonizer is a real `ast.NodeTransformer` -
exact, but Python-only, since there is no equivalent stdlib AST for
TypeScript/JavaScript/Go. `UniversalSlicer` gets the same *effect* (retain
control flow, error handling, and call shape; drop noise) from the CST every
supported language already has, by working at the byte level rather than
rebuilding a language-specific AST:

  1. Walk the function/method's body with Tree-sitter's own node/field
     navigation (no AST reconstruction).
  2. Classify each direct statement in every nested block as retain/prune.
  3. Collect `(start_byte, end_byte)` for every pruned run of statements.
  4. Splice the original source bytes from the last edit to the first (so
     earlier byte offsets never shift out from under a later edit).

This module does not touch the existing `prism.slicer.compressor` path or
`ContextKnapsackPacker` - it is a standalone component, validated in
`tests/test_universal_slicer.py` against real TypeScript/Go snippets and
for parity with the legacy Python AST compressor on the same input.
"""
from __future__ import annotations

from tree_sitter import Node

from prism.parser.lang_config import CALL_NODE_TYPE, CLASS_NODE_TYPES, FUNCTION_NODE_TYPES, call_callee_segments
from prism.parser.tree_sitter_loader import LanguageID, node_text
from prism.slicer.compressor import ControlFlowSkeletonizer

# --------------------------------------------------------------------- #
# Per-language structural node-type tables
# --------------------------------------------------------------------- #
# The actual statement-list container to prune *within*. Go is the odd one
# out: a `block` node (`{ ... }`) wraps a separate `statement_list` child
# holding the real statements - `block` itself never directly contains
# them, unlike Python's `block` or JS/TS's `statement_block`.
_BLOCK_NODE_TYPES: dict[str, frozenset[str]] = {
    LanguageID.PYTHON: frozenset({"block"}),
    LanguageID.JAVASCRIPT: frozenset({"statement_block"}),
    LanguageID.TYPESCRIPT: frozenset({"statement_block"}),
    LanguageID.TSX: frozenset({"statement_block"}),
    LanguageID.GO: frozenset({"statement_list"}),
    # Java's own body node types both hold their statements directly, no
    # wrapper - "constructor_body" only differs from "block" by name (a
    # constructor can't declare a return type, so the grammar gives it a
    # distinct node type), not by shape.
    LanguageID.JAVA: frozenset({"block", "constructor_body"}),
    LanguageID.CSHARP: frozenset({"block"}),
}

# Item 11 (second post-implementation audit): comment/doc-block node
# types, stripped at L1 (`_classify` below) the same way
# `compress_python`'s L1 already strips Python docstrings - confirmed
# directly that every supported grammar but Java's names its comment
# node(s) `"comment"` uniformly; Java's grammar splits line vs. block
# comments into two distinct node types instead.
_COMMENT_NODE_TYPES: dict[str, frozenset[str]] = {
    LanguageID.PYTHON: frozenset({"comment"}),
    LanguageID.JAVASCRIPT: frozenset({"comment"}),
    LanguageID.TYPESCRIPT: frozenset({"comment"}),
    LanguageID.TSX: frozenset({"comment"}),
    LanguageID.GO: frozenset({"comment"}),
    LanguageID.JAVA: frozenset({"line_comment", "block_comment"}),
    LanguageID.CSHARP: frozenset({"comment"}),
}

# Nodes that are not themselves a statement-list, but may contain one
# (possibly nested inside further compound/clause children of their own) -
# recursed through to find every nested block, without being pruned as a
# unit themselves. Includes each language's own control-flow headers
# (if/for/while/switch/try) and their clause children (elif/else/except/
# finally/catch/case), plus Go's `block` (a pure pass-through wrapper, per
# the note above).
_COMPOUND_NODE_TYPES: dict[str, frozenset[str]] = {
    LanguageID.PYTHON: frozenset({
        "if_statement", "elif_clause", "else_clause", "for_statement",
        "while_statement", "try_statement", "except_clause", "except_group_clause",
        "finally_clause", "with_statement", "match_statement", "case_clause",
    }),
    LanguageID.JAVASCRIPT: frozenset({
        "if_statement", "else_clause", "for_statement", "for_in_statement",
        "while_statement", "do_statement", "try_statement", "catch_clause",
        "finally_clause", "switch_statement", "switch_body", "switch_case", "switch_default",
    }),
    LanguageID.TYPESCRIPT: frozenset({
        "if_statement", "else_clause", "for_statement", "for_in_statement",
        "while_statement", "do_statement", "try_statement", "catch_clause",
        "finally_clause", "switch_statement", "switch_body", "switch_case", "switch_default",
    }),
    LanguageID.TSX: frozenset({
        "if_statement", "else_clause", "for_statement", "for_in_statement",
        "while_statement", "do_statement", "try_statement", "catch_clause",
        "finally_clause", "switch_statement", "switch_body", "switch_case", "switch_default",
    }),
    LanguageID.GO: frozenset({
        "block", "if_statement", "for_statement", "expression_switch_statement",
        "type_switch_statement", "select_statement", "expression_case",
        "default_case", "type_case", "communication_case",
    }),
    # Java has no separate elif/else wrapper node at all - `else if` is
    # just another `if_statement` directly nested under the `else` keyword
    # (confirmed via grammar exploration), so "if_statement" alone covers
    # every arm. `switch_expression` is also Java's statement-level switch
    # (the grammar reuses one node type for both roles); its
    # `switch_block_statement_group` case bodies hold their statements
    # directly with no per-case block wrapper - matching JS/TS's own
    # switch_case/switch_default shape - so, like those, case bodies are
    # traversed through but never individually pruned (the group is listed
    # here, not in _BLOCK_NODE_TYPES).
    LanguageID.JAVA: frozenset({
        "if_statement", "for_statement", "enhanced_for_statement",
        "while_statement", "do_statement", "try_statement", "catch_clause",
        "finally_clause", "switch_expression", "switch_block", "switch_block_statement_group",
    }),
    LanguageID.CSHARP: frozenset({
        "if_statement", "for_statement", "foreach_statement",
        "while_statement", "do_statement", "try_statement", "catch_clause",
        "finally_clause", "switch_statement", "switch_body", "switch_section",
    }),
}

# Always retained, never pruned, and never recursed into further - a
# statement is "structurally essential" the moment it signals control
# transfer or error propagation, regardless of what it contains.
_ALWAYS_RETAIN_TYPES: dict[str, frozenset[str]] = {
    LanguageID.PYTHON: frozenset({"return_statement", "raise_statement", "yield_statement", "break_statement", "continue_statement"}),
    LanguageID.JAVASCRIPT: frozenset({"return_statement", "throw_statement", "break_statement", "continue_statement"}),
    LanguageID.TYPESCRIPT: frozenset({"return_statement", "throw_statement", "break_statement", "continue_statement"}),
    LanguageID.TSX: frozenset({"return_statement", "throw_statement", "break_statement", "continue_statement"}),
    # Go has no raise/throw - errors propagate via `return err`/`return nil, err`,
    # already covered by return_statement. `defer_statement` is Go's own
    # cleanup-on-exit idiom (structurally analogous to try/finally).
    LanguageID.GO: frozenset({"return_statement", "defer_statement", "break_statement", "continue_statement"}),
    # `yield_statement` is Java's switch-expression `yield <value>;` arm
    # (structurally a control-transfer statement, same category as return).
    LanguageID.JAVA: frozenset({"return_statement", "throw_statement", "break_statement", "continue_statement", "yield_statement"}),
    LanguageID.CSHARP: frozenset({"return_statement", "throw_statement", "break_statement", "continue_statement"}),
}

# Statement shapes that assign/declare a variable - retained only when their
# right-hand side is directly a call (a constructor/call-sink assignment,
# e.g. `result = compute(a, b)`); pure intermediate math/formatting with no
# call is prunable noise. `expression_statement` is handled separately
# below since it does double duty as every language's wrapper for a bare
# call statement, and - for Python's `assignment` and JS/TS's
# `assignment_expression` alike - for a plain (re)assignment too.
_DECLARATION_NODE_TYPES: dict[str, frozenset[str]] = {
    LanguageID.PYTHON: frozenset(),  # assignment is always expression_statement-wrapped
    LanguageID.JAVASCRIPT: frozenset({"lexical_declaration", "variable_declaration"}),
    LanguageID.TYPESCRIPT: frozenset({"lexical_declaration", "variable_declaration"}),
    LanguageID.TSX: frozenset({"lexical_declaration", "variable_declaration"}),
    LanguageID.GO: frozenset({"short_var_declaration", "assignment_statement"}),
    LanguageID.JAVA: frozenset({"local_variable_declaration"}),
    LanguageID.CSHARP: frozenset({"local_declaration_statement"}),
}

# Placeholder inserted when *every* statement in a block gets pruned - a
# block, unlike an individually-removed statement, can never be left
# byte-empty without breaking the grammar (Python needs a body; a bare `{}`
# is valid in the curly-brace languages, but `/* ... */` says plainly that
# something was removed rather than looking like a genuinely empty body).
_EMPTY_BLOCK_PLACEHOLDER: dict[str, bytes] = {
    LanguageID.PYTHON: b"pass",
    LanguageID.JAVASCRIPT: b"/* ... */",
    LanguageID.TYPESCRIPT: b"/* ... */",
    LanguageID.TSX: b"/* ... */",
    LanguageID.GO: b"/* ... */",
    LanguageID.JAVA: b"/* ... */",
    LanguageID.CSHARP: b"/* ... */",
}

# A collapsed call's replacement argument list. Python's bare `...` is a
# real, valid standalone expression (the `Ellipsis` literal), so `(...)`
# The exception-raising statement type per language - Go has none of its
# own (see _ALWAYS_RETAIN_TYPES above), so it's simply absent here.
_RAISE_LIKE_TYPES: dict[str, str] = {
    LanguageID.PYTHON: "raise_statement",
    LanguageID.JAVASCRIPT: "throw_statement",
    LanguageID.TYPESCRIPT: "throw_statement",
    LanguageID.TSX: "throw_statement",
    LanguageID.JAVA: "throw_statement",
    LanguageID.CSHARP: "throw_statement",
}

# Reuse the exact same "is this call noise, not signal" heuristic the
# legacy AST-based L1 compressor already uses, so the two skeletonizers
# don't quietly diverge on what counts as loggy/prunable.
_NOISY_CALL_TOKENS = ControlFlowSkeletonizer.LOG_TOKENS


def _unwrap_await(node: Node | None) -> Node | None:
    """`await someCall()` wraps the call in its own `await_expression` node
    (JS/TS) - unwrap it so "is this value directly a call" checks see
    through it, the same way they already see a bare call. Python's
    `await` uses a different grammar shape (no wrapping node at all - the
    call is already the direct child), so this is a no-op there.
    """
    if node is not None and node.type == "await_expression" and node.named_children:
        return node.named_children[0]
    return node


def _as_call_node(node: Node | None, language_id: str) -> Node | None:
    """`node` itself, unwrapped through `await` if present, when it is
    directly a call - otherwise None. Use this (not a bare type check)
    everywhere a "value is a call" decision also needs the actual call
    node back, e.g. `await someCall()` must resolve to `someCall()`
    itself, not the outer `await_expression`, so arg-collapsing and the
    noisy-call check (both of which need the call's own "function"/
    "arguments" fields) work correctly.
    """
    node = _unwrap_await(node)
    if node is not None and node.type == CALL_NODE_TYPE.get(language_id):
        return node
    return None


def _is_noisy_call(call_node: Node, language_id: str, source: bytes) -> bool:
    # `call_callee_segments` (not a bare "function"-field lookup) is
    # required here since Java's `method_invocation` has no unified
    # "function" field at all - it splits an optional receiver and the
    # method name into separate "object"/"name" fields instead (see
    # `lang_config.call_callee_segments`'s own docstring); every other
    # supported language's call node does follow the "function"-field
    # convention, so this generalizes cleanly rather than special-casing.
    segments = call_callee_segments(call_node, source, language_id)
    name = ".".join(segments).lower() if segments else node_text(call_node, source).lower()
    return any(token in name for token in _NOISY_CALL_TOKENS)


def _find_call_in_simple_statement(node: Node, language_id: str) -> Node | None:
    """The call node driving this statement's retain/prune decision, if
    any - a bare call statement, or a declaration/assignment whose value is
    directly a call. Returns None for pure intermediate math/formatting
    with no call anywhere at the statement's own top level.
    """
    call_type = CALL_NODE_TYPE.get(language_id)

    if node.type == "expression_statement":
        # A top-level statement is always wrapped in `expression_statement`
        # in every one of these grammars, including a plain assignment
        # (Python's `assignment`, JS/TS's `assignment_expression`) - not
        # just a bare call.
        for child in node.named_children:
            if child.type == call_type:
                return child
            if child.type in ("assignment", "assignment_expression", "augmented_assignment_expression"):
                call_node = _as_call_node(child.child_by_field_name("right"), language_id)
                if call_node is not None:
                    return call_node
        return None

    if node.type in ("short_var_declaration", "assignment_statement"):  # Go
        right = node.child_by_field_name("right")
        if right is None:
            return None
        candidates = right.named_children if right.type == "expression_list" else [right]
        for item in candidates:
            call_node = _as_call_node(item, language_id)
            if call_node is not None:
                return call_node
        return None

    if node.type in ("lexical_declaration", "variable_declaration"):  # JS/TS
        for declarator in node.named_children:
            if declarator.type != "variable_declarator":
                continue
            call_node = _as_call_node(declarator.child_by_field_name("value"), language_id)
            if call_node is not None:
                return call_node
        return None

    if node.type == "local_variable_declaration":  # Java
        for declarator in node.named_children:
            if declarator.type != "variable_declarator":
                continue
            call_node = _as_call_node(declarator.child_by_field_name("value"), language_id)
            if call_node is not None:
                return call_node
        return None

    if node.type == "local_declaration_statement":  # C#
        var_decl = next((c for c in node.named_children if c.type == "variable_declaration"), None)
        if var_decl is None:
            return None
        for declarator in var_decl.named_children:
            if declarator.type != "variable_declarator":
                continue
            call_node = _as_call_node(_csharp_declarator_value(declarator), language_id)
            if call_node is not None:
                return call_node
        return None

    return None


def _csharp_declarator_value(declarator: Node) -> Node | None:
    """A C# `variable_declarator`'s initializer expression. Unlike Java,
    C#'s grammar assigns it no field name at all (confirmed empirically -
    `child_by_field_name("value")` returns None even though the child is
    present as the third positional child, after the name identifier and
    the "=" token), so this falls back to the second *named* child.
    """
    named = [c for c in declarator.children if c.is_named]
    return named[1] if len(named) > 1 else None


def _classify(node: Node, language_id: str, source: bytes) -> str:
    """Returns "retain" or "prune" for one direct statement of a block."""
    if node.type in _COMMENT_NODE_TYPES.get(language_id, frozenset()):
        # Item 11: stripped at L1 the same way compress_python's L1
        # already strips Python docstrings - a comment carries no
        # structural/behavioral information this tier is meant to
        # preserve.
        return "prune"
    if node.type in _ALWAYS_RETAIN_TYPES.get(language_id, frozenset()):
        return "retain"
    if node.type in _COMPOUND_NODE_TYPES.get(language_id, frozenset()):
        # The compound statement's own header (condition/iterator) is
        # always retained; what happens *inside* it is decided separately
        # by recursing into its nested blocks - see `_collect_edits`.
        return "retain"
    if node.type == "expression_statement" or node.type in _DECLARATION_NODE_TYPES.get(language_id, frozenset()):
        call_node = _find_call_in_simple_statement(node, language_id)
        if call_node is None:
            # A plain assignment/declaration/expression with no call at
            # all - pure intermediate math, string formatting, or similar.
            return "prune"
        return "prune" if _is_noisy_call(call_node, language_id, source) else "retain"
    # Anything not positively identified as prunable noise (imports inside
    # a function, `del`/`global`, labeled statements, ...) is kept by
    # default - conservative on purpose, matching the legacy AST
    # compressor's own default (an unhandled node type passes through
    # `NodeTransformer.generic_visit` unchanged).
    return "retain"


def _collect_edits(node: Node, language_id: str, source: bytes, edits: list[tuple[int, int, bytes]]) -> None:
    """Descends from `node` looking for block-typed children to prune
    within, without crossing into a nested function/class definition (that
    is a separate symbol with its own line_range, rendered independently -
    not something a parent's skeletonization pass should rewrite).

    Item 11 (second post-implementation audit): real call arguments are
    always retained here now (`doit(1, 2, 3)` stays `doit(1, 2, 3)`, not
    `doit(...)`) - matching `compress_python`'s L1
    (`ArgPreservingSkeletonizer`, Issue #10) parity. L2 (`extract_contract`)
    already renders its own arg-free placeholder body
    (`{ /* contract */ }`) independently of this L1-only pass.
    """
    function_types = FUNCTION_NODE_TYPES.get(language_id, frozenset())
    class_types = CLASS_NODE_TYPES.get(language_id, frozenset())
    for child in node.named_children:
        if child.type in function_types or child.type in class_types:
            continue
        if child.type in _COMMENT_NODE_TYPES.get(language_id, frozenset()):
            # Item 11: a comment can appear as a *sibling* of the real
            # statement container, not only as one of its own named
            # children - confirmed directly for Go, whose `block` wrapper
            # (this function's own def_node "body" field) parses a
            # leading comment as `{ comment statement_list }`, a direct
            # child of `block` itself, structurally outside the nested
            # `statement_list` node `_process_block` below actually
            # iterates. `_classify`'s own comment-pruning inside
            # `_process_block` never reaches a comment at this level, so
            # it needs its own prune edit here too.
            edits.append((child.start_byte, child.end_byte, b""))
        elif child.type in _BLOCK_NODE_TYPES.get(language_id, frozenset()):
            _process_block(child, language_id, source, edits)
        elif child.type in _COMPOUND_NODE_TYPES.get(language_id, frozenset()):
            _collect_edits(child, language_id, source, edits)


def _process_block(block_node: Node, language_id: str, source: bytes, edits: list[tuple[int, int, bytes]]) -> None:
    statements = list(block_node.named_children)
    if not statements:
        return
    decisions = [_classify(stmt, language_id, source) for stmt in statements]

    for stmt, decision in zip(statements, decisions):
        if decision != "retain":
            continue
        if stmt.type in _COMPOUND_NODE_TYPES.get(language_id, frozenset()):
            # Recurse into every retained compound statement to prune noise
            # nested inside its own clauses/blocks (an `if` being "retained"
            # only pins its own condition line - what's inside still needs
            # its own decision).
            _collect_edits(stmt, language_id, source, edits)

    runs: list[tuple[int, int]] = []
    i, n = 0, len(statements)
    while i < n:
        if decisions[i] == "prune":
            j = i
            while j + 1 < n and decisions[j + 1] == "prune":
                j += 1
            runs.append((i, j))
            i = j + 1
        else:
            i += 1

    all_pruned = len(runs) == 1 and runs[0] == (0, n - 1)
    for start_idx, end_idx in runs:
        start_byte = statements[start_idx].start_byte
        end_byte = statements[end_idx].end_byte
        replacement = _EMPTY_BLOCK_PLACEHOLDER[language_id] if all_pruned else b""
        edits.append((start_byte, end_byte, replacement))


def _collect_body_edits(body: Node, language_id: str, source: bytes, edits: list[tuple[int, int, bytes]]) -> None:
    if body.type in _BLOCK_NODE_TYPES.get(language_id, frozenset()):
        _process_block(body, language_id, source, edits)
    else:
        # Go's function body field is itself typed "block", a pass-through
        # wrapper around the real "statement_list" - descend once more.
        _collect_edits(body, language_id, source, edits)


def _walk_all(node: Node):
    yield node
    for child in node.children:
        yield from _walk_all(child)


class UniversalSlicer:
    """Language-agnostic L1/L2 slicing via Tree-sitter byte-range statement
    pruning - see this module's docstring for the overall strategy.
    """

    SUPPORTED_LANGUAGES = frozenset(_BLOCK_NODE_TYPES)

    def skeletonize(self, source: bytes, def_node: Node, language_id: str) -> str:
        """L1: retains control-flow branch conditions, error returns/raises/
        throws, and call-shaped statements; prunes plain intermediate
        assignments and noisy logging calls. Returns standalone,
        independently parseable source text for just `def_node`.
        """
        if language_id not in self.SUPPORTED_LANGUAGES:
            raise ValueError(f"UniversalSlicer does not support language id: {language_id!r}")

        raw = bytearray(source[def_node.start_byte : def_node.end_byte])
        offset = def_node.start_byte
        body = def_node.child_by_field_name("body")
        edits: list[tuple[int, int, bytes]] = []
        if body is not None:
            _collect_body_edits(body, language_id, source, edits)

        for start, end, replacement in sorted(edits, key=lambda e: e[0], reverse=True):
            raw[start - offset : end - offset] = replacement
        return raw.decode("utf-8", errors="replace")

    def extract_contract(
        self,
        source: bytes,
        def_node: Node,
        language_id: str,
        tags: set[str] | frozenset[str] = frozenset(),
        callees: list[str] | tuple[str, ...] = (),
    ) -> str:
        """L2: signature only, body replaced with a placeholder, plus a
        contract metadata block (tags, raised/thrown exception names,
        known downstream callees) - mirrors
        `prism.slicer.compressor._render_contract_block`'s format.
        """
        if language_id not in self.SUPPORTED_LANGUAGES:
            raise ValueError(f"UniversalSlicer does not support language id: {language_id!r}")

        body = def_node.child_by_field_name("body")
        signature_line = self.signature_line(source, def_node, language_id)
        raises = self._extract_raised_names(body, language_id, source) if body is not None else []
        lines = [signature_line, _render_contract_metadata(language_id, tags, raises, callees)]
        return "\n".join(line for line in lines if line)

    def signature_line(self, source: bytes, def_node: Node, language_id: str) -> str:
        """Just the signature (no contract metadata block) - a def_node's
        header text up to its body, verbatim including whatever precedes
        the declaration keyword itself on its own source line(s) (Java
        annotations, C# attributes: `@Override\\npublic String toString()`
        is one header, not one line). Callers that need "the first line"
        specifically (none currently do - `.splitlines()[0]` on this
        method's own output silently truncated a multi-line annotated
        header down to just `@Override`, confirmed via a live
        spring-petclinic benchmark run producing an ERROR-node L3 slice)
        must not assume this is single-line.
        """
        if language_id not in self.SUPPORTED_LANGUAGES:
            raise ValueError(f"UniversalSlicer does not support language id: {language_id!r}")

        body = def_node.child_by_field_name("body")
        if body is None:
            return node_text(def_node, source).rstrip()
        header = source[def_node.start_byte : body.start_byte].decode("utf-8", errors="replace").rstrip()
        if language_id == LanguageID.PYTHON:
            # `header` already ends in ":" (the block's opening colon).
            return f"{header} ..."
        return f"{header} {{ /* contract */ }}"

    @staticmethod
    def _extract_raised_names(body: Node, language_id: str, source: bytes) -> list[str]:
        raise_type = _RAISE_LIKE_TYPES.get(language_id)
        if raise_type is None:
            return []
        names: list[str] = []
        call_type = CALL_NODE_TYPE.get(language_id)
        for node in _walk_all(body):
            if node.type != raise_type:
                continue
            value = node.named_children[0] if node.named_children else None
            if value is None:
                continue
            if value.type == call_type:
                # Python: `raise ValueError("bad")` - a bare call.
                segments = call_callee_segments(value, source, language_id)
                name = segments[-1] if segments else node_text(value, source)
            elif value.type == "new_expression":
                # JS/TS: `throw new ValidationError("bad")` - just the
                # constructor name, not the full `new X(...)` text.
                ctor = value.child_by_field_name("constructor")
                name = node_text(ctor if ctor is not None else value, source)
            elif value.type == "object_creation_expression":
                # Java/C#: `throw new PermissionDeniedException("bad");` -
                # the exception type lives in the "type" field, not a
                # "constructor" field (a differently-named grammar
                # convention from JS/TS's `new_expression` above).
                type_node = value.child_by_field_name("type")
                name = node_text(type_node if type_node is not None else value, source)
            else:
                name = node_text(value, source)
            if name and name not in names:
                names.append(name)
        return names


_COMMENT_PREFIX: dict[str, str] = {
    LanguageID.PYTHON: "#",
    LanguageID.JAVASCRIPT: "//",
    LanguageID.TYPESCRIPT: "//",
    LanguageID.TSX: "//",
    LanguageID.GO: "//",
    LanguageID.JAVA: "//",
    LanguageID.CSHARP: "//",
}


def _render_contract_metadata(
    language_id: str, tags: set[str] | frozenset[str], raises: list[str], callees: list[str] | tuple[str, ...]
) -> str:
    # `#` is only a comment marker in Python - in JS/TS it's the private-
    # class-field sigil, and Go has no `#` token at all, so a Python-style
    # metadata block would make the L2 contract fail to reparse as the
    # target language (confirmed: it reparsed with ERROR nodes in
    # TypeScript). Use each language's own line-comment marker instead.
    prefix = _COMMENT_PREFIX.get(language_id, "#")
    lines = [f"{prefix} Tags: [{', '.join(sorted(tags))}]"]
    if raises:
        lines.append(f"{prefix} Raises: {', '.join(raises)}")
    if callees:
        lines.append(f"{prefix} Calls: {', '.join(callees)}")
    return "\n".join(lines)
