"""Robot Framework suites (`.robot`) and resource files (`.resource`).

Robot files have no tree-sitter grammar here, so this is a small line-based
reader of the plain-text format (space- or pipe-separated). It adds:

* one `function` symbol per test case (role `VERIFICATION`) and per user
  keyword (role `IMPLEMENTATION`), language `"robot"`;
* `CALLS` edges from each test/keyword to the user keywords and Python
  library keywords it uses, including `[Setup]`/`[Teardown]`/`[Template]`,
  `Test Setup`/`Test Teardown`/`Test Template`, and the keyword argument of
  `Run Keyword*`/`Wait Until Keyword Succeeds`/`Repeat Keyword`;
* two synthetic symbols per suite for `Suite Setup`/`Suite Teardown`.

Keyword lookup follows Robot's own rules, simplified:

* names match case-insensitively, ignoring spaces and underscores, so
  `Create User` is `create_user`; `Library.Keyword` restricts the lookup to
  that library or resource; `${arg}` parts of a keyword name (embedded
  arguments) match any text;
* a call resolves against keywords defined in the same file first, then in
  its (transitively) imported resource files, then in its imported
  libraries;
* a Python library is a `Library` import of a `.py` path or a dotted module /
  class name found in the repo. Its keywords are its public functions (a
  module) or the public methods of the class named like the module (or the
  named class), renamed by `@keyword("...")`. `@library` or
  `ROBOT_AUTO_KEYWORDS = False` restrict them to `@keyword` methods, and
  `@not_keyword` excludes one;
* a call no import explains, whose name matches exactly one keyword in the
  repo, is linked as `TENTATIVE_CALL` (Robot's search path can make keywords
  visible that this reader cannot see imported).

Standard and external libraries (BuiltIn, Collections, SeleniumLibrary,
Browser, RequestsLibrary, ...) are not in the repo, so their keywords are
simply not linked.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from prism.graph.symbol_table import SymbolInfo, SymbolRole, path_to_module

ROBOT_EXTENSIONS = (".robot", ".resource")
ROBOT_LANGUAGE = "robot"

_SECTION = re.compile(r"^\s*\*+\s*([A-Za-z ]+?)\s*\**\s*$")
_SEPARATOR = re.compile(r" {2,}|\t+| \t")
_ASSIGNMENT = re.compile(r"^[$@&]\{[^}]*\}(\[[^\]]*\])*\s*=?$")
_VARIABLE = re.compile(r"\$\{([^}]*)\}")
_SLUG = re.compile(r"\W+", re.UNICODE)
_CONTROL = frozenset({"FOR", "END", "IF", "ELSE IF", "ELSE", "WHILE", "TRY", "EXCEPT", "FINALLY", "BREAK",
                      "CONTINUE", "RETURN", "VAR", "IN", ":FOR", "GROUP"})
_SETTINGS_WITH_KEYWORD = frozenset({"[setup]", "[teardown]", "[template]"})
#: `Run Keyword*`-style BuiltIn keywords: index of the nested keyword
#: argument (`None` = every `AND`-separated part, as in `Run Keywords`).
_RUN_KEYWORD_ARG = {
    "runkeyword": 0, "runkeywordandignoreerror": 0, "runkeywordandreturnstatus": 0,
    "runkeywordandcontinueonfailure": 0, "runkeywordandwarnonfailure": 0, "runkeywordandreturn": 0,
    "runkeywordandexpecterror": 1, "runkeywordif": 1, "runkeywordunless": 1, "runkeywordandreturnif": 1,
    "waituntilkeywordsucceeds": 2, "repeatkeyword": 1, "runkeywords": None,
    "runkeywordiftestfailed": 0, "runkeywordiftestpassed": 0, "runkeywordifalltestspassed": 0,
    "runkeywordifanytestsfailed": 0, "runkeywordiftimeoutoccurred": 0,
}


def normalize(name: str) -> str:
    return re.sub(r"[\s_]", "", name).lower()


def _slug(text: str) -> str:
    return _SLUG.sub("_", text).strip("_") or "unnamed"


def _cells(line: str) -> list[str]:
    stripped = line.rstrip("\n")
    if stripped.lstrip().startswith("| "):
        cells = [c.strip() for c in stripped.strip().strip("|").split(" | ")]
    else:
        cells = [c.strip() for c in _SEPARATOR.split(stripped)]
    # a comment cell ends the line
    out: list[str] = []
    for c in cells:
        if c.startswith("#"):
            break
        out.append(c)
    return out


@dataclass(frozen=True)
class _Call:
    name: str
    line: int
    binds_return: bool = False      # `${x}=    Keyword`
    passes_variables: bool = False  # an argument carries a `${...}`/`@{...}`/`&{...}` value


def _passes_variables(args: list[str]) -> bool:
    return any(re.search(r"[$@&]\{", a) for a in args)


@dataclass
class _Block:
    name: str
    kind: str                          # "test" | "keyword" | "suite"
    start: int                         # 1-indexed lines
    end: int
    calls: list["_Call"] = field(default_factory=list)
    template: str | None = None
    qualified_name: str = ""


@dataclass
class RobotFile:
    path: str
    module: str
    libraries: list[tuple[str, int]] = field(default_factory=list)      # (import text, line)
    resources: list[tuple[str, int]] = field(default_factory=list)
    blocks: list[_Block] = field(default_factory=list)


def _body_calls(cells: list[str], line_no: int) -> list[_Call]:
    """Keyword calls on one body line (cells after the leading indent)."""
    while cells and cells[0] in ("", "\\"):
        cells = cells[1:]
    if not cells or cells[0] == "...":
        # a continuation line only matters for `Run Keyword If ... ELSE Kw`
        return _else_branches(cells[1:], line_no) if cells else []
    head = cells[0].upper()
    if head in _CONTROL:
        if head in ("ELSE", "ELSE IF", "IF") and len(cells) > 1:
            return []        # inline IF: condition/keyword split is ambiguous here
        return []
    assigned = False
    while cells and _ASSIGNMENT.match(cells[0]):
        cells, assigned = cells[1:], True
    if cells and cells[0] == "=":
        cells = cells[1:]
    if not cells or not cells[0]:
        return []
    return _expand_run_keyword(cells, line_no, assigned)


def _else_branches(cells: list[str], line_no: int) -> list[_Call]:
    out: list[_Call] = []
    for i, c in enumerate(cells):
        if c == "ELSE" and i + 1 < len(cells):
            out.extend(_expand_run_keyword(cells[i + 1:], line_no))
        elif c == "ELSE IF" and i + 2 < len(cells):
            out.extend(_expand_run_keyword(cells[i + 2:], line_no))
    return out


def _expand_run_keyword(cells: list[str], line_no: int, assigned: bool = False) -> list[_Call]:
    """The call `cells[0]` and, for a `Run Keyword*`-style keyword, the
    keyword(s) it runs. A nested call's own arguments end at `ELSE`/`AND`."""
    name = cells[0]
    own_args: list[str] = []
    for arg in cells[1:]:
        if arg in ("ELSE", "ELSE IF", "AND"):
            break
        own_args.append(arg)
    out = [_Call(name, line_no, assigned, _passes_variables(own_args))]
    key = normalize(name.rsplit(".", 1)[-1]) if normalize(name).startswith("builtin.") else normalize(name)
    if key not in _RUN_KEYWORD_ARG:
        return out
    args = cells[1:]
    index = _RUN_KEYWORD_ARG[key]
    if index is None:                                  # Run Keywords  A  AND  B
        part: list[str] = []
        for arg in [*args, "AND"]:
            if arg == "AND":
                if part:
                    out.extend(_expand_run_keyword(part, line_no, assigned))
                part = []
            else:
                part.append(arg)
        return out
    if index < len(args):
        out.extend(_expand_run_keyword(args[index:], line_no, assigned))
    if key == "runkeywordif":
        out.extend(_else_branches(args, line_no))
    return out


def parse_robot_file(path: str, repo_root: str) -> RobotFile:
    with open(path, encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()
    module = path_to_module(path, repo_root)
    robot = RobotFile(path=path, module=module)
    section = ""
    current: _Block | None = None
    file_test_template: str | None = None
    per_test: list[_Call] = []                             # Test Setup / Teardown
    suite: dict[str, _Block] = {}

    def close(line_no: int) -> None:
        nonlocal current
        if current is not None:
            current.end = max(current.start, line_no)
            robot.blocks.append(current)
            current = None

    last_content_line = 0
    for i, raw in enumerate(lines, start=1):
        header_text = raw
        if raw.lstrip().startswith("| "):                  # pipe format: `| *** Settings *** |`
            header_text = next(iter(_cells(raw)), "")
        header = _SECTION.match(header_text) if header_text.lstrip().startswith("*") else None
        if header:
            close(last_content_line)
            section = header.group(1).strip().lower().rstrip("s")
            continue
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        cells = _cells(raw)
        if section in ("setting", "settings"):
            if not cells or not cells[0]:
                continue
            setting = cells[0].lower()
            value = cells[1] if len(cells) > 1 else ""
            if setting == "library" and value:
                robot.libraries.append((value, i))
            elif setting == "resource" and value:
                robot.resources.append((value, i))
            elif setting in ("test setup", "test teardown", "task setup", "task teardown") and value:
                per_test.extend(_expand_run_keyword(cells[1:], i))
            elif setting in ("test template", "task template") and value:
                file_test_template = value
            elif setting in ("suite setup", "suite teardown") and value:
                name = "Suite Setup" if setting == "suite setup" else "Suite Teardown"
                suite[name] = _Block(name, "suite", i, i, _expand_run_keyword(cells[1:], i))
            continue
        if section not in ("test case", "task", "keyword"):
            continue
        indented = raw[0] in (" ", "\t") or (raw.startswith("| ") and cells and cells[0] == "")
        if not indented:
            close(last_content_line)
            current = _Block(cells[0], "keyword" if section == "keyword" else "test", i, i)
            last_content_line = i
            rest = cells[1:]                       # name and first step on one line
            if rest and current is not None:
                current.calls.extend(_body_calls(["", *rest], i))
            continue
        if current is None:
            continue
        last_content_line = i
        body = cells[1:] if cells and cells[0] == "" else cells
        if body and body[0].lower() in _SETTINGS_WITH_KEYWORD:
            if len(body) > 1 and body[1].upper() != "NONE":
                if body[0].lower() == "[template]":
                    current.template = body[1]
                current.calls.extend(_expand_run_keyword(body[1:], i))
            continue
        if body and body[0].startswith("["):
            continue
        if current.kind == "test" and (current.template or file_test_template):
            continue                               # data rows of a templated test
        current.calls.extend(_body_calls(["", *body], i))
    close(last_content_line)

    for block in robot.blocks:
        if block.kind == "test":
            if file_test_template and not block.template:
                block.calls.append(_Call(file_test_template, block.start))
            block.calls.extend(per_test)
    robot.blocks.extend(suite.values())
    used: dict[str, int] = {}
    for block in robot.blocks:
        leaf = _slug(block.name)
        n = used.get(leaf, 0) + 1
        used[leaf] = n
        block.qualified_name = f"{module}.{leaf}" if n == 1 else f"{module}.{leaf}_{n}"
    return robot


# ---------------------------------------------------------------------- #
# Python keyword libraries
# ---------------------------------------------------------------------- #
_DECORATOR = re.compile(r"^@\s*([\w.]+)\s*(?:\((.*)\))?\s*$", re.DOTALL)
_STRING_ARG = re.compile(r"""(?:name\s*=\s*)?(['"])(.*?)\1""", re.DOTALL)


def _decorators(builder, qname: str) -> list[tuple[str, str | None]]:
    """(decorator name, first string argument) for a Python symbol."""
    node = builder.def_node(qname)
    info = builder.symbol_table.get(qname)
    parsed = builder.parsed_file(info.file) if info is not None else None
    if node is None or parsed is None or node.parent is None or node.parent.type != "decorated_definition":
        return []
    out: list[tuple[str, str | None]] = []
    for child in node.parent.children:
        if child.type != "decorator":
            continue
        text = parsed.source[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
        match = _DECORATOR.match(text.strip())
        if match is None:
            continue
        name = match.group(1).rsplit(".", 1)[-1]
        arg = _STRING_ARG.search(match.group(2) or "") if match.group(2) else None
        out.append((name, arg.group(2) if arg else None))
    return out


def _python_library_keywords(builder, owner: str, members: list[SymbolInfo], file_text: str,
                             owner_is_library_class: bool) -> dict[str, str]:
    """keyword name -> symbol for one module's functions or one class's methods."""
    auto = "ROBOT_AUTO_KEYWORDS = False" not in file_text.replace("  ", " ")
    if owner_is_library_class:
        for name, arg in _decorators(builder, owner):
            if name == "library" and not (arg is None and "auto_keywords=True" in file_text.replace(" ", "")):
                auto = False
    keywords: dict[str, str] = {}
    for member in members:
        simple = member.qualified_name.rsplit(".", 1)[-1]
        decorators = dict(_decorators(builder, member.qualified_name))
        if "not_keyword" in decorators:
            continue
        if "keyword" in decorators:
            keywords[decorators["keyword"] or simple] = member.qualified_name
        elif auto and not simple.startswith("_"):
            keywords[simple] = member.qualified_name
    return keywords


@dataclass
class _Library:
    name: str                                     # what `Name.Keyword` uses
    keywords: dict[str, str]                      # normalized keyword -> qname
    patterns: list[tuple[re.Pattern, str]] = field(default_factory=list)


def _index_keywords(name: str, keywords: dict[str, str]) -> _Library:
    lib = _Library(name, {})
    for kw, qname in keywords.items():
        if "${" in kw:
            parts = re.split(r"\$\{[^}]*\}", kw)
            regex = ".+?".join(re.escape(" ".join(p.split())) for p in parts)
            lib.patterns.append((re.compile(f"^{regex}$", re.IGNORECASE), qname))
        else:
            lib.keywords.setdefault(normalize(kw), qname)
    return lib


def _lookup(lib: _Library, call: str) -> str | None:
    hit = lib.keywords.get(normalize(call))
    if hit is not None:
        return hit
    spaced = " ".join(call.split())
    for pattern, qname in lib.patterns:
        if pattern.match(spaced):
            return qname
    return None


class _PythonLibraries:
    """Resolves `Library` import text to the repo's Python keyword libraries."""

    def __init__(self, builder, repo_root: str) -> None:
        self.builder = builder
        self.repo_root = repo_root
        self._by_file: dict[str, list[SymbolInfo]] = {}
        for symbol in builder.symbol_table:
            if symbol.language_id == "python":
                self._by_file.setdefault(symbol.file, []).append(symbol)
        self._modules = {path_to_module(f, repo_root): f for f in self._by_file}
        self._cache: dict[str, _Library | None] = {}

    def _library_for_file(self, path: str, class_name: str | None = None) -> _Library | None:
        symbols = self._by_file.get(path)
        if not symbols:
            return None
        stem = os.path.splitext(os.path.basename(path))[0]
        module = path_to_module(path, self.repo_root)
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        wanted = class_name or stem
        cls = next((s for s in symbols if s.kind == "class" and s.qualified_name == f"{module}.{wanted}"), None)
        if cls is not None:
            methods = [s for s in symbols if s.kind == "method" and s.enclosing_class == cls.qualified_name]
            keywords = _python_library_keywords(self.builder, cls.qualified_name, methods, text, True)
            return _index_keywords(wanted, keywords)
        if class_name is not None:
            return None
        functions = [s for s in symbols if s.kind == "function" and s.enclosing_class is None]
        return _index_keywords(stem, _python_library_keywords(self.builder, module, functions, text, False))

    def resolve(self, import_text: str, importing_file: str) -> _Library | None:
        key = f"{importing_file}\0{import_text}"
        if key in self._cache:
            return self._cache[key]
        lib: _Library | None = None
        text = import_text.replace("${CURDIR}", os.path.dirname(importing_file)).replace("\\", "/")
        if text.endswith(".py") or "/" in text:
            candidate = os.path.normpath(os.path.join(os.path.dirname(importing_file), text))
            if not os.path.isabs(text) and candidate in self._by_file:
                lib = self._library_for_file(candidate)
            elif os.path.normpath(text) in self._by_file:
                lib = self._library_for_file(os.path.normpath(text))
            else:                                       # search path: match by trailing path
                tail = "/" + text.lstrip("./").split("${")[-1].lstrip("}/")
                matches = [f for f in self._by_file if f.replace("\\", "/").endswith(tail)]
                if len(matches) == 1:
                    lib = self._library_for_file(matches[0])
        else:
            parts = text.split(".")
            for module, path in self._modules.items():
                if module == text or module.endswith("." + text):
                    lib = self._library_for_file(path)
                    break
            if lib is None and len(parts) >= 2:             # pkg.module.ClassName
                owner = ".".join(parts[:-1])
                for module, path in self._modules.items():
                    if module == owner or module.endswith("." + owner):
                        lib = self._library_for_file(path, class_name=parts[-1])
                        break
        self._cache[key] = lib
        return lib


# ---------------------------------------------------------------------- #
# Linking
# ---------------------------------------------------------------------- #
def _resource_path(import_text: str, importing_file: str, robot_files: dict[str, RobotFile]) -> str | None:
    text = import_text.replace("${CURDIR}", os.path.dirname(importing_file)).replace("\\", "/")
    candidate = os.path.normpath(os.path.join(os.path.dirname(importing_file), text))
    if candidate in robot_files:
        return candidate
    tail = "/" + os.path.normpath(text).replace("\\", "/").lstrip("./")
    matches = [f for f in robot_files if f.replace("\\", "/").endswith(tail)]
    return matches[0] if len(matches) == 1 else None


def link_robot_framework(builder, robot_paths: list[str]) -> dict[str, int]:
    """Adds Robot test/keyword symbols and their keyword-call edges to
    `builder`. Returns counts for diagnostics."""
    repo_root = builder.repo_root
    robot_files: dict[str, RobotFile] = {}
    for path in sorted(robot_paths):
        try:
            robot_files[path] = parse_robot_file(path, repo_root)
        except (OSError, UnicodeDecodeError) as exc:
            builder._record_index_error(path, exc, stage="robot")

    for robot in robot_files.values():
        for block in robot.blocks:
            role = SymbolRole.VERIFICATION if block.kind in ("test", "suite") else SymbolRole.IMPLEMENTATION
            line_range = (block.start, block.end)
            key = builder.symbol_table.add(SymbolInfo(
                qualified_name=block.qualified_name, kind="function", file=robot.path, line_range=line_range,
                language_id=ROBOT_LANGUAGE, module=robot.module, enclosing_class=None, role=role,
            ))
            block.qualified_name = key
            builder.graph.add_node(
                key, kind="function", file=robot.path, line_range=line_range, language_id=ROBOT_LANGUAGE,
                module=robot.module, enclosing_class=None, role=role, test_block=f"robot_{block.kind}",
                robot_name=block.name,
            )

    own: dict[str, _Library] = {
        path: _index_keywords(os.path.splitext(os.path.basename(path))[0],
                              {b.name: b.qualified_name for b in r.blocks if b.kind == "keyword"})
        for path, r in robot_files.items()
    }
    python_libraries = _PythonLibraries(builder, repo_root)

    def scope(path: str) -> tuple[list[_Library], list[_Library]]:
        """(resource libraries, python libraries) visible from `path`."""
        resources: list[_Library] = []
        libraries: list[_Library] = []
        seen: set[str] = set()
        stack = [path]
        while stack:
            current = stack.pop(0)
            if current in seen:
                continue
            seen.add(current)
            robot = robot_files[current]
            if current != path:
                resources.append(own[current])
            for text, _line in robot.libraries:
                lib = python_libraries.resolve(text, current)
                if lib is not None:
                    libraries.append(lib)
            for text, _line in robot.resources:
                target = _resource_path(text, current, robot_files)
                if target is not None:
                    stack.append(target)
        return resources, libraries

    all_keywords: dict[str, set[str]] = {}
    for lib in own.values():
        for norm, qname in lib.keywords.items():
            all_keywords.setdefault(norm, set()).add(qname)

    edges = tentative = unresolved = 0
    for path, robot in robot_files.items():
        resources, libraries = scope(path)
        for block in robot.blocks:
            for robot_call in block.calls:
                call = robot_call.name
                target: str | None = None
                kind: str | None = None
                prefix, _, rest = call.rpartition(".")
                if prefix and rest:
                    for lib in [*resources, *libraries, own[path]]:
                        if normalize(lib.name) == normalize(prefix):
                            target = _lookup(lib, rest)
                            if target is not None:
                                break
                if target is None:
                    for lib in [own[path], *resources, *libraries]:
                        target = _lookup(lib, call)
                        if target is not None:
                            break
                if target is None:
                    candidates = all_keywords.get(normalize(call), set())
                    if len(candidates) == 1:
                        target, kind = next(iter(candidates)), "TENTATIVE_CALL"
                if target is None:
                    unresolved += 1
                    continue
                if target == block.qualified_name:
                    continue
                if target not in builder.graph:
                    builder.graph.add_node(target, external=False)
                existing = builder.graph.get_edge_data(block.qualified_name, target)
                if existing is not None:
                    # several call sites: an indicator holds if any site has it
                    existing["robot_binds_return"] |= robot_call.binds_return
                    existing["robot_args"] |= robot_call.passes_variables
                    continue
                attrs = {"relation": "CALLS", "robot_keyword": call, "robot_binds_return": robot_call.binds_return,
                         "robot_args": robot_call.passes_variables}
                if kind:
                    attrs["kind"] = kind
                    tentative += 1
                builder.graph.add_edge(block.qualified_name, target, **attrs)
                edges += 1
    return {"robot_files": len(robot_files), "robot_edges": edges, "robot_tentative": tentative,
            "robot_unlinked_calls": unresolved}
