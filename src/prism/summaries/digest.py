"""PRISM digest: what one function's own code does, as line-cited facts.

One walk over the function's syntax tree (every supported grammar: Python,
JavaScript / TypeScript / TSX, Java, C#, Go), the same procedure for every
function whatever its length or subject. Each fact is a construct of the
language, not a guess about intent:

    return / yield      the returned / yielded expression
    raise               `raise` / `throw` / `panic(...)` / `assert` / `abort(...)` / process exit
    catch               an except / catch clause and what its body does with the error
    state               a write outside the function's locals: `self.x = ...`, `param.x[k] = ...`,
                        a mutating method on self / a parameter / an imported name, `global`
    assign              a local binding (`x = ...`), kept only while the budget allows
    call                a call site and its target: the resolved index symbol, a builtin, an
                        imported library name, or "target unknown"
    read                a setting / environment / header key the body reads
    dynamic             a call whose target is chosen at runtime: reflection (`getattr`,
                        `Reflect`, `Class.forName`, `reflect.*`), `eval`, subscript dispatch
                        (`handlers[k]()`), a call of a parameter (callback), a dynamic import
    decorator           a decorator / annotation on the definition and whether its effect is known
    entry               the function is a registered entry point (route, command, handler)
    define              a nested function or class defined in the body

Every fact carries `when`: the conditions under which it happens, read from
the enclosing `if` / `elif` / `else` / `switch` case / loop / `except` /
`with` / callback, innermost last - so `if port is None: return None` is the
fact "return None, when port is None".

Every fact carries a certainty about what it claims:

    certain    read directly from the syntax (and, for a call, a single resolved target)
    possible   the target is one of a listed set (ambiguous resolution, tentative
               dispatch, candidates derived from the code - never invented)
    runtime    a recorded runtime trace (`prism trace`) observed this target
    unknown    the target cannot be known from the code; the raw line is kept

A dynamic or unknown fact keeps its raw source line (`raw`) so a reader sees
exactly the code the digest could not explain.

Output size is bounded by `max_facts`: facts are ranked by kind (dynamic,
entry, raise, catch, return, state first; local assignments last) and the
omitted ones are counted by kind, never silently dropped.
"""
from __future__ import annotations

import builtins as _py_builtins
import json
import re
from dataclasses import asdict, dataclass, field

from prism.graph.call_site import detect_call_site_hazard
from prism.parser.lang_config import (
    CALL_NODE_TYPE,
    CLASS_NODE_TYPES,
    DECORATED_WRAPPER_TYPES,
    FUNCTION_NODE_TYPES,
    call_callee_segments,
    collect_annotation_texts,
    collect_decorator_texts,
    flatten_reference_chain,
)
from prism.parser.tree_sitter_loader import LanguageID

DIGEST_VERSION = 1

CERTAIN, POSSIBLE, RUNTIME, UNKNOWN = "certain", "possible", "runtime", "unknown"
CERTAINTIES = (CERTAIN, POSSIBLE, RUNTIME, UNKNOWN)
KINDS = ("entry", "decorator", "dynamic", "raise", "catch", "return", "yield", "flow", "state", "read", "call",
         "calls", "define", "assign", "runtime")
#: lower ranks are kept first when a digest is over budget
_RANK = {"dynamic": 0, "entry": 0, "runtime": 0, "raise": 1, "catch": 1, "decorator": 1, "return": 2,
         "yield": 2, "flow": 2, "state": 2, "read": 3, "call": 4, "calls": 4, "define": 4, "assign": 5}
MAX_FACTS = 40
EXPR_CAP = 90       # characters of one expression kept in a fact
RAW_CAP = 160       # characters of a raw (unexplained) source line
CAND_CAP = 6        # candidate targets listed for a possible fact
_MAX_DEPTH = 300    # same guard as lang_config.MAX_SCOPED_NODE_DEPTH

_JS = frozenset({LanguageID.JAVASCRIPT, LanguageID.TYPESCRIPT, LanguageID.TSX})
_SELF = frozenset({"self", "this", "cls"})

# ---- per-grammar node types ------------------------------------------------ #
_IF = frozenset({"if_statement", "elif_clause"})
_LOOPS = frozenset({"for_statement", "for_in_statement", "while_statement", "do_statement",
                    "enhanced_for_statement", "foreach_statement"})
_CASES = frozenset({"switch_case", "switch_default", "switch_block_statement_group", "switch_rule",
                    "switch_section", "expression_case", "default_case", "type_case", "case_clause",
                    "communication_case"})
_SWITCH_SUBJECT_FIELDS = ("value", "condition", "subject")
_CATCH = frozenset({"except_clause", "catch_clause"})
_FINALLY = frozenset({"finally_clause"})
_WITH = frozenset({"with_statement", "using_statement"})
_INLINE_FUNCTIONS = frozenset({"lambda", "arrow_function", "function_expression", "function", "func_literal",
                               "lambda_expression", "anonymous_method_expression", "generator_function"})
_BLOCKS = frozenset({"block", "statement_block", "constructor_body", "statement_list", "class_body",
                     "declaration_list", "enum_body", "interface_body", "field_declaration_list", "object_type",
                     "enum_class_body", "enum_member_declaration_list", "record_declaration_body"})
_EMBEDDING = frozenset({"return_statement", "raise_statement", "throw_statement", "throw_expression",
                        "assert_statement", "assignment", "augmented_assignment", "assignment_expression",
                        "augmented_assignment_expression", "assignment_statement", "variable_declarator",
                        "short_var_declaration", "yield", "yield_expression"})
_BRANCH_FIELDS = frozenset({"body", "consequence", "alternative"})
_ARGUMENT_LISTS = frozenset({"arguments", "argument_list"})
_RETURN = frozenset({"return_statement"})
_YIELD = frozenset({"yield", "yield_expression", "yield_statement"})
_THROW = frozenset({"raise_statement", "throw_statement", "throw_expression"})
_ASSERT = frozenset({"assert_statement"})
_ASSIGN = frozenset({"assignment", "augmented_assignment", "assignment_expression",
                     "augmented_assignment_expression", "assignment_statement"})
_DECLARE = frozenset({"variable_declarator", "short_var_declaration"})
_INCDEC = frozenset({"inc_statement", "dec_statement", "update_expression", "postfix_unary_expression",
                     "prefix_unary_expression"})
_DELETE = frozenset({"delete_statement"})
_GLOBAL = frozenset({"global_statement", "nonlocal_statement"})
_NEW = frozenset({"new_expression", "object_creation_expression"})
_SUBSCRIPTS = frozenset({"subscript", "subscript_expression", "index_expression", "element_access_expression"})
_ATTRIBUTES = frozenset({"attribute", "member_expression", "selector_expression", "field_access",
                         "member_access_expression"})

#: methods that change their receiver (`prism.semantics.contract_axis.MUTATING_METHODS`, plus the
#: Java / C# / Go collection verbs)
from prism.semantics.contract_axis import MUTATING_METHODS as _CA_MUTATING  # noqa: E402

MUTATING_METHODS = _CA_MUTATING | frozenset({
    "put", "putAll", "addAll", "removeAll", "removeIf", "Add", "AddRange", "Remove", "RemoveAt", "Clear",
    "Insert", "Push", "Pop", "Enqueue", "Dequeue", "Store", "Delete", "Set", "SaveChanges", "assign",
    "removeItem", "setItem", "setdefault", "write", "writelines",
})

#: calls that end the function abnormally (beyond `raise` / `throw`)
_EXIT_CALLS = {"panic": "panics", "abort": "aborts", "exit": "exits the process", "_exit": "exits the process",
               "Exit": "exits the process", "Fatal": "logs and exits", "Fatalf": "logs and exits",
               "Fatalln": "logs and exits"}
_EXIT_ROOTS = {"sys", "os", "process", "System", "Environment", "log", "flask", "werkzeug", None}

# ---- builtins and standard roots, per language ----------------------------- #
_PY_BUILTINS = frozenset(n for n in dir(_py_builtins) if not n.startswith("_")) | {"__import__"}
_JS_GLOBALS = frozenset({
    "Object", "Array", "JSON", "Math", "Promise", "console", "parseInt", "parseFloat", "String", "Number",
    "Boolean", "Symbol", "Date", "RegExp", "Error", "TypeError", "RangeError", "Map", "Set", "WeakMap",
    "WeakSet", "Reflect", "Proxy", "isNaN", "isFinite", "encodeURIComponent", "decodeURIComponent",
    "encodeURI", "decodeURI", "setTimeout", "clearTimeout", "setInterval", "clearInterval", "queueMicrotask",
    "structuredClone", "BigInt", "Buffer", "process", "require", "fetch", "URL", "URLSearchParams",
    "TextEncoder", "TextDecoder", "globalThis", "Intl", "Atomics", "ArrayBuffer", "Uint8Array", "Int32Array",
    "Float64Array", "DataView", "AbortController", "Response", "Request", "Headers", "Blob", "FormData",
    "eval", "Function", "atob", "btoa", "crypto", "performance", "document", "window", "navigator",
})
_GO_BUILTINS = frozenset({"len", "cap", "append", "make", "new", "panic", "recover", "copy", "delete", "close",
                          "print", "println", "min", "max", "clear", "complex", "real", "imag", "string",
                          "int", "int64", "int32", "uint", "uint8", "uint64", "byte", "rune", "float64",
                          "float32", "bool", "error"})
_JAVA_STD = frozenset({"System", "Math", "String", "Integer", "Long", "Double", "Boolean", "Character",
                       "Objects", "Arrays", "Collections", "List", "Map", "Set", "Optional", "Stream",
                       "Collectors", "Thread", "StringBuilder", "Files", "Paths", "Path", "UUID", "Instant",
                       "LocalDate", "LocalDateTime", "Duration", "Pattern", "Class", "Enum", "Byte", "Short",
                       "Float", "BigDecimal", "BigInteger", "CompletableFuture", "Executors", "TimeUnit"})
_CS_STD = frozenset({"Console", "Math", "String", "Convert", "Task", "Enumerable", "File", "Path", "Directory",
                     "Guid", "DateTime", "DateTimeOffset", "TimeSpan", "Environment", "Activator", "Type",
                     "Array", "List", "Dictionary", "Int32", "Int64", "Double", "Boolean", "Encoding",
                     "Regex", "Debug", "Trace", "Thread", "Interlocked", "Volatile", "Uri", "Nullable",
                     "Object", "GC", "Marshal", "Buffer", "BitConverter", "JsonSerializer", "string", "object"})

#: decorators / annotations whose effect is known and does not hide behaviour
_KNOWN_DECORATORS = {
    "property": "read as an attribute", "staticmethod": "static method", "classmethod": "class method",
    "abstractmethod": "abstract method", "abc.abstractmethod": "abstract method",
    "cached_property": "computed once per instance, then cached", "functools.cached_property": "computed once per instance, then cached",
    "lru_cache": "results cached by arguments", "functools.lru_cache": "results cached by arguments",
    "cache": "results cached by arguments", "functools.cache": "results cached by arguments",
    "wraps": "copies the wrapped function's metadata", "functools.wraps": "copies the wrapped function's metadata",
    "overload": "typing overload (no runtime body)", "typing.overload": "typing overload (no runtime body)",
    "override": "overrides a base method", "typing.override": "overrides a base method",
    "Override": "overrides a base method", "contextmanager": "used as a `with` context manager",
    "contextlib.contextmanager": "used as a `with` context manager",
    "asynccontextmanager": "used as an `async with` context manager",
    "contextlib.asynccontextmanager": "used as an `async with` context manager",
    "dataclass": "dataclass", "dataclasses.dataclass": "dataclass", "final": "may not be overridden",
    "Deprecated": "deprecated", "deprecated": "deprecated", "SuppressWarnings": "compiler hint",
    "Nullable": "may return null", "NonNull": "never returns null", "Test": "test case",
}
#: Java / C# reflection and dynamic-invocation method names (receiver-independent)
_JVM_REFLECTION = frozenset({"forName", "getMethod", "getDeclaredMethod", "invoke", "newInstance",
                             "getDeclaredConstructor", "getConstructor", "getField", "getDeclaredField"})
_CS_REFLECTION = frozenset({"GetMethod", "Invoke", "CreateInstance", "GetProperty", "GetField", "InvokeMember",
                            "DynamicInvoke", "GetType"})
_PY_DYNAMIC_IMPORTS = frozenset({("importlib", "import_module"), ("import_module",), ("__import__",)})
_PY_REFLECTION = frozenset({"getattr", "setattr", "delattr", "hasattr"})
_JS_INDIRECT = frozenset({"call", "apply", "bind"})
_GETENV = frozenset({"getenv", "Getenv", "LookupEnv", "GetEnvironmentVariable"})
_SETTINGS_ROOTS = frozenset({"settings", "config", "conf", "CONFIG", "SETTINGS"})
_HEADER_OBJECTS = frozenset({"META", "headers", "environ", "COOKIES", "cookies", "GET", "POST", "query_params",
                             "env", "args", "form", "params", "query", "body"})
_CS_USING_ALIAS = re.compile(r"^\s*using\s+(\w+)\s*=\s*([\w.]+)\s*;", re.MULTILINE)
_PRAGMA_CALL_RE = re.compile(r"([A-Za-z_$][\w$]*)\s*$")


@dataclass(frozen=True)
class Fact:
    kind: str
    text: str
    lines: tuple[int, int]
    certainty: str = CERTAIN
    when: tuple[str, ...] = ()
    targets: tuple[str, ...] = ()
    raw: str | None = None
    count: int = 1
    #: one id per `when` entry (the syntax node it came from), so two separate `if x:` blocks are not
    #: rendered as one; empty when unknown
    scope: tuple[int, ...] = field(default=(), compare=False, repr=False)


@dataclass
class Digest:
    qname: str
    language: str
    lines: tuple[int, int]
    facts: list[Fact] = field(default_factory=list)
    omitted: dict[str, int] = field(default_factory=dict)
    note: str | None = None
    version: int = DIGEST_VERSION

    def to_dict(self) -> dict:
        d = asdict(self)
        d["facts"] = [{k: v for k, v in asdict(f).items()
                       if k != "scope" and (v not in (None, (), 1) or k in ("kind", "text", "lines", "certainty"))}
                      for f in self.facts]
        return d

    def render(self, header: bool = True) -> str:
        """Facts in line order, grouped under the conditions they run in (indented, innermost last)."""
        out = []
        if header:
            out.append(f"Digest (lines {self.lines[0]}-{self.lines[1]})" + (f": {self.note}" if self.note else ""))
        elif self.note:
            out.append(self.note)
        nested = {(f.lines, f.when): f for f in self.facts if f.kind == "calls"}
        attached = set()
        for f in self.facts:
            key = (f.lines, f.when)
            if f.kind != "calls" and key in nested and key not in attached:
                attached.add(key)
        open_when: tuple[str, ...] = ()
        open_scope: tuple[int, ...] = ()
        shown: set = set()
        for f in self.facts:
            if f.kind == "calls" and (f.lines, f.when) in attached:
                continue
            common = 0
            while common < min(len(open_when), len(f.when)) and open_when[common] == f.when[common] and (
                    len(open_scope) <= common or len(f.scope) <= common or open_scope[common] == f.scope[common]):
                common += 1
            for depth in range(common, len(f.when)):
                out.append("  " * depth + f"when {f.when[depth]}:")
            open_when, open_scope = f.when, f.scope
            line = _render_fact(f)
            key = (f.lines, f.when)
            if key in attached and f.kind != "calls" and key not in shown:
                line += f" [calls: {nested[key].text}]"
                shown.add(key)
            out.append("  " * len(f.when) + line)
        if self.omitted:
            out.append("omitted: " + ", ".join(f"{n} {k}" for k, n in sorted(self.omitted.items())))
        return "\n".join(out)


_VERB = {"call": "call ", "calls": "calls inside: ", "read": "read ", "dynamic": "dynamic: ", "return": "return ", "yield": "yield ",
         "runtime": "runtime: "}


def _render_fact(f: Fact) -> str:
    span = f"L{f.lines[0]}" if f.lines[0] == f.lines[1] else f"L{f.lines[0]}-{f.lines[1]}"
    line = f"{span} {_VERB.get(f.kind, '')}{f.text}" + (f" (x{f.count})" if f.count > 1 else "")
    if f.kind in ("call", "dynamic", "runtime"):
        line += _render_certainty(f)
    if f.raw:
        line += f" | code: {f.raw}"
    return line


def _target_label(t: str) -> str:
    for prefix, label in (("builtin ", "builtin"), ("standard library ", "std ")):  # "library"/"unindexed" stay
        if t.startswith(prefix):
            return (label + t[len(prefix):]) if label != "builtin" else "builtin"
    return t


def _render_certainty(f: Fact) -> str:
    targets = ", ".join(_target_label(t) for t in f.targets)
    if f.certainty == CERTAIN:
        return f" -> {targets}" if targets else ""
    if f.certainty == POSSIBLE:
        return f" -> one of: {targets}" if targets else " -> possible"
    if f.certainty == RUNTIME:
        return f" -> seen at runtime: {targets}"
    if f.kind == "dynamic":
        return " -> unknown" + (f" ({targets})" if targets else "")
    return " -> ?"


# ---- small helpers --------------------------------------------------------- #
def _text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf8", "replace")


def _short(text: str, cap: int = EXPR_CAP) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


def _expr(node, src: bytes) -> str:
    return _short(_text(node, src))


def _span(node) -> tuple[int, int]:
    return node.start_point[0] + 1, node.end_point[0] + 1


def _strip_parens(text: str) -> str:
    text = text.strip()
    while text.startswith("(") and text.endswith(")") and _balanced(text[1:-1]):
        text = text[1:-1].strip()
    return text


def _balanced(text: str) -> bool:
    depth = 0
    for ch in text:
        depth += ch == "("
        depth -= ch == ")"
        if depth < 0:
            return False
    return depth == 0


def _negate(cond: str) -> str:
    return f"not ({cond})"


def _raw_line(node, src: bytes) -> str:
    start = src.rfind(b"\n", 0, node.start_byte) + 1
    end = src.find(b"\n", node.start_byte)
    return _short(src[start: end if end != -1 else len(src)].decode("utf8", "replace"), RAW_CAP)


def _leaf(call, src: bytes, lang) -> tuple[list[str] | None, str]:
    """(callee segments or None, the callee's last name)."""
    if call.type == CALL_NODE_TYPE.get(lang):
        segments = call_callee_segments(call, src, lang)
    else:  # `new Foo(...)` / `new pkg.Foo<T>(...)`
        ctor = call.child_by_field_name("constructor") or call.child_by_field_name("type")
        if ctor is not None and ctor.type in ("generic_type", "generic_name"):
            ctor = ctor.named_children[0] if ctor.named_children else ctor
        segments = flatten_reference_chain(ctor, src, lang) if ctor is not None else None
        if segments is None and ctor is not None and re.fullmatch(r"[\w$]+(\.[\w$]+)*", _text(ctor, src)):
            segments = _text(ctor, src).split(".")
    if segments:
        return segments, segments[-1]
    func = call.child_by_field_name("function") or call.child_by_field_name("name") or \
        call.child_by_field_name("constructor") or call.child_by_field_name("type")
    text = _text(func, src) if func is not None else ""
    text = re.sub(r"<[^<>]*>$", "", text.strip())  # generic arguments: `new Foo<T>()`
    match = _PRAGMA_CALL_RE.search(text.rstrip("?!. "))
    return None, (match.group(1) if match else "")


def _elide_args(text: str) -> str:
    """`commands.filter((c) => x(c)).map` -> `commands.filter(…).map`: a chain's inner arguments elided."""
    previous = None
    while previous != text:
        previous, text = text, re.sub(r"\([^()]*\)", "\u2060", text)
    return re.sub(r"\s+", " ", re.sub(r"\s*\.\s*", ".", text)).strip().replace("\u2060", "(…)")


def _callee_text(call, src: bytes, lang) -> str:
    if lang == LanguageID.JAVA and call.type == "method_invocation":
        args = call.child_by_field_name("arguments")
        return _short(_text(call, src)[: (args.start_byte - call.start_byte) if args is not None else None], 60)
    func = call.child_by_field_name("function") or call.child_by_field_name("constructor") or \
        call.child_by_field_name("type")
    return _short(_text(func if func is not None else call, src), 60)


def _params(def_node, src: bytes) -> list[str]:
    params = def_node.child_by_field_name("parameters") or def_node.child_by_field_name("parameter")
    return _params_of(params, src) if params is not None else []


def _params_of(params, src: bytes) -> list[str]:
    if params.type == "identifier":  # `x => ...`
        return [_text(params, src)]
    names: list[str] = []
    for p in params.named_children:
        node = p
        for fname in ("name", "pattern", "left"):
            child = p.child_by_field_name(fname)
            if child is not None:
                node = child
                break
        if node.type in ("identifier", "this"):
            names.append(_text(node, src))
        elif node.type in ("list_splat_pattern", "dictionary_splat_pattern", "typed_parameter",
                           "typed_default_parameter", "default_parameter", "rest_pattern"):
            ident = next((c for c in node.named_children if c.type == "identifier"), None)
            if ident is not None:
                names.append(_text(ident, src))
        elif p.type == "parameter_declaration":  # Go: `a, b int`
            names.extend(_text(c, src) for c in p.named_children if c.type == "identifier")
    return [n for n in dict.fromkeys(names) if n]


_TYPE_SKIP = frozenset({"var", "let", "const", "any", "object", "Object", "dynamic", "auto", "unknown", "None",
                        "Any", "Self", "self", "void", "interface", "func", "map", "chan", "struct", "typing"})


def _type_name(text: str, js: bool = False) -> str | None:
    """The class name a declared type refers to: `Optional[Foo]` / `Foo | None` / `*pkg.Foo` / `List<Foo>` ->
    the outer name (`Foo`, `Foo`, `Foo`, `List`); None for no single named type."""
    t = text.strip().lstrip(":").strip()
    t = re.sub(r"^(readonly|final|const|ref|out|in|params)\s+", "", t)
    m = re.fullmatch(r"(?:typing\.)?Optional\[(.*)\]", t)
    if m:
        t = m.group(1)
    if "|" in t and "<" not in t and "[" not in t:
        parts = [p.strip() for p in t.split("|") if p.strip() not in ("None", "null", "undefined", "nil")]
        if len(parts) != 1:
            return None
        t = parts[0]
    t = t.strip("\"' ").rstrip("?!")
    if t.endswith("[]") or t.startswith("[]") or re.match(r"^\[\d*\]", t):
        return "Array" if js else None  # an array / slice: its methods are the array type's, not the element's
    t = t.lstrip("*&")
    t = re.sub(r"(<.*>|\[.*\])$", "", t).strip()
    name = t.rsplit(".", 1)[-1].strip()
    if not re.fullmatch(r"[A-Za-z_$][\w$]*", name) or name in _TYPE_SKIP or len(name) == 1:
        return None
    return name


def _declared_types(def_node, src: bytes, lang, classes) -> tuple[dict[str, str], dict[str, str]]:
    """`({variable: declared class name}, {variable: fully qualified Java / C# type})` for the parameters and
    locals of one function (first declaration wins): annotations, typed declarations, and `x = Foo(...)` /
    `new Foo()` / `&Foo{}` constructions."""
    types: dict[str, str] = {}
    qualified: dict[str, str] = {}

    def put(name_node, type_text) -> None:
        if name_node is None or not type_text:
            return
        t = _type_name(type_text, lang in _JS)
        if t:
            name = _text(name_node, src)
            types.setdefault(name, t)
            dotted = re.sub(r"<.*>|\[.*\]", "", type_text.strip().lstrip(":").strip()).lstrip("*&").strip()
            if lang in (LanguageID.JAVA, LanguageID.CSHARP) and dotted.count(".") >= 2 and dotted.endswith("." + t):
                qualified.setdefault(name, dotted)

    containers = [def_node.child_by_field_name("parameters"), def_node.child_by_field_name("receiver")]
    for params in containers:
        for p in (params.named_children if params is not None else []):
            ptype = p.child_by_field_name("type")
            if ptype is None:
                continue
            names = [c for c in (p.child_by_field_name("name"), p.child_by_field_name("pattern")) if c is not None]
            names = names or [c for c in p.named_children if c.type == "identifier"]
            for n in names:
                put(n, _text(ptype, src))
    body = _body(def_node)
    stack = [body] if body is not None else []
    skip = CLASS_NODE_TYPES.get(lang, set()) | FUNCTION_NODE_TYPES.get(lang, set())
    steps = 0
    while stack and steps < 50000:
        steps += 1
        n = stack.pop()
        if n.type in skip:
            continue
        t = n.type
        if t in ("local_variable_declaration", "variable_declaration", "field_declaration"):
            dtype = n.child_by_field_name("type")
            for d in n.named_children:
                if d.type != "variable_declarator":
                    continue
                name = d.child_by_field_name("name") or next((c for c in d.named_children if c.type == "identifier"), None)
                if dtype is not None and dtype.type != "implicit_type":
                    put(name, _text(dtype, src))
                else:
                    value = d.child_by_field_name("value") or next(
                        (c for c in d.named_children if c.type in _NEW), None)
                    if value is not None and value.type in _NEW:
                        ctor = value.child_by_field_name("type") or value.child_by_field_name("constructor")
                        put(name, _text(ctor, src) if ctor is not None else None)
        elif t == "variable_declarator" and lang in _JS:
            name, ann, value = n.child_by_field_name("name"), n.child_by_field_name("type"), n.child_by_field_name("value")
            if ann is not None:
                put(name, _text(ann, src))
            elif value is not None and value.type in _NEW:
                ctor = value.child_by_field_name("constructor")
                put(name, _text(ctor, src) if ctor is not None else None)
        elif t == "assignment" and lang == LanguageID.PYTHON:
            left, ann, right = n.child_by_field_name("left"), n.child_by_field_name("type"), n.child_by_field_name("right")
            if left is not None and left.type == "identifier":
                if ann is not None:
                    put(left, _text(ann, src))
                elif right is not None and right.type == "call":
                    func = right.child_by_field_name("function")
                    fname = _text(func, src).rsplit(".", 1)[-1] if func is not None else ""
                    if fname in classes():
                        put(left, fname)
        elif t == "var_spec":
            vtype = n.child_by_field_name("type")
            for c in n.named_children:
                if c.type == "identifier" and vtype is not None:
                    put(c, _text(vtype, src))
        elif t == "short_var_declaration":
            left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
            lefts = [c for c in (left.named_children if left is not None else []) if c.type == "identifier"]
            rights = list(right.named_children) if right is not None else []
            for name, value in zip(lefts, rights):
                if value.type == "unary_expression":
                    value = value.child_by_field_name("operand") or value
                if value.type == "composite_literal":
                    ctype = value.child_by_field_name("type")
                    put(name, _text(ctype, src) if ctype is not None else None)
        stack.extend(n.named_children)
    return types, qualified


def _outer_params(def_node, src: bytes, function_types) -> set[str]:
    """Parameters of the functions enclosing a nested function (closure captures)."""
    names: set[str] = set()
    parent = def_node.parent
    while parent is not None:
        if parent.type in function_types:
            names.update(_params(parent, src))
        parent = parent.parent
    return names


def _body(def_node):
    body = def_node.child_by_field_name("body")
    if body is None and def_node.type == "variable_declarator":
        value = def_node.child_by_field_name("value")
        body = value.child_by_field_name("body") if value is not None else None
    if body is None and def_node.type in ("pair", "public_field_definition", "field_definition"):
        value = def_node.child_by_field_name("value")
        body = value.child_by_field_name("body") if value is not None else None
    return body


# ---- the extractor --------------------------------------------------------- #
class _Extractor:
    @staticmethod
    def nested_skip_types(lang) -> set[str]:
        return FUNCTION_NODE_TYPES.get(lang, set()) | {"lambda", "arrow_function", "function_expression", "function"}

    def __init__(self, builder, qname: str, def_node, parsed, import_map, entry, runtime: set[str] | None,
                 indexed_defs: dict[int, str], modules, classes, leaves=frozenset, aliases=None):
        self.b, self.qname, self.node, self.parsed = builder, qname, def_node, parsed
        self.sym = builder.symbol_table.get(qname)
        self.src, self.lang = parsed.source, parsed.language_id
        self.import_map = import_map
        self.entry = entry
        self.runtime = runtime or set()
        self.indexed = indexed_defs
        self.modules = modules  # () -> the index's module names, computed once per DigestBuilder
        self.params = _params(def_node, self.src)
        self.classes = classes  # () -> {class leaf name: [qualified names]}, computed once per DigestBuilder
        self.leaves = leaves  # () -> every symbol's last name
        self.aliases = aliases or {}  # this file's `using X = A.B;` aliases
        self.types, self.qualified_types = _declared_types(def_node, self.src, self.lang, classes)
        self.outer_params = _outer_params(def_node, self.src, self.nested_skip_types(self.lang))
        self.inline_params: set[str] = set()
        receiver = def_node.child_by_field_name("receiver")  # Go: `func (s *Server) Serve()`
        self.self_names = set(_SELF) | (set(_params_of(receiver, self.src)) if receiver is not None else set())
        self.facts: list[Fact] = []
        self.call_type = CALL_NODE_TYPE.get(self.lang)
        self.nested_skip = (CLASS_NODE_TYPES.get(self.lang, set()) | FUNCTION_NODE_TYPES.get(self.lang, set())
                            | DECORATED_WRAPPER_TYPES.get(self.lang, set()))
        self.targets = self._edge_targets()
        self.local_dicts: dict[str, list[str]] = {}
        self.local_names: set[str] = set()
        self.local_values: dict[str, str] = {}
        self.dynamic_bindings: dict[str, tuple[str, tuple[str, ...]]] = {}
        self.globals: set[str] = set()
        self.nested: dict[int, list] = {}
        self.scope: tuple[int, ...] = ()

    # graph out-edges of this function, by the callee's last name
    def _edge_targets(self) -> dict[str, list[tuple[str, bool]]]:
        by_leaf: dict[str, list[tuple[str, bool]]] = {}
        graph = self.b.graph
        if self.qname not in graph:
            return by_leaf
        for _u, target, data in graph.out_edges(self.qname, data=True):
            if data.get("relation", "CALLS") not in ("CALLS", "INSTANTIATES"):
                continue
            tentative = data.get("kind") == "TENTATIVE_CALL"
            if target.startswith("<"):
                m = re.match(r"<ambiguous:([^@>]+)@", target)
                if m:
                    candidates = graph.nodes[target].get("candidates") or ()
                    for cand in candidates or ():
                        by_leaf.setdefault(m.group(1).rsplit(".", 1)[-1], []).append((cand, True))
                    by_leaf.setdefault(m.group(1).rsplit(".", 1)[-1], [])
                continue
            label = self._outside_label(target) if graph.nodes[target].get("external") and \
                target not in self.b.symbol_table else target
            by_leaf.setdefault(target.rsplit(".", 1)[-1], []).append((label, tentative))
        return by_leaf

    def add(self, kind: str, text: str, node, when, certainty: str = CERTAIN, targets=(), raw: str | None = None,
            scope=None):
        scope = self.scope if scope is None else scope
        self.facts.append(Fact(kind, text, _span(node), certainty, tuple(when), tuple(targets)[:CAND_CAP], raw,
                               scope=tuple(scope) if len(scope) == len(when) else ()))

    # -- context: the condition a child runs under ------------------------- #
    def _context(self, parent, child, index: int) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """(conditions from `child`'s position in `parent`, conditions `child` itself adds for its body)."""
        return self._field_context(parent, child, index), self._own_context(parent, child)

    def _own_context(self, parent, child) -> tuple[str, ...]:
        if child.type in _CASES:
            return (self._case_text(parent, child),)
        if child.type in _CATCH:
            return (f"on {self._catch_type(child)}",)
        if child.type in _FINALLY:
            return ("finally (always)",)
        if child.type == "defer_statement":
            return ("deferred until the function returns",)
        if child.type == "go_statement":
            return ("in a new goroutine",)
        if child.type in _INLINE_FUNCTIONS:
            return (self._callback_text(child),)
        return ()

    def _field_context(self, parent, child, index: int) -> tuple[str, ...]:
        src, ptype = self.src, parent.type
        fname = parent.field_name_for_child(index)
        if ptype in _IF:
            cond = parent.child_by_field_name("condition")
            cond_text = _short(_strip_parens(_text(cond, src)), 80) if cond is not None else "?"
            if fname == "consequence":
                return (cond_text,)
            if fname == "alternative":
                negs = [_negate(cond_text)]
                for sib in parent.children[:index]:  # Python: earlier `elif` conditions are false too
                    if sib.type == "elif_clause":
                        c = sib.child_by_field_name("condition")
                        if c is not None:
                            negs.append(_negate(_short(_strip_parens(_text(c, src)), 80)))
                return tuple(negs)
            return ()
        if ptype in _LOOPS and fname == "body":
            return (self._loop_text(parent),)
        if ptype in _WITH and fname == "body":
            head = _text(parent, src)[: (child.start_byte - parent.start_byte)]
            return (_short("within " + head.rstrip(" :{\n"), 80),)
        if ptype == "try_statement" and child.type == "else_clause":
            return ("no exception was raised",)
        return ()

    def _loop_text(self, loop) -> str:
        src = self.src
        left = loop.child_by_field_name("left") or loop.child_by_field_name("name")
        right = loop.child_by_field_name("right") or loop.child_by_field_name("value")
        rng = next((c for c in loop.named_children if c.type == "range_clause"), None)
        if rng is not None:
            left, right = rng.child_by_field_name("left"), rng.child_by_field_name("right")
        if left is not None and right is not None:
            return _short(f"for each {_text(left, src)} in {_text(right, src)}", 80)
        cond = loop.child_by_field_name("condition")
        if cond is None:
            clause = next((c for c in loop.named_children if c.type == "for_clause"), None)
            cond = clause.child_by_field_name("condition") if clause is not None else None
        if cond is None and self.lang == LanguageID.GO:  # `for cond { ... }`: the condition has no field name
            cond = next((c for c in loop.named_children if c.type not in ("block", "for_clause", "range_clause",
                                                                          "comment")), None)
        if cond is not None:
            return _short(f"while {_strip_parens(_text(cond, src))}", 80)
        return "in an endless loop" if self.lang == LanguageID.GO else "in a loop"

    def _case_text(self, parent, case) -> str:
        switch = parent
        if switch.type in ("switch_body", "switch_block", "block") and switch.parent is not None:
            switch = switch.parent
        subject = None
        for f in _SWITCH_SUBJECT_FIELDS:
            subject = switch.child_by_field_name(f)
            if subject is not None:
                break
        if subject is None and switch.type == "match_statement":
            subject = next((c for c in switch.named_children if c.type != "block"), None)
        labels = self._case_labels(case)
        if subject is None:  # `switch { case a > b: }`: each case is a condition
            return "no other case holds" if labels is None else _short(" or ".join(labels), 80)
        subj = _short(_strip_parens(_text(subject, self.src)), 40)
        if labels is None:
            return f"{subj} matches no other case"
        return _short(f"{subj} matches {' or '.join(labels)}", 80)

    def _case_labels(self, case) -> list[str] | None:
        """The values a case matches (fall-through cases above it included); None for `default` / `_`."""
        src, t = self.src, case.type
        if t in ("switch_default", "default_case"):
            return None
        if t in ("switch_block_statement_group", "switch_rule", "switch_section"):  # Java / C#: several labels
            heads = [c for c in case.named_children if not c.type.endswith("_statement") and c.type not in ("block", "comment")
                     and not c.type.endswith("_declaration")]
        elif t == "case_clause":  # Python `match`
            heads = [c for c in case.named_children if c.type == "case_pattern"]
        else:  # JS `switch_case`, Go `expression_case` / `type_case`
            value = case.child_by_field_name("value") or case.child_by_field_name("type")
            heads = [value] if value is not None else []
        prev = case.prev_named_sibling
        while prev is not None and prev.type == t and not self._case_body(prev):  # empty cases fall through
            more = self._case_labels_only(prev)
            if more is None:
                return None
            heads = more + heads
            prev = prev.prev_named_sibling
        labels = []
        for h in heads:
            if isinstance(h, str):
                labels.append(h)
                continue
            text = re.sub(r"^case\s+", "", _text(h, src).strip()).rstrip(":").strip()
            if text in ("default", "_") or h.type in ("default_switch_label",):
                return None
            labels.append(_short(text, 40))
        if not labels:  # an unknown case shape: its first line
            labels = [re.split(r":(?!:)|->|=>|\{", _text(case, src).split("\n", 1)[0], maxsplit=1)[0]
                      .removeprefix("case").strip()]
        return labels

    def _case_body(self, case) -> list:
        heads = {c.id for c in (case.child_by_field_name("value"), case.child_by_field_name("type")) if c is not None}
        return [c for c in case.named_children if c.id not in heads and c.type not in ("comment", "switch_label",
                "case_pattern", "constant_pattern", "case_switch_label", "default_switch_label", "expression_list")
                and not c.type.endswith("_pattern")]

    def _case_labels_only(self, case) -> list[str] | None:
        if case.type in ("switch_default", "default_case"):
            return None
        value = case.child_by_field_name("value") or case.child_by_field_name("type")
        heads = [value] if value is not None else [c for c in case.named_children if c.type != "comment"]
        texts = []
        for h in heads:
            text = re.sub(r"^case\s+", "", _text(h, self.src).strip()).rstrip(":").strip()
            if text in ("default", "_"):
                return None
            texts.append(_short(text, 40))
        return texts

    def _catch_type(self, clause) -> str:
        src = self.src
        if clause.type == "except_clause":
            head = _text(clause, src).split(":", 1)[0].removeprefix("except").strip()
            head = re.sub(r"\s+as\s+\w+$", "", head).lstrip("*").strip()
            return _short(head or "any exception", 60)
        for c in clause.named_children:
            if c.type == "catch_formal_parameter":
                t = next((x for x in c.named_children if x.type == "catch_type"), None)
                return _short(_text(t, src) if t is not None else _text(c, src), 60)
            if c.type == "catch_declaration":
                t = c.child_by_field_name("type")
                return _short(_text(t, src) if t is not None else _text(c, src), 60)
        param = clause.child_by_field_name("parameter")
        if param is not None:
            ann = clause.child_by_field_name("type")
            return f"any error ({_text(param, src)}" + (f": {_text(ann, src)}" if ann is not None else "") + ")"
        return "any exception"

    def _callback_text(self, fn) -> str:
        parent = fn.parent
        if parent is not None and parent.type == "argument":  # C#: `Throws(() => { ... })`
            fn, parent = parent, parent.parent
        if parent is not None and parent.type in _ARGUMENT_LISTS and parent.parent is not None:
            return _short(f"in a callback passed to {_elide_args(_callee_text(parent.parent, self.src, self.lang))}", 80)
        own = fn.child_by_field_name("name")
        if own is not None:
            return _short(f"in inline function {_text(own, self.src)}", 80)
        while parent is not None and parent.type in ("ternary_expression", "conditional_expression",
                                                     "parenthesized_expression", "as_expression", "await_expression"):
            parent = parent.parent
        if parent is not None and parent.type in ("variable_declarator", "assignment", "assignment_expression", "pair",
                                                  "public_field_definition", "field_definition"):
            name = parent.child_by_field_name("name") or parent.child_by_field_name("left") or \
                parent.child_by_field_name("key") or parent.child_by_field_name("property")
            if name is not None:
                return _short(f"in inline function {_text(name, self.src)}", 80)
        return "in an inline function"

    # -- the walk ------------------------------------------------------------ #
    def run(self) -> None:
        body = _body(self.node)
        if body is None:
            return
        if body.type not in _BLOCKS \
                and body.type not in self.nested_skip and not body.type.endswith("_statement"):
            # expression body: `x => x * 2`, `lambda: ...`
            self.add("return", _expr(body, self.src), body, ())
        self._collect_local_dicts(body)
        self._visit(body, ())
        self._walk(body, (), 0)
        self.nested_facts()

    def _walk(self, node, ctx: tuple[str, ...], depth: int, scope: tuple[int, ...] = ()) -> None:
        if depth >= _MAX_DEPTH:
            return
        for i, child in enumerate(node.children):
            if not child.is_named:
                continue
            self.scope = scope
            if child.type in self.nested_skip:
                self._define(child, ctx)
                continue
            if child.type in _INLINE_FUNCTIONS:
                self.inline_params.update(_params(child, self.src))
            at, own = self._context(node, child, i)
            at_scope, own_scope = (node.id,) * len(at), (child.id,) * len(own)
            self.scope = scope + at_scope
            self._visit(child, ctx + at)
            self._walk(child, ctx + at + own, depth + 1, scope + at_scope + own_scope)

    def _define(self, node, ctx) -> None:
        target = node
        if node.type in DECORATED_WRAPPER_TYPES.get(self.lang, set()):
            target = node.child_by_field_name("definition") or node
        name = target.child_by_field_name("name")
        qname = self.indexed.get(target.start_byte)
        kind = "class" if target.type in CLASS_NODE_TYPES.get(self.lang, set()) else \
            "method" if self.node.type in CLASS_NODE_TYPES.get(self.lang, set()) else "function"
        label = _text(name, self.src) if name is not None else "(anonymous)"
        self.add("define", f"defines nested {kind} {label}", target, ctx, targets=(qname,) if qname else ())

    def _visit(self, node, ctx) -> None:
        t, src = node.type, self.src
        if t in _RETURN:
            value = next((c for c in node.named_children if c.type != "comment"), None)
            self.add("return", _expr(value, src) if value is not None else "nothing", node, ctx)
        elif t in _YIELD:
            value = next((c for c in node.named_children if c.type != "comment"), None)
            self.add("yield", _expr(value, src) if value is not None else "nothing", node, ctx)
        elif t in _THROW:
            value = next((c for c in node.named_children if c.type != "comment"), None)
            keyword = "raise" if t == "raise_statement" else "throw"
            self.add("raise", f"{keyword} {_expr(value, src)}" if value is not None
                     else f"{keyword} (re-raises the current exception)", node, ctx)
        elif t in _ASSERT:
            self.add("raise", f"assert {_expr(node, src).removeprefix('assert').strip()}", node, ctx)
        elif t in _CATCH:
            self.add("catch", f"catches {self._catch_type(node)}: {self._handling(node)}", node, ctx)
        elif t in _ASSIGN:
            self._assignment(node, ctx)
        elif t in _DECLARE:
            name = node.child_by_field_name("name") or node.child_by_field_name("left")
            value = node.child_by_field_name("value") or node.child_by_field_name("right") or next(
                (c for c in reversed(node.named_children) if name is None or c.id != name.id), None)
            if name is not None and value is not None:
                self.add("assign", f"{_short(_text(name, src), 40)} = {_expr(value, src)}", node, ctx)
        elif t in _INCDEC and (_text(node, src).strip()[-2:] in ("++", "--") or _text(node, src).strip()[:2] in ("++", "--")):
            operand = node.child_by_field_name("argument") or node.child_by_field_name("operand") or \
                (node.named_children[0] if node.named_children else None)
            if not self._assigned_by_ancestor(node, operand):
                self._write_target(operand, _expr(node, src), node, ctx, local_ok=False)
        elif t in _DELETE:
            for target in node.named_children:
                self._write_target(target, f"deletes {_expr(target, src)}", node, ctx, local_ok=False)
        elif t in ("continue_statement", "break_statement") and ctx and not self._ends_case(node):
            self.add("flow", "skip to the next iteration" if t == "continue_statement" else "leave the loop", node, ctx)
        elif t in _GLOBAL:
            self.globals.update(_text(c, src) for c in node.named_children if c.type == "identifier")
        elif t == self.call_type or t in _NEW:
            self._call(node, ctx)
        elif t == "type_conversion_expression" and self.lang == LanguageID.GO:
            self._go_index_call(node, ctx)
        elif t in _ATTRIBUTES or t in _SUBSCRIPTS:
            self._read(node, ctx)

    def _assigned_by_ancestor(self, node, operand) -> bool:
        """`this.i = ++this.i % n`: the enclosing assignment already states the write to the same target."""
        text = _text(operand, self.src) if operand is not None else None
        parent = node.parent
        while parent is not None and parent.id != self.node.id and not parent.type.endswith("_statement"):
            if parent.type in _ASSIGN:
                left = parent.child_by_field_name("left")
                if left is not None and _text(left, self.src) == text:
                    return True
            parent = parent.parent
        return False

    def _ends_case(self, node) -> bool:
        """`break` that only ends a switch case (the nearest enclosing construct is a switch, not a loop)."""
        if node.type != "break_statement":
            return False
        parent = node.parent
        while parent is not None and parent.id != self.node.id:
            if parent.type in _LOOPS:
                return False
            if "switch" in parent.type or parent.type in ("select_statement", "match_statement"):
                return True
            parent = parent.parent
        return False

    def _handling(self, clause) -> str:
        kinds = set()
        stack = list(clause.named_children)
        depth = 0
        while stack and depth < 2000:
            depth += 1
            n = stack.pop()
            if n.type in self.nested_skip:
                continue
            if n.type in _THROW:
                kinds.add("raises")
                continue
            elif n.type in _RETURN:
                kinds.add("returns")
            elif n.type in ("continue_statement", "break_statement"):
                kinds.add("continues" if n.type == "continue_statement" else "stops the loop")
            elif n.type == self.call_type:
                kinds.add("calls")
            elif n.type in _ASSIGN or n.type in _DECLARE:
                kinds.add("assigns")
            stack.extend(n.named_children)
        body = clause.child_by_field_name("body") or next(
            (c for c in clause.named_children if c.type in ("block", "statement_block")), None)
        statements = [c for c in (body.named_children if body is not None else []) if c.type != "comment"]
        if not kinds and all(c.type in ("pass_statement", "empty_statement") for c in statements):
            return "ignores the error"
        order = ["raises", "returns", "continues", "stops the loop", "calls", "assigns"]
        return ", ".join(k for k in order if k in kinds) or "handles the error"

    # -- writes ---------------------------------------------------------------- #
    def _assignment(self, node, ctx) -> None:
        src = self.src
        if node.parent is not None and node.parent.type in ("initializer_expression", "object_initializer"):
            return  # `new Settings { MaxDepth = 1 }`: part of the creation expression shown by its statement
        left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
        if left is None:
            return
        op = "="
        operator = node.child_by_field_name("operator")
        if operator is not None:
            op = _text(operator, src)
        elif node.type in ("augmented_assignment", "augmented_assignment_expression"):
            m = re.search(r"\s([-+*/%&|^<>@]{1,3}=)\s", _text(node, src))
            op = m.group(1) if m else op
        value = _expr(right, src) if right is not None else ""
        targets = left.named_children if left.type in ("expression_list", "pattern_list", "tuple_pattern") else [left]
        wrote_state = False
        for target in targets:
            wrote_state |= self._write_target(target, f"{_short(_text(target, src), 50)} {op} {value}", node, ctx)
        if not wrote_state:
            self.add("assign", f"{_short(_text(left, src), 50)} {op} {value}", node, ctx)

    def _write_target(self, target, text: str, node, ctx, local_ok: bool = True) -> bool:
        """Add a `state` fact when `target` is outside the function's locals; True if added."""
        if target is None:
            return False
        subscript = target.type in _SUBSCRIPTS
        base = target
        if subscript:
            base = target.child_by_field_name("value") or target.child_by_field_name("object") or \
                target.child_by_field_name("operand") or target.child_by_field_name("expression") or \
                (target.named_children[0] if target.named_children else None)
        if base is None:
            return False
        segments = flatten_reference_chain(base, self.src, self.lang) if base.type in _ATTRIBUTES or \
            base.type in ("identifier", "this", "this_expression") else None
        if segments is None and re.fullmatch(r"[\w$]+(\.[\w$]+)*", _text(base, self.src)):
            segments = _text(base, self.src).split(".")
        if not segments:
            return False
        if not subscript and len(segments) < 2:
            if segments[0] in self.globals:
                self.add("state", f"{text} (global {segments[0]})", node, ctx)
                return True
            if not local_ok:
                owner = self._owner(segments[0])
                if owner:
                    self.add("state", f"{text} ({owner})", node, ctx)
                    return True
            return False
        owner = self._owner(segments[0])
        if owner is None:
            return False
        self.add("state", f"{text} ({owner})", node, ctx)
        return True

    def _resolve_import(self, name: str) -> str | None:
        """The import a local name refers to. Go also names `gopkg.in/yaml.v3` and `example.com/m/v2` by the
        package itself (`yaml`, `m`): the version element is not part of the name."""
        if self.import_map is None:
            return None
        found = self.import_map.resolve(name)
        if found or self.lang != LanguageID.GO:
            return found
        for key, target in getattr(self.import_map, "aliases", {}).items():
            if re.sub(r"\.v\d+$", "", key) == name:
                return target
            if re.fullmatch(r"v\d+", key) and target.rsplit(".", 2)[-2:-1] == [name]:
                return target
        return None

    def _imported_value(self, receiver: list[str]) -> bool:
        """Whether an imported receiver chain names a value (a module variable or class attribute of this
        repository) rather than a module, class or function - only a value can be changed in place."""
        resolved = self._resolve_import(receiver[0]) if self.import_map is not None else None
        if not resolved:
            return False
        full = ".".join([resolved, *receiver[1:]])
        sym = self.b.symbol_table.get(full)
        if sym is not None:
            return sym.kind == "attribute"
        modules = self.modules()
        return full not in modules and full.rsplit(".", 1)[0] in modules

    def _owner(self, root: str) -> str | None:
        if root in self.globals:
            return f"global {root}"
        if root in self.self_names:
            return "own state" if root != "cls" else "class state"
        if root in self.params:
            return f"argument {root}"
        if self.import_map is not None and self._resolve_import(root):
            return f"imported {root}"
        if root.isupper() and len(root) > 1:
            return f"module constant {root}"
        return None

    # -- reads ----------------------------------------------------------------- #
    def _read(self, node, ctx) -> None:
        parent = node.parent
        if parent is not None and (parent.type in _ATTRIBUTES or parent.type in _SUBSCRIPTS) and \
                parent.child_by_field_name("object") == node:
            return  # only the outermost chain
        src = self.src
        if node.type in _ATTRIBUTES:
            seg = flatten_reference_chain(node, src, self.lang) or []
            if parent is not None and parent.type == self.call_type and parent.child_by_field_name("function") == node:
                return
            if len(seg) >= 2 and seg[0] in _SETTINGS_ROOTS and seg[1][:1].isupper():
                self.add("read", f"setting {'.'.join(seg[1:3])}", node, ctx)
            elif len(seg) >= 3 and seg[0] == "process" and seg[1] == "env":
                self.add("read", f"environment {seg[2]}", node, ctx)
            return
        obj = node.child_by_field_name("value") or node.child_by_field_name("object") or node.child_by_field_name("operand")
        key = node.child_by_field_name("subscript") or node.child_by_field_name("index")
        if obj is None or key is None or key.type not in ("string", "interpreted_string_literal", "string_literal"):
            return
        holder = _text(obj, src).split(".")[-1]
        key_text = _text(key, src).strip("\"'`")
        if holder == "environ" or holder == "env":
            self.add("read", f"environment {key_text}", node, ctx)
        elif holder in _HEADER_OBJECTS:
            self.add("read", f"{holder} {key_text}", node, ctx)
        elif holder in _SETTINGS_ROOTS:
            self.add("read", f"setting {key_text}", node, ctx)

    # -- calls ----------------------------------------------------------------- #
    def _collect_local_dicts(self, body) -> None:
        """`name = {"k": handler, ...}` inside the body: candidate targets for `name[k]()`."""
        stack, seen = [body], 0
        while stack and seen < 20000:
            seen += 1
            n = stack.pop()
            if n.type in self.nested_skip:
                continue
            if n.type in _ASSIGN or n.type in _DECLARE:
                name = n.child_by_field_name("left") or n.child_by_field_name("name")
                value = n.child_by_field_name("right") or n.child_by_field_name("value")
                if name is not None:
                    for c in [name, *name.named_children]:
                        if c.type == "identifier":
                            self.local_names.add(_text(c, self.src))
                            if value is not None:
                                self.local_values.setdefault(_text(c, self.src), value.type)
                if name is not None and value is not None and value.type in ("dictionary", "object"):
                    refs = []
                    for pair in value.named_children:
                        v = pair.child_by_field_name("value")
                        if v is not None and (v.type in _ATTRIBUTES or v.type == "identifier"):
                            refs.append(_text(v, self.src))
                    if refs:
                        self.local_dicts[_text(name, self.src)] = refs
            stack.extend(n.named_children)

    def _resolve(self, leaf: str) -> tuple[str, tuple[str, ...]]:
        found = self.targets.get(leaf)
        if not found:
            return "", ()
        names = tuple(dict.fromkeys(t for t, _ in found))
        if len(names) == 1 and not any(tent for _, tent in found):
            return CERTAIN, names
        return POSSIBLE, names

    def _call(self, call, ctx) -> None:
        src, lang = self.src, self.lang
        segments, leaf = _leaf(call, src, lang)
        callee = _callee_text(call, src, lang)
        is_new = call.type in _NEW
        text = ("constructs " if is_new else "") + callee + "(" + self._args_text(call) + ")"
        if call.parent is not None and call.parent.type in ("await", "await_expression"):
            text = "await " + text
        root = segments[0] if segments else None

        # abnormal exits
        if leaf in _EXIT_CALLS and (root in _EXIT_ROOTS or (segments and len(segments) == 1)) and not is_new:
            if not (leaf == "exit" and root not in ("sys", "os", "process", "System", "Environment")):
                self.add("raise", f"{text} ({_EXIT_CALLS[leaf]})", call, ctx)
                return
        # environment reads
        if leaf in _GETENV or (segments and len(segments) >= 2 and segments[-2] in ("environ", "env") and leaf in ("get", "pop")):
            key = self._first_literal(call)
            if key:
                self.add("read", f"environment {key}", call, ctx)
        elif segments and len(segments) >= 2 and segments[-2] in _HEADER_OBJECTS and leaf in ("get", "getlist", "pop"):
            key = self._first_literal(call)
            if key:
                self.add("read", f"{segments[-2]} {key}", call, ctx)

        dynamic = self._dynamic(call, segments, leaf, ctx)
        if dynamic:
            return
        certainty, targets = self._resolve(leaf) if leaf else ("", ())
        if certainty and not is_new:
            certainty, targets = self._check_receiver(segments, leaf, certainty, targets)
        if not certainty:
            certainty, targets = self._unresolved_target(segments, leaf, is_new)
            if call.parent is not None and call.parent.type in _THROW:
                return  # `raise ValueError(...)`: the raise fact already states it
        root = self._embedding_root(call)
        if root is not None:
            # inside a return / assignment / raise / another call: its text is already shown there,
            # so only a resolved index target adds anything - collected into one `calls` fact per statement
            index = tuple(t for t in targets if t in self.b.symbol_table)
            if certainty != UNKNOWN and (index or certainty == RUNTIME):
                group = self.nested.setdefault(root.id, [root, tuple(ctx), {}, self.scope])
                group[2].setdefault(leaf or callee, (certainty, index or targets))
            return
        if certainty == UNKNOWN or certainty == POSSIBLE:
            observed = self._observed(leaf, targets)
            if observed:
                certainty, targets = RUNTIME, observed
        # state change through a mutating method
        if segments and len(segments) >= 2 and leaf in MUTATING_METHODS and not is_new:
            owner = self._owner(segments[0])
            if owner is not None and owner.startswith("imported ") and not self._imported_value(segments[:-1]):
                owner = None  # `os.remove(p)` calls a module function; it does not change `os`
            if owner is not None:
                receiver = ".".join(segments[:-1])
                self.add("state", f"{receiver}.{leaf}({self._args_text(call)}) changes {receiver} ({owner})", call, ctx)
                if certainty == UNKNOWN:
                    return  # the state fact already shows the call
        self._merge_call(text, call, ctx, certainty, targets)

    def _go_index_call(self, node, ctx) -> None:
        """Go's grammar reads `handlers[i](c)` as a conversion to a generic type: when the "type" is
        an indexed value of a local, parameter or receiver (not an imported package), it is a call of
        the function stored at that index."""
        gtype = node.child_by_field_name("type")
        if gtype is None or gtype.type != "generic_type":
            return
        base = gtype.child_by_field_name("type")
        text = _text(base, self.src) if base is not None else ""
        root = text.split(".", 1)[0]
        if self.import_map is not None and self._resolve_import(root) and root not in self.params:
            return
        if root in self.params or root in self.self_names or (base is not None and base.type == "qualified_type"):
            self._dyn(f"calls the entry of {text} selected by index", node, ctx, UNKNOWN, (), _raw_line(node, self.src))

    def _embedding_root(self, call):
        """The outermost return / assignment / raise / call around `call` within its statement, or None."""
        node, root, prev = call.parent, None, call
        while node is not None and node.id != self.node.id:
            t = node.type
            if t in _IF or t in _LOOPS or "switch" in t:
                index = next(i for i, c in enumerate(node.children) if c.id == prev.id)
                in_head = node.field_name_for_child(index) not in _BRANCH_FIELDS and prev.type not in _BLOCKS \
                    and prev.type not in _CASES and prev.type not in ("else_clause", "elif_clause")
                return prev if in_head else root  # a head call is shown in the `when` line of its branch / loop
            if t in _BLOCKS or t in _INLINE_FUNCTIONS or t in _CASES or t in _CATCH or \
                    (t.endswith("_statement") and t not in _EMBEDDING):
                break
            if t in _EMBEDDING or t == self.call_type or t in _NEW:
                root = node
            prev, node = node, node.parent
        return root

    def nested_facts(self) -> None:
        for root, ctx, calls, scope in self.nested.values():
            parts, cert, all_targets = [], CERTAIN, []
            for name, (certainty, targets) in calls.items():
                shown = "|".join(targets[:3]) + ("|..." if len(targets) > 3 else "")
                if certainty == POSSIBLE:
                    cert = POSSIBLE
                    shown = f"one of {shown}" if len(targets) > 1 else f"{shown}?"
                elif certainty == RUNTIME:
                    shown += " (seen at runtime)"
                parts.append(f"{name}() -> {shown}")
                all_targets.extend(targets)
            self.facts.append(Fact("calls", "; ".join(parts), _span(root), cert, ctx, tuple(dict.fromkeys(all_targets)),
                                   scope=tuple(scope) if len(scope) == len(ctx) else ()))

    def _merge_call(self, text, call, ctx, certainty, targets) -> None:
        for i in range(len(self.facts) - 1, max(-1, len(self.facts) - 400), -1):
            f = self.facts[i]
            if f.kind == "call" and f.text == text and f.when == tuple(ctx) and f.certainty == certainty and \
                    (not f.scope or f.scope == tuple(self.scope)):
                self.facts[i] = Fact(f.kind, f.text, f.lines, f.certainty, f.when, f.targets, f.raw, f.count + 1,
                                     scope=f.scope)
                return
        self.add("call", text, call, ctx, certainty, targets)

    def _args_text(self, call) -> str:
        args = call.child_by_field_name("arguments")
        if args is None:
            return ""
        inner = _text(args, self.src).strip()
        if inner[:1] in "([" and inner[-1:] in ")]":
            inner = inner[1:-1]
        return _short(inner, 40)

    def _first_literal(self, call) -> str | None:
        args = call.child_by_field_name("arguments")
        first = next((a for a in args.named_children if a.type != "comment"), None) if args is not None else None
        if first is not None and first.type in ("string", "interpreted_string_literal", "string_literal",
                                                 "raw_string_literal", "template_string"):
            return _text(first, self.src).strip("\"'`")
        return None

    def _outside_label(self, target: str) -> str:
        """`library X` for a name outside the repository; `unindexed X` for a member of an object the repository
        defines (a module-level value, an instance attribute) that the index does not list as a symbol."""
        parts = target.split(".")
        modules = self.modules()
        if any(".".join(parts[:i]) in modules for i in range(1, len(parts))):
            return f"unindexed {target}"
        return f"library {target}"

    def _check_receiver(self, segments, leaf: str, certainty: str, targets) -> tuple[str, tuple[str, ...]]:
        """The graph resolves some method calls by name alone. A target stays certain only when the receiver
        is self / this, a bare function, an imported name or a class, or a variable whose declared type
        owns the method; otherwise it becomes possible."""
        if not segments or len(segments) == 1:
            return certainty, targets
        root = segments[0]
        if len(segments) == 2 and (root in self.self_names or root in self.classes()):
            return certainty, targets
        if root in self.aliases:
            aliased = self._external_static(segments)
            if aliased is not None:
                return aliased
        resolved = self._resolve_import(root) if self.import_map is not None and root not in self.types else None
        if resolved:
            full = ".".join([resolved, *segments[1:]])
            if full not in self.b.symbol_table and not any(t.endswith(full) for t in targets) and \
                    self._outside_label(full).startswith("library "):
                return CERTAIN, (f"library {full}",)  # `yaml.Marshal(v)`: the import says which module it is
            return certainty, targets
        typed = self._typed_target(segments, leaf, targets)
        if typed is not None:
            return typed
        external = self._external_static(segments)
        if external is not None:
            return external
        return (POSSIBLE if certainty == CERTAIN else certainty), targets

    def _external_static(self, segments) -> tuple[str, tuple[str, ...]] | None:
        """Java / C#: `BigInteger.Parse(s)`, `System.Convert.ToInt64(v)` - a static call on a type that is not
        in the index (the capitalised receiver is not a variable, a member or an index class)."""
        if self.lang not in (LanguageID.JAVA, LanguageID.CSHARP) or not segments or len(segments) < 2:
            return None
        root = segments[0]
        if root in self.aliases:  # C# `using Assert = Tests.XUnitAssert;`
            classes = self._closest_classes(self.aliases[root].rsplit(".", 1)[-1])
            methods = tuple(dict.fromkeys(m for c in classes for m in self._methods_of(c, segments[-1])))
            return ((CERTAIN if len(methods) == 1 else POSSIBLE), methods) if methods and len(segments) == 2 else None
        if not root[:1].isupper() or root in self.types or root in self.params or root in self.local_names \
                or root in self.classes() or root in self.self_names or root in self.leaves():
            return None
        if self.lang == LanguageID.CSHARP and self._declares_member(root):
            return None  # C# properties are capitalised too
        return CERTAIN, (f"method of {'.'.join(segments[:-1])}, a type outside the index",)

    def _declares_member(self, name: str) -> bool:
        node = self.node.parent
        while node is not None and node.type not in CLASS_NODE_TYPES.get(self.lang, set()):
            node = node.parent
        text = _text(node, self.src) if node is not None else ""
        return re.search(rf"\b{re.escape(name)}\s*(\{{|=|;|=>)", text) is not None

    def _typed_target(self, segments, leaf: str, graph_targets=()) -> tuple[str, tuple[str, ...]] | None:
        """The target named by the receiver's declared type (`JsonWriter out` -> `JsonWriter.name`), or None."""
        recv_type = self.types.get(segments[0]) if segments and len(segments) == 2 else None
        if not recv_type:
            return None
        classes = self._closest_classes(recv_type, self.qualified_types.get(segments[0]))
        if not classes:
            return CERTAIN, (f"method of {recv_type}, a type outside the index",)
        methods = tuple(dict.fromkeys(m for c in classes for m in self._methods_of(c, leaf)))
        if methods:
            return (CERTAIN if len(methods) == 1 else POSSIBLE), methods
        if graph_targets:  # inherited along an edge the index does not have: the graph's name matches remain possible
            return POSSIBLE, tuple(graph_targets)
        return UNKNOWN, (f"{recv_type} has no {leaf} in the index (inherited from outside it?)",)

    def _closest_classes(self, name: str, qualified: str | None = None) -> list[str]:
        """Index classes a type name can mean here: same language, then the imported one, then the same package.
        A fully qualified name (`com.fasterxml.jackson.core.JsonParser`) matches only a class at that path."""
        table = self.b.symbol_table
        found = [c for c in self.classes().get(name, ()) if str(getattr(table.get(c), "language_id", self.lang)) == str(self.lang)]
        if qualified:
            return [c for c in found if c == qualified or c.endswith("." + qualified)]
        if len(found) > 1 and self.import_map is not None:
            resolved = self._resolve_import(name)
            if resolved in found:
                return [resolved]
        if len(found) > 1 and self.sym is not None:
            package = self.sym.module.rsplit(".", 1)[0]
            near = [c for c in found if c.startswith(package + ".")]
            found = near or found
        return found

    def _methods_of(self, cls: str, name: str, depth: int = 0) -> list[str]:
        table, graph = self.b.symbol_table, self.b.graph
        if f"{cls}.{name}" in table:
            return [f"{cls}.{name}"]
        if depth >= 6 or cls not in graph:
            return []
        found: list[str] = []
        for _u, base, d in graph.out_edges(cls, data=True):
            if d.get("relation") in ("EXTENDS", "IMPLEMENTS") and not found:
                found = self._methods_of(base, name, depth + 1)
        return found

    def _unresolved_target(self, segments, leaf: str, is_new: bool) -> tuple[str, tuple[str, ...]]:
        lang = self.lang
        typed = self._typed_target(segments, leaf) if not is_new else None
        if typed is not None:
            return typed
        if not segments:
            return UNKNOWN, ("receiver is a computed value",) if leaf else ()
        root = segments[0]
        if len(segments) == 1:
            if (lang == LanguageID.PYTHON and root in _PY_BUILTINS) or (lang in _JS and root in _JS_GLOBALS) \
                    or (lang == LanguageID.GO and root in _GO_BUILTINS):
                return CERTAIN, (f"builtin {root}",)
        if (lang == LanguageID.JAVA and root in _JAVA_STD) or (lang == LanguageID.CSHARP and root in _CS_STD) \
                or (lang in _JS and root in _JS_GLOBALS):
            return CERTAIN, (f"standard library {'.'.join(segments)}",)
        resolved = self._resolve_import(root) if self.import_map is not None else None
        if resolved:
            rest = ".".join(segments[1:])
            full = f"{resolved}{'.' + rest if rest else ''}"
            return CERTAIN, (full if full in self.b.symbol_table else self._outside_label(full),)
        external = self._external_static(segments) if not is_new else None
        if external is not None:
            return external
        if root in self.self_names:
            return UNKNOWN, ("own method not found in the index",) if len(segments) == 2 else ("method on own attribute, type not resolved",)
        if root in self.params:
            return UNKNOWN, (f"method on argument {root}, type not resolved",)
        if len(segments) == 1:
            return UNKNOWN, ("name not found in the index",)
        return UNKNOWN, (f"method on {root}, type not resolved",)

    def _observed(self, leaf: str, candidates: tuple[str, ...]) -> tuple[str, ...]:
        if not self.runtime:
            return ()
        cands = {c for c in candidates if "." in c and " " not in c}
        hits = sorted(self.runtime & cands) if cands else []
        if not hits and leaf:
            hits = sorted(c for c in self.runtime if c.rsplit(".", 1)[-1] == leaf)
        return tuple(hits)

    def _dynamic(self, call, segments, leaf: str, ctx) -> bool:
        """Add a `dynamic` fact when the call's target is chosen at runtime; True if added."""
        src, lang = self.src, self.lang
        raw = _raw_line(call, src)
        if lang == LanguageID.PYTHON and segments == ["__import__"]:
            literal = self._first_literal(call)
            self._dyn(f"imports module {literal!r} at runtime" if literal else "imports a module named at runtime",
                      call, ctx, CERTAIN if literal else UNKNOWN, (literal,) if literal else (), None if literal else raw)
            return True
        hazard = detect_call_site_hazard(call, lang, src, self.parsed.path) if call.type == self.call_type else None
        func = call.child_by_field_name("function")
        if hazard is None and func is not None and func.type in ("subscript_expression", "element_access_expression"):
            hazard_type, target_object = "SUBSCRIPT_DISPATCH", _text(func.child_by_field_name("object") or func.named_children[0], src)
        elif hazard is not None:
            hazard_type, target_object = hazard.hazard_type, hazard.target_object
        else:
            hazard_type, target_object = None, None

        if hazard_type == "EVAL":
            self._dyn("evaluates code built at runtime", call, ctx, UNKNOWN, (), raw)
            return True
        if hazard_type == "REFLECTION":
            name_arg = self._arg(call, 1)
            literal = self._literal_text(name_arg)
            action = {"getattr": "looks up", "setattr": "sets", "delattr": "deletes"}.get(leaf, "reflects on")
            obj = target_object or "an object"
            if literal is not None:
                certainty, targets = self._resolve_member(target_object, literal)
                self._dyn(f"{action} attribute {literal!r} of {obj} by name", call, ctx, certainty, targets,
                          None if certainty == CERTAIN else raw)
            else:
                prefix = self._literal_prefix(name_arg)
                targets = self._prefixed_members(target_object, prefix) if prefix else ()
                certainty = POSSIBLE if targets else UNKNOWN
                what = f"an attribute of {obj} named at runtime" + (f" (starts with {prefix!r})" if prefix else "")
                self._dyn(f"{action} {what}", call, ctx, certainty, targets, raw)
            if leaf == "getattr":
                self._bind_dynamic(call, certainty, targets)
            if leaf == "setattr":
                owner = self._owner(target_object.split(".")[0]) if target_object else None
                if owner:
                    self.add("state", f"sets an attribute of {target_object} by name ({owner})", call, ctx)
            return True
        if hazard_type == "SUBSCRIPT_DISPATCH":
            base = (target_object or "").split("[")[0]
            cands = tuple(self.local_dicts.get(base, ()))
            resolved = tuple(self._resolve_name(self.qname, self.sym, self.parsed, c.split(".")) or c for c in cands)
            self._dyn(f"calls the entry of {base or 'a table'} selected by key", call, ctx,
                      POSSIBLE if resolved else UNKNOWN, resolved, raw)
            return True
        if self._reflection(call, segments, leaf):
            self._dyn(f"uses reflection: {'.'.join(segments) if segments else _callee_text(call, src, lang)}",
                      call, ctx, UNKNOWN, (), raw)
            return True
        if not segments:
            return False
        if lang == LanguageID.PYTHON and tuple(segments) in _PY_DYNAMIC_IMPORTS or \
                (lang in _JS and segments == ["require"] and self._first_literal(call) is None) or \
                (lang in _JS and call.type == "call_expression" and func is not None and func.type == "import"):
            literal = self._first_literal(call)
            self._dyn(f"imports module {literal!r} at runtime" if literal else "imports a module named at runtime",
                      call, ctx, CERTAIN if literal else UNKNOWN, (literal,) if literal else (), None if literal else raw)
            return True
        if lang in _JS and segments[0] == "Reflect" and len(segments) == 2:
            self._dyn(f"Reflect.{leaf}: target chosen at runtime", call, ctx, UNKNOWN, (), raw)
            return True
        if lang in _JS and leaf in _JS_INDIRECT and len(segments) >= 2:
            fn_leaf = segments[-2]
            certainty, targets = self._resolve(fn_leaf)
            if segments[-2] in self.params or (len(segments) == 2 and segments[0] in self.params):
                self._callback(call, segments[-2], ctx, raw, via=f".{leaf}")
            else:
                self._dyn(f"calls {'.'.join(segments[:-1])} through .{leaf}(...)", call, ctx,
                          certainty or UNKNOWN, targets, None if certainty == CERTAIN else raw)
            return True
        if len(segments) == 1 and segments[0] in self.params and segments[0] not in self.targets:
            self._callback(call, segments[0], ctx, raw)
            return True
        if len(segments) == 1 and segments[0] in self.local_names and segments[0] not in self.params \
                and segments[0] not in self.targets and not (self.import_map and self._resolve_import(segments[0])):
            name, kind = segments[0], self.local_values.get(segments[0], "")
            if kind in _INLINE_FUNCTIONS:
                self.add("call", f"{name}({self._args_text(call)})", call, ctx, CERTAIN, (f"inline function {name}",))
            elif name in self.dynamic_bindings:
                certainty, targets = self.dynamic_bindings[name]
                self._dyn(f"calls {name}, the attribute looked up by name above", call, ctx, certainty, targets,
                          None if certainty == CERTAIN else raw)
            else:
                self._dyn(f"calls the function held in local variable {name}", call, ctx, UNKNOWN, (), raw)
            return True
        if len(segments) == 1 and segments[0] not in self.params and segments[0] not in self.local_names:
            name = segments[0]
            if name in self.inline_params:
                self._dyn(f"calls the function passed to an inline callback as {name}", call, ctx, UNKNOWN, (), raw)
                return True
            if name in self.outer_params:
                self._dyn(f"calls {name}, a function captured from the enclosing function's arguments", call, ctx,
                          UNKNOWN, (), raw)
                return True
        return False

    def _reflection(self, call, segments, leaf: str) -> bool:
        lang = self.lang
        if lang == LanguageID.GO:
            return bool(segments) and segments[0] == "reflect"
        if lang == LanguageID.JAVA and leaf in _JVM_REFLECTION:
            return segments is None or len(segments) > 1 or leaf == "forName"
        if lang == LanguageID.CSHARP and leaf in _CS_REFLECTION and (segments is None or len(segments) > 1):
            if leaf == "GetType":  # `Type.GetType(name)` looks a type up by name; `obj.GetType()` does not
                return self._arg(call, 0) is not None and self._first_literal(call) is None
            return True
        return False

    def _bind_dynamic(self, call, certainty, targets) -> None:
        parent = call.parent
        while parent is not None and parent.type in ("await", "await_expression", "parenthesized_expression"):
            parent = parent.parent
        if parent is not None and (parent.type in _ASSIGN or parent.type in _DECLARE):
            name = parent.child_by_field_name("left") or parent.child_by_field_name("name")
            if name is not None and name.type == "identifier":
                self.dynamic_bindings[_text(name, self.src)] = (certainty, tuple(targets))

    def _dyn(self, text, node, ctx, certainty, targets, raw) -> None:
        last = self.facts[-1] if self.facts else None
        if last is not None and last.kind == "dynamic" and last.raw == raw and raw and last.lines[0] == _span(node)[0] \
                and last.text.startswith("uses reflection") and text.startswith("uses reflection"):
            return  # `Class.forName(k).getMethod(m).invoke(o)`: one reflective chain, one fact
        if certainty in (UNKNOWN, POSSIBLE):
            observed = tuple(sorted(self.runtime & set(targets))) if targets else ()
            if observed:
                certainty, targets = RUNTIME, observed
        self.add("dynamic", text, node, ctx, certainty, targets, raw)

    def _callback(self, call, param: str, ctx, raw, via: str = "") -> None:
        cands = self._callback_candidates(param)
        self._dyn(f"calls the function passed as argument {param}{via}", call, ctx,
                  POSSIBLE if cands else UNKNOWN, cands, raw)

    def _callback_candidates(self, param: str) -> tuple[str, ...]:
        """Functions the index's callers pass for `param` (by position or keyword)."""
        try:
            pos = self.params.index(param)
        except ValueError:
            return ()
        if self.params and self.params[0] in ("self", "cls") and self.lang == LanguageID.PYTHON:
            pos -= 1
        leaf = self.qname.rsplit(".", 1)[-1]
        found: list[str] = []
        graph = self.b.graph
        if self.qname not in graph:
            return ()
        callers = sorted({u for u, _t, d in graph.in_edges(self.qname, data=True) if d.get("relation", "CALLS") == "CALLS"})
        for caller in callers[:40]:
            node = self.b.def_node(caller)
            sym = self.b.symbol_table.get(caller)
            parsed = self.b.parsed_file(sym.file) if sym is not None else None
            if node is None or parsed is None or not CALL_NODE_TYPE.get(parsed.language_id):
                continue
            out = {t.rsplit(".", 1)[-1]: t for _u, t, _d in graph.out_edges(caller, data=True) if not t.startswith("<")}
            stack = [node]
            steps = 0
            while stack and steps < 20000:
                steps += 1
                n = stack.pop()
                stack.extend(n.named_children)
                if n.type != CALL_NODE_TYPE[parsed.language_id]:
                    continue
                seg = call_callee_segments(n, parsed.source, parsed.language_id)
                if not seg or seg[-1] != leaf:
                    continue
                args = n.child_by_field_name("arguments")
                if args is None:
                    continue
                positional = [a for a in args.named_children if a.type not in ("comment", "keyword_argument")]
                chosen = None
                for a in args.named_children:
                    if a.type == "keyword_argument":
                        name = a.child_by_field_name("name")
                        if name is not None and _text(name, parsed.source) == param:
                            chosen = a.child_by_field_name("value")
                if chosen is None and 0 <= pos < len(positional):
                    chosen = positional[pos]
                if chosen is None:
                    continue
                ref = flatten_reference_chain(chosen, parsed.source, parsed.language_id) \
                    if chosen.type in _ATTRIBUTES or chosen.type == "identifier" else None
                if ref and ref[-1] in out:
                    found.append(out[ref[-1]])
                elif ref and (hit := self._resolve_name(caller, sym, parsed, ref)):
                    found.append(hit)
                elif chosen.type in _INLINE_FUNCTIONS:
                    found.append(f"inline function in {caller}")
        return tuple(dict.fromkeys(found))

    def _resolve_name(self, caller: str, sym, parsed, ref: list[str]) -> str | None:
        """The index symbol a caller's plain name refers to: its own module, its class, or an import."""
        table = self.b.symbol_table
        if len(ref) == 1 and f"{sym.module}.{ref[0]}" in table:
            return f"{sym.module}.{ref[0]}"
        if len(ref) == 2 and ref[0] in _SELF and f"{caller.rsplit('.', 1)[0]}.{ref[1]}" in table:
            return f"{caller.rsplit('.', 1)[0]}.{ref[1]}"
        try:
            imports = self.b._build_import_map(parsed, sym.module)
        except Exception:  # an aid only
            return None
        resolved = imports.resolve(ref[0])
        full = ".".join([resolved, *ref[1:]]) if resolved else None
        return full if full and full in table else None

    def _arg(self, call, index: int):
        args = call.child_by_field_name("arguments")
        if args is None:
            return None
        positional = [a for a in args.named_children if a.type != "comment"]
        return positional[index] if index < len(positional) else None

    def _literal_text(self, node) -> str | None:
        if node is not None and node.type in ("string", "string_literal", "interpreted_string_literal") and \
                not any(c.type == "interpolation" for c in node.named_children):
            return _text(node, self.src).strip("\"'").lstrip("rbuRBU").strip("\"'")
        return None

    def _literal_prefix(self, node) -> str | None:
        """The literal start of a name built at runtime: `"visit_" + kind`, `f"handle_{x}"`."""
        if node is None:
            return None
        if node.type in ("binary_operator", "binary_expression"):
            left = node.child_by_field_name("left")
            return self._literal_text(left) or self._literal_prefix(left)
        if node.type == "string":
            text = _text(node, self.src)
            m = re.match(r"^[rbuRBUfF]*[\"']([A-Za-z_][\w]*)\{", text)
            return m.group(1) if m else None
        if node.type == "template_string":
            m = re.match(r"^`([A-Za-z_][\w]*)\$\{", _text(node, self.src))
            return m.group(1) if m else None
        return None

    def _owner_class(self, target_object: str | None) -> str | None:
        if not target_object:
            return None
        if target_object.split(".")[0] in _SELF and "." not in target_object:
            return self.qname.rsplit(".", 1)[0]
        return None

    def _resolve_member(self, target_object, name: str) -> tuple[str, tuple[str, ...]]:
        owner = self._owner_class(target_object)
        if owner and f"{owner}.{name}" in self.b.symbol_table:
            return CERTAIN, (f"{owner}.{name}",)
        return POSSIBLE if owner else UNKNOWN, ()

    def _prefixed_members(self, target_object, prefix: str) -> tuple[str, ...]:
        owner = self._owner_class(target_object)
        if not owner:
            return ()
        names = sorted(q for q in _symbol_names(self.b) if q.startswith(owner + ".") and
                       q[len(owner) + 1:].startswith(prefix) and "." not in q[len(owner) + 1:])
        return tuple(names[:CAND_CAP]) + ((f"... (+{len(names) - CAND_CAP} more)",) if len(names) > CAND_CAP else ())


def _modules(builder) -> frozenset[str]:
    return frozenset(s.module for s in (builder.symbol_table.get(q) for q in _symbol_names(builder))
                     if s is not None and getattr(s, "module", None))


def _symbol_names(builder):
    table = builder.symbol_table
    for attr in ("symbols", "_symbols", "by_qname", "_by_qname"):
        store = getattr(table, attr, None)
        if isinstance(store, dict):
            return store.keys()
    try:
        return list(iter(table))
    except TypeError:
        return []


def _decorator_facts(ex: _Extractor) -> None:
    texts = collect_decorator_texts(ex.node, ex.parsed) or collect_annotation_texts(ex.node, ex.parsed)
    for text in texts:
        name = text.lstrip("@[").split("(", 1)[0].rstrip("]").strip()
        known = _KNOWN_DECORATORS.get(name) or _KNOWN_DECORATORS.get(name.rsplit(".", 1)[-1])
        if known:
            ex.facts.append(Fact("decorator", f"{_short(text, 60)}: {known}", _span(ex.node)[:1] * 2))
        else:
            effect = ("annotation: its effect, if any, comes from whatever processes it"
                      if ex.lang in (LanguageID.JAVA, LanguageID.CSHARP) else
                      "wraps or registers the function; its effect is defined by the decorator")
            ex.facts.append(Fact("decorator", f"{_short(text, 60)}: {effect}", _span(ex.node)[:1] * 2, POSSIBLE))


def _entry_fact(ex: _Extractor) -> None:
    entry = ex.entry
    if not entry:
        return
    method = getattr(entry, "method", "") or ""
    path = getattr(entry, "path", "") or ""
    kind = getattr(entry, "kind", "") or ""
    how = " ".join(x for x in (method, path) if x) or kind or "entry point"
    ex.facts.append(Fact("entry", f"registered as an entry point: {how}", _span(ex.node)[:1] * 2))


def _runtime_facts(ex: _Extractor) -> None:
    """Runtime-observed callees that no fact names: listed, not tied to a line."""
    if not ex.runtime:
        return
    named = {t for f in ex.facts for t in f.targets}
    static = {t for ts in ex.targets.values() for t, _ in ts}
    extra = sorted(ex.runtime - named - static)
    if extra:
        ex.facts.append(Fact("runtime", "callees seen at runtime that the code does not name", _span(ex.node),
                             RUNTIME, (), tuple(extra[:CAND_CAP])))


def _budget(facts: list[Fact], max_facts: int) -> tuple[list[Fact], dict[str, int]]:
    if len(facts) <= max_facts:
        return facts, {}
    ranked = sorted(range(len(facts)), key=lambda i: (_RANK.get(facts[i].kind, 9), facts[i].lines, i))
    keep = set(ranked[:max_facts])
    omitted: dict[str, int] = {}
    for i, f in enumerate(facts):
        if i not in keep:
            omitted[f.kind] = omitted.get(f.kind, 0) + 1
    return [f for i, f in enumerate(facts) if i in keep], omitted


def _indexed_defs(builder, path: str) -> dict[int, str]:
    out: dict[int, str] = {}
    for q, node in getattr(builder, "_def_nodes", {}).items():
        sym = builder.symbol_table.get(q)
        if sym is not None and sym.file == path and node is not None and hasattr(node, "start_byte"):
            out.setdefault(node.start_byte, q)
    return out


class DigestBuilder:
    """Digests for many functions of one index, sharing per-file lookups."""

    def __init__(self, builder, entries=None, runtime_edges: "set[tuple[str, str]] | None" = None,
                 max_facts: int = MAX_FACTS):
        self.b = builder
        self.entries = entries if entries is not None else {}
        self.max_facts = max_facts
        self.runtime: dict[str, set[str]] = {}
        for caller, callee in runtime_edges or ():
            if caller and callee:
                self.runtime.setdefault(caller, set()).add(callee)
        self._imports: dict[str, object] = {}
        self._indexed: dict[str, dict[int, str]] = {}
        self._module_names: frozenset[str] | None = None
        self._class_index: dict[str, list[str]] | None = None
        self._leaf_names: frozenset[str] | None = None
        self._aliases: dict[str, dict[str, str]] = {}

    def _classes(self) -> dict[str, list[str]]:
        if self._class_index is None:
            index: dict[str, list[str]] = {}
            for q in _symbol_names(self.b):
                sym = self.b.symbol_table.get(q)
                node = self.b.def_node(q)
                is_class = sym is not None and (sym.kind in ("class", "interface") or (
                    node is not None and getattr(node, "type", None) in CLASS_NODE_TYPES.get(sym.language_id, set())))
                if is_class:  # some nested classes are indexed with kind "method"; their syntax says class
                    index.setdefault(q.rsplit(".", 1)[-1].split("#", 1)[0], []).append(q)
            self._class_index = index
        return self._class_index

    def _leaves(self) -> frozenset[str]:
        if self._leaf_names is None:
            self._leaf_names = frozenset(q.rsplit(".", 1)[-1].split("#", 1)[0] for q in _symbol_names(self.b))
        return self._leaf_names

    def _modules(self) -> frozenset[str]:
        if self._module_names is None:
            self._module_names = _modules(self.b)
        return self._module_names

    def digest(self, qname: str) -> Digest:
        b = self.b
        sym = b.symbol_table.get(qname)
        node = b.def_node(qname)
        parsed = b.parsed_file(sym.file) if sym is not None else None
        if sym is None or node is None or parsed is None or not hasattr(node, "children"):
            return Digest(qname, str(getattr(parsed, "language_id", "")), (0, 0), note="no syntax tree for this symbol")
        if sym.file not in self._imports:
            try:
                self._imports[sym.file] = b._build_import_map(parsed, sym.module)
            except Exception:  # an import map is an aid, never a reason to fail the digest
                self._imports[sym.file] = None
            self._indexed[sym.file] = _indexed_defs(b, sym.file)
            if parsed.language_id == LanguageID.CSHARP:
                self._aliases[sym.file] = dict(_CS_USING_ALIAS.findall(parsed.source.decode("utf8", "replace")))
        entry = self.entries.get(qname) if isinstance(self.entries, dict) else (qname in self.entries)
        ex = _Extractor(b, qname, node, parsed, self._imports[sym.file], entry, self.runtime.get(qname),
                        self._indexed[sym.file], self._modules, self._classes, self._leaves,
                        self._aliases.get(sym.file))
        _entry_fact(ex)
        _decorator_facts(ex)
        ex.run()
        _runtime_facts(ex)
        note = None
        if _body(node) is None:
            note = "declaration only (no body)"
        elif not ex.facts:
            note = "empty body"
        facts = sorted(ex.facts, key=lambda f: (f.lines, _RANK.get(f.kind, 9)))
        facts, omitted = _budget(facts, self.max_facts)
        return Digest(qname, str(parsed.language_id), _span(node), facts, omitted, note)


def digest_function(builder, qname: str, entries=None, runtime_edges=None, max_facts: int = MAX_FACTS) -> Digest:
    return DigestBuilder(builder, entries, runtime_edges, max_facts).digest(qname)


def load_runtime_edges(paths) -> set[tuple[str, str]]:
    """`(caller, callee)` pairs from `prism trace` JSONL files (`.prism/traces/run_*.jsonl`)."""
    edges: set[tuple[str, str]] = set()
    for path in paths:
        with open(path, encoding="utf8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("caller") and row.get("callee"):
                    edges.add((row["caller"], row["callee"]))
    return edges
