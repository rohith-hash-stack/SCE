"""JS/TS test-runner blocks (Playwright, Jest, Vitest, Mocha) as symbols.

A test written as `test('title', async ({ loginPage }) => { ... })` is an
anonymous callback: Pass 1 registers named functions/classes/methods only,
so before this module a whole spec file contributed no symbol and a
page-object method had no test callers at all.

`find_test_blocks` returns one `TestBlock` per test, hook and fixture
callback, with a deterministic qualified name:

    test('valid user sees greeting', ...)       -> <module>.valid_user_sees_greeting
    test.describe('login', () => { test(...) }) -> <module>.login.<test>
    test.beforeEach(...) inside that describe   -> <module>.login.beforeEach
    base.extend({ loginPage: async (...) => {}}) -> <module>.fixture_loginPage

`describe` groups only prefix names; they are not symbols. A `test.step`
callback is not a separate block: arrow functions are not a scope boundary
for `iter_scoped_nodes`, so a step's calls already belong to its test.

`find_fixture_types` reads the fixture-name -> class-name map a
`base.extend<T>({...})` call declares, from the type argument (`T` inline
or a same-file `type`/`interface`) and from `use(new C(...))` in a fixture
body. Class names are returned unresolved; `ConcreteGraphBuilder` resolves
them through the defining file's import map.

Pure functions of one parsed file: Pass 1 and the index-cache rehydration
call the same `find_test_blocks`, so both produce identical names.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from tree_sitter import Node

from prism.parser.tree_sitter_loader import node_text

TEST_FUNCTIONS = frozenset({"test", "it", "specify"})
GROUP_FUNCTIONS = frozenset({"describe", "context", "suite"})
HOOK_FUNCTIONS = frozenset({"beforeEach", "afterEach", "beforeAll", "afterAll", "before", "after"})
# `test.extend`/`test.use`/`test.step`/`test.info`... are not test blocks.
_NON_BLOCK_MEMBERS = frozenset({"extend", "use", "step", "info", "expect", "setTimeout", "slow"})
_CALLBACK_TYPES = frozenset({"arrow_function", "function_expression", "function"})
_STRING_TYPES = frozenset({"string", "template_string"})
_SLUG = re.compile(r"\W+", re.UNICODE)


@dataclass(frozen=True)
class TestBlock:
    qualified_name: str
    kind: str                      # "test" | "hook" | "fixture"
    callback: Node                 # the arrow/function expression (the def node)
    outer: Node                    # the node whose lines are the symbol's line range
    fixture_params: tuple[str, ...] = field(default=())


def _slug(text: str) -> str:
    return _SLUG.sub("_", text).strip("_")


def _callee_segments(call: Node, source: bytes) -> list[str] | None:
    """`test` -> ["test"], `test.describe.serial` -> ["test", "describe",
    "serial"]; None for any other callee shape."""
    fn = call.child_by_field_name("function")
    segments: list[str] = []
    while fn is not None and fn.type == "member_expression":
        prop = fn.child_by_field_name("property")
        if prop is None:
            return None
        segments.insert(0, node_text(prop, source))
        fn = fn.child_by_field_name("object")
    if fn is None or fn.type != "identifier":
        return None
    segments.insert(0, node_text(fn, source))
    return segments


def _classify(segments: list[str]) -> tuple[str, str] | None:
    """(`group` | `hook` | `test`, hook name) for a test-runner callee."""
    root = segments[0]
    rest = segments[1:]
    if root not in TEST_FUNCTIONS | GROUP_FUNCTIONS | HOOK_FUNCTIONS:
        return None
    if any(s in _NON_BLOCK_MEMBERS for s in rest):
        return None
    if root in GROUP_FUNCTIONS or any(s in GROUP_FUNCTIONS for s in rest):
        return "group", ""
    hook = next((s for s in [root, *rest] if s in HOOK_FUNCTIONS), None)
    if hook is not None:
        return "hook", hook
    if root in TEST_FUNCTIONS:
        return "test", ""
    return None


def _arguments(call: Node) -> list[Node]:
    args = call.child_by_field_name("arguments")
    return list(args.named_children) if args is not None else []


def _string_value(node: Node, source: bytes) -> str:
    text = node_text(node, source)
    return text[1:-1] if len(text) >= 2 else text


def _destructured_names(callback: Node, source: bytes) -> tuple[str, ...]:
    """`async ({ loginPage, page: p }) => ...` -> ("loginPage", "p" bound to
    fixture "page")... returned as fixture names in the first parameter's
    object pattern (`{ a, b: alias }` binds `alias`, recorded as "b=alias")."""
    params = callback.child_by_field_name("parameters")
    if params is None:
        return ()
    first = next(iter(params.named_children), None)
    if first is None:
        return ()
    pattern = first.child_by_field_name("pattern") or first
    if pattern.type != "object_pattern":
        return ()
    names: list[str] = []
    for child in pattern.named_children:
        if child.type == "shorthand_property_identifier_pattern":
            names.append(node_text(child, source))
        elif child.type == "pair_pattern":
            key, value = child.child_by_field_name("key"), child.child_by_field_name("value")
            if key is not None and value is not None and value.type == "identifier":
                names.append(f"{node_text(key, source)}={node_text(value, source)}")
        elif child.type == "object_assignment_pattern":
            left = child.child_by_field_name("left")
            if left is not None and left.type == "shorthand_property_identifier_pattern":
                names.append(node_text(left, source))
    return tuple(names)


def _fixture_object(call: Node, source: bytes) -> Node | None:
    """The `{...}` argument of a `<x>.extend(...)` call, else None."""
    segments = _callee_segments(call, source)
    if not segments or len(segments) < 2 or segments[-1] != "extend":
        return None
    args = _arguments(call)
    return args[0] if args and args[0].type == "object" else None


def _fixture_callback(value: Node) -> Node | None:
    if value.type in _CALLBACK_TYPES:
        return value
    if value.type == "array":            # [async ({}, use) => {...}, { scope: 'worker' }]
        first = next(iter(value.named_children), None)
        if first is not None and first.type in _CALLBACK_TYPES:
            return first
    return None


def find_test_blocks(root: Node, source: bytes, module: str) -> list[TestBlock]:
    blocks: list[TestBlock] = []
    used: dict[str, int] = {}

    def unique(name: str) -> str:
        n = used.get(name, 0) + 1
        used[name] = n
        return name if n == 1 else f"{name}_{n}"

    def visit(node: Node, prefix: tuple[str, ...]) -> None:
        if node.type == "call_expression":
            fixtures = _fixture_object(node, source)
            if fixtures is not None:
                for pair in fixtures.named_children:
                    if pair.type != "pair":
                        continue
                    key, value = pair.child_by_field_name("key"), pair.child_by_field_name("value")
                    callback = _fixture_callback(value) if value is not None else None
                    if key is None or callback is None:
                        continue
                    qname = unique(".".join((module, *prefix, f"fixture_{_slug(node_text(key, source))}")))
                    blocks.append(TestBlock(qname, "fixture", callback, pair, _destructured_names(callback, source)))
                    visit(callback, prefix)
                return
            segments = _callee_segments(node, source)
            kind = _classify(segments) if segments else None
            if kind is not None:
                args = _arguments(node)
                callback = next((a for a in reversed(args) if a.type in _CALLBACK_TYPES), None)
                title = next((_string_value(a, source) for a in args if a.type in _STRING_TYPES), "")
                if callback is not None:
                    block_kind, hook = kind
                    if block_kind == "group":
                        visit(callback, (*prefix, _slug(title) or "describe"))
                        return
                    leaf = hook if block_kind == "hook" else (_slug(title) or "test")
                    qname = unique(".".join((module, *prefix, leaf)))
                    blocks.append(TestBlock(qname, block_kind, callback, node, _destructured_names(callback, source)))
                    return
        for child in node.children:
            visit(child, prefix)

    visit(root, ())
    return blocks


def _type_name(type_node: Node | None, source: bytes) -> str | None:
    if type_node is None:
        return None
    if type_node.type == "type_annotation":
        type_node = next(iter(type_node.named_children), None)
    if type_node is not None and type_node.type == "type_identifier":
        return node_text(type_node, source)
    return None


def _object_type_members(object_type: Node, source: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    for member in object_type.named_children:
        if member.type != "property_signature":
            continue
        name = member.child_by_field_name("name")
        type_name = _type_name(member.child_by_field_name("type"), source)
        if name is not None and type_name is not None:
            out[node_text(name, source)] = type_name
    return out


def _named_object_types(root: Node, source: bytes) -> dict[str, Node]:
    """Same-file `type X = {...}` / `interface X {...}` bodies by name."""
    found: dict[str, Node] = {}
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in ("type_alias_declaration", "interface_declaration"):
            name = node.child_by_field_name("name")
            body = node.child_by_field_name("value") or node.child_by_field_name("body")
            if name is not None and body is not None and body.type in ("object_type", "interface_body"):
                found[node_text(name, source)] = body
        stack.extend(node.children)
    return found


def _constructed_class(callback: Node, source: bytes) -> str | None:
    """`use(new C(...))` (or `const x = new C(); await use(x)`) in a
    fixture body -> "C"."""
    params = callback.child_by_field_name("parameters")
    named = list(params.named_children) if params is not None else []
    use_name = "use"
    if len(named) >= 2:
        pattern = named[1].child_by_field_name("pattern") or named[1]
        if pattern.type == "identifier":
            use_name = node_text(pattern, source)
    locals_: dict[str, str] = {}
    result: str | None = None
    stack = [callback]
    calls: list[Node] = []
    while stack:
        node = stack.pop()
        if node.type == "variable_declarator":
            name, value = node.child_by_field_name("name"), node.child_by_field_name("value")
            if name is not None and value is not None:
                ctor = _new_class(value, source)
                if ctor is not None:
                    locals_[node_text(name, source)] = ctor
        elif node.type == "call_expression":
            calls.append(node)
        stack.extend(node.children)
    for call in calls:
        fn = call.child_by_field_name("function")
        if fn is None or fn.type != "identifier" or node_text(fn, source) != use_name:
            continue
        args = _arguments(call)
        if not args:
            continue
        arg = args[0]
        result = _new_class(arg, source) or (locals_.get(node_text(arg, source)) if arg.type == "identifier" else None)
        if result is not None:
            return result
    return None


def _new_class(value: Node, source: bytes) -> str | None:
    while value.type in ("await_expression", "parenthesized_expression") and value.named_children:
        value = value.named_children[0]
    if value.type != "new_expression":
        return None
    ctor = value.child_by_field_name("constructor")
    return node_text(ctor, source) if ctor is not None and ctor.type == "identifier" else None


def find_fixture_types(root: Node, source: bytes) -> dict[str, str]:
    """Fixture name -> class name (unresolved) declared by every
    `<x>.extend<T>({...})` call in one file."""
    named_types: dict[str, Node] | None = None
    out: dict[str, str] = {}
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.type != "call_expression":
            continue
        fixtures = _fixture_object(node, source)
        if fixtures is None:
            continue
        type_args = node.child_by_field_name("type_arguments")
        declared: dict[str, str] = {}
        for arg in type_args.named_children if type_args is not None else ():
            if arg.type == "object_type":
                declared.update(_object_type_members(arg, source))
            elif arg.type == "type_identifier":
                if named_types is None:
                    named_types = _named_object_types(root, source)
                body = named_types.get(node_text(arg, source))
                if body is not None:
                    declared.update(_object_type_members(body, source))
        for pair in fixtures.named_children:
            if pair.type != "pair":
                continue
            key, value = pair.child_by_field_name("key"), pair.child_by_field_name("value")
            if key is None or value is None:
                continue
            name = node_text(key, source)
            callback = _fixture_callback(value)
            constructed = _constructed_class(callback, source) if callback is not None else None
            class_name = declared.get(name) or constructed
            if class_name is not None:
                out[name] = class_name
    return out
