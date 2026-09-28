"""`TypeScriptSourceLocator` (Phase D prerequisite): the TypeScript/
JavaScript `ExternalSourceLocator` implementation - real `node_modules`
resolution, no bespoke parser, matching `prism.external.index.
PythonSourceLocator`'s own "thin adapter that finds where a package's
real source or stub files live" contract exactly.

Node's own module resolution algorithm is inherently *starting-point*
relative (a bare specifier resolves by walking up from the *importing
file's own directory*, not from any single global path list the way
Python's `sys.path` works) - `ExternalSourceLocator.locate(package_name,
package_version)` has no such parameter, since it is deliberately a thin,
language-agnostic Protocol shared with `PythonSourceLocator`. The
starting directory is therefore a constructor argument here, not a
`locate` call argument: one `TypeScriptSourceLocator` instance is
constructed per real starting point (the importing file's own directory,
or a repo root when no more specific file context is available), the
same way a caller would construct a fresh instance per file rather than
expect one shared instance to somehow infer where to start walking up
from on every call.
"""
from __future__ import annotations

import json
from pathlib import Path


class TypeScriptSourceLocator:
    """Resolves an installed TypeScript/JavaScript package's real typed
    declaration (or, failing that, plain source) files via a real
    `node_modules` filesystem walk - never fabricated, never fetched over
    the network, mirroring `PythonSourceLocator`'s own discipline.

    Resolution order per package, matching real `tsc`/`node` module
    resolution:

      1. Walk up from `start_dir` to the filesystem root looking for
         `node_modules/<package_name>` at each level (`_find_package_dir`)
         - "nearest wins", the same hoisted-dependency resolution a real
         monorepo needs (a package hoisted to the workspace root's own
         `node_modules` is found once the walk reaches that level).
      2. Within that package directory, read `package.json` and pick a
         typed entry point in priority order (`_typed_entry_point`):
         `types`/`typings` field, then `exports["."]`'s own `types`
         (checked directly, then under its `import`/`require`
         conditions), then `main` (default `"index.js"`) with an
         adjacent same-named `.d.ts` checked.
      3. If the package itself ships no usable typed entry point this
         way (no `package.json` at all, or none of step 2's fields
         resolve to a real file on disk), a second, independent lookup
         is tried against the DefinitelyTyped-style sibling
         `@types/<name>` package (`_types_package_name`) - the real,
         common case for an older package like `express` that ships no
         types of its own at all.
      4. If neither the package nor its `@types` sibling yields a typed
         entry point, falls back to the package's own declared `main`
         JS file (or conventional default `index.js`) if that real file
         exists - the same "no stub, use the real source" degrade
         `PythonSourceLocator` already applies for a package (like
         Starlette) that ships no `.pyi` at all.
      5. Returns `[]` - never raises - when the package can't be found
         in `node_modules` anywhere between `start_dir` and the
         filesystem root at all (Scope 3's own "graceful degradation
         when a dependency is missing" requirement) and no `@types`
         sibling exists either.

    Scoped packages (`@trpc/server`, `@types/node`) are resolved via
    `package_name.split("/")` joined as *separate* path segments under
    `node_modules` (`node_modules/@trpc/server`, never a collapsed or
    re-split single-segment `@trpc-server`-shaped path) - the real,
    two-directory-deep layout npm/yarn/pnpm all use for a scoped
    package. `@types/node` itself resolves through this exact same,
    un-special-cased path, since it is a real, ordinary npm package in
    its own right (not merely a fallback target for some other package).

    Known, disclosed gap: a package whose real entry is itself a bundled
    re-export barrel with no inline definitions (an increasingly common
    modern build-tool output shape - confirmed live against a real
    installed `@trpc/server`, whose own `dist/index.d.cts` is entirely
    `export { ... } from "./<hashed-chunk>.cjs"` statements, and `zod`'s
    `index.d.cts`, three lines, entirely re-exports) is *located*
    correctly by this class, but a symbol only reachable by chasing that
    re-export into the chunk file will not be *found* by `prism.external.
    index._find_definition` - a real instance of Section 0's already-
    documented "no external-to-external chain-following" Non-goal
    (leaf-only, single-file resolution), not a bug in this locator's own
    resolution logic. `extract_external_symbol` degrades to its normal,
    documented `None` ("can't resolve this") result in that case, never a
    crash or a fabricated stub.

    `package_version` is accepted (Protocol conformance) and ignored,
    matching `PythonSourceLocator`'s own precedent - resolution is always
    against whatever is actually installed in `node_modules` on disk, no
    version-specific fetch or pin.
    """

    def __init__(self, start_dir: str) -> None:
        self._start_dir = Path(start_dir).resolve()

    def locate(self, package_name: str, package_version: str | None = None, subpath: str | None = None) -> list[Path]:
        """Tries, in the exact priority order Scope 1 specifies: the
        package's own typed entry point (`types`/`typings`, `exports["."]`,
        `main`-adjacent `.d.ts`); then, only if that fails, an `@types/
        <pkg>` sibling's own typed entry point (via that exact same
        chain); then, only if *that* also fails, the original package's
        own plain `.js` `main` - never earlier, so an untyped `express`
        with a real `@types/express` sibling installed correctly prefers
        the real types over its own untyped JS, rather than the plain-JS
        fallback short-circuiting before the `@types` lookup ever runs.

        `subpath` (`feature/subpath-export-resolution`): a real npm
        `exports` subpath the caller resolved the import through (e.g.
        `"adapters/express"` for `@trpc/server/adapters/express`) - when
        given, `_typed_entry_point` tries resolving *that* subpath's own
        `exports["./" + subpath]` entry (or a `<subpath>.d.ts`/
        `<subpath>/index.d.ts` file on disk) before falling through to
        this same root-export chain, both here and in the `@types`
        sibling lookup below. `None`/`""` (the default) is a complete
        no-op - every existing root-export caller is unaffected.
        """
        pkg_dir = self._find_package_dir(package_name)
        pkg_data = None
        if pkg_dir is not None:
            pkg_data = _read_package_json(pkg_dir / "package.json")
            if pkg_data is not None:
                entry = _typed_entry_point(pkg_data, pkg_dir, subpath=subpath)
                if entry is not None:
                    return [entry]

        if not package_name.startswith("@types/"):
            types_dir = self._find_package_dir(_types_package_name(package_name))
            if types_dir is not None:
                entry = self._best_effort_typed_entry(types_dir, subpath=subpath)
                if entry is not None:
                    return [entry]

        if pkg_dir is not None:
            resolved_pkg_dir: Path = pkg_dir
            if pkg_data is not None:
                fallback = _main_js_entry(pkg_data, resolved_pkg_dir)
                if fallback is not None:
                    return [fallback]
            default_index = resolved_pkg_dir / "index.js"
            if default_index.is_file():
                return [default_index]

        return []

    @staticmethod
    def _best_effort_typed_entry(pkg_dir: Path, subpath: str | None = None) -> Path | None:
        data = _read_package_json(pkg_dir / "package.json")
        if data is not None:
            entry = _typed_entry_point(data, pkg_dir, subpath=subpath)
            if entry is not None:
                return entry
        # An `@types/*` package with no readable `package.json` at all is
        # not a real shape DefinitelyTyped ever publishes, but degrades
        # the same honest way as everywhere else here: the conventional
        # default, checked directly, never fabricated.
        default_dts = pkg_dir / "index.d.ts"
        return default_dts if default_dts.is_file() else None

    def _find_package_dir(self, package_name: str) -> Path | None:
        """Walks upward from `self._start_dir` to the filesystem root,
        checking `<level>/node_modules/<package_name>` at each one -
        unbounded except by the real filesystem root, the same real
        `require.resolve`/`tsc` module-resolution walk (no separate
        "workspace root" heuristic invented here: a real repo can nest
        packages arbitrarily deep, and the filesystem root is already a
        hard, unambiguous stopping point).
        """
        segments = package_name.split("/")
        current = self._start_dir
        while True:
            candidate = current.joinpath("node_modules", *segments)
            if candidate.is_dir():
                return candidate
            parent = current.parent
            if parent == current:
                return None
            current = parent


def _types_package_name(package_name: str) -> str:
    """The DefinitelyTyped naming convention for `package_name`'s own
    `@types` sibling: `express` -> `@types/express`; a scoped package
    drops its own `@` and joins scope/name with a double underscore
    (`@babel/core` -> `@types/babel__core`) - real npm/DefinitelyTyped
    convention, not a guess."""
    if package_name.startswith("@") and "/" in package_name:
        scope, _, name = package_name.partition("/")
        return f"@types/{scope[1:]}__{name}"
    return f"@types/{package_name}"


def _read_package_json(package_json_path: Path) -> dict | None:
    try:
        raw = package_json_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _resolve_declared_path(pkg_dir: Path, declared: str) -> Path | None:
    """`declared` (a `package.json` field value like `"./dist/index.d.ts"`
    or `"index.d.ts"`) resolved against `pkg_dir`, appending `.d.ts` when
    `declared` names no extension at all (`"types": "index"` is real,
    valid shorthand both `tsc` and Node's own resolution accept) - never
    when it already has one, even a non-`.d.ts` one (a value should
    always be a real, existing file's own path, not overwritten by a
    guessed extension).
    """
    candidate = pkg_dir / declared
    if candidate.suffix:
        return candidate if candidate.is_file() else None
    with_dts = candidate.with_name(candidate.name + ".d.ts")
    return with_dts if with_dts.is_file() else None


def _types_from_exports_target(target: object) -> str | None:
    """A single `exports["."]` (or `exports["./<subpath>"]`, or top-
    level `exports`, for a package with no subpath map at all) value's
    own `types` condition, walked with the standard TypeScript/Node
    condition precedence: `types` direct (`{"types": "...", "import":
    "...", ...}`, the flatter shape a package like `zod` uses), then
    `import.types` -> `require.types` -> `default.types` (`{"import":
    {"types": "...", ...}, "require": {"types": "...", ...}}`, the
    shape `@trpc/server` uses; `default` is the same condition-object
    shape, checked last as the real ecosystem's own least-specific
    fallback condition) - `import` before `require` matches real `tsc`
    `moduleResolution: "bundler"` preference order. Purely a condition-
    object walk with no package-name awareness of any kind - the same
    function resolves any package's root `"."` export and any real
    subpath export identically. A bare-string target (`"exports":
    "./index.js"`, or a `default` condition that is itself a bare
    string rather than a nested object) names no types condition at all
    and returns `None` here - `_default_js_target`/`main`-adjacent
    `.d.ts` resolution is the right fallback for that shape, not this
    function's concern.
    """
    if not isinstance(target, dict):
        return None
    direct = target.get("types")
    if isinstance(direct, str):
        return direct
    for condition in ("import", "require", "default"):
        nested = target.get(condition)
        if isinstance(nested, dict):
            nested_types = nested.get("types")
            if isinstance(nested_types, str):
                return nested_types
    return None


def _default_js_target(target: object) -> str | None:
    """The real JS asset `target` ultimately resolves to when no
    `types`/`typings` condition exists anywhere in it - a bare string
    target itself, or whichever of its own `import`/`require`/`default`
    conditions names one (checked in that order, recursing into a
    nested condition object exactly as real Node resolution does).
    Used only as the basis for an adjacent-`.d.ts` guess
    (`_adjacent_dts_for_js_path`) - the same "no explicit types, so
    check the resolved JS file's own sibling `.d.ts`" degrade the root
    chain's `main`-adjacent resolution already applies, generalized to
    an `exports` condition target. Never itself returned as a types
    path.
    """
    if isinstance(target, str):
        return target
    if isinstance(target, dict):
        for condition in ("import", "require", "default"):
            nested = target.get(condition)
            if isinstance(nested, str):
                return nested
            if isinstance(nested, dict):
                resolved = _default_js_target(nested)
                if resolved is not None:
                    return resolved
    return None


def _adjacent_dts_for_js_path(pkg_dir: Path, js_path: str) -> Path | None:
    """`<js_path>.d.ts` (or `<js_path minus its own extension>.d.ts`)
    resolved against `pkg_dir`, checked for real existence on disk -
    the same sibling-`.d.ts` convention `_typed_entry_point`'s own
    `main`-adjacent fallback already uses, generalized to an arbitrary
    resolved `exports` JS target rather than just `main`."""
    candidate = pkg_dir / js_path
    adjacent = candidate.with_name(candidate.name + ".d.ts") if not candidate.suffix else candidate.with_suffix(".d.ts")
    return adjacent if adjacent.is_file() else None


def _best_export_pattern_match(exports: dict, request_key: str) -> tuple[object, str] | None:
    """The real Node.js `exports` "subpath pattern" match (a single
    literal `*` in a key, e.g. `"./features/*"` mapping to a target
    like `"./dist/features/*.js"`) for `request_key` (`"./" + subpath`)
    - tried only after an exact key lookup already failed, exactly
    matching real Node resolution order. Among every key containing
    exactly one `*` (the spec's own restriction - more than one `*` in
    a single key is not a valid pattern) whose literal prefix and
    suffix both match `request_key`, the key with the longest prefix
    wins - Node's own "most specific pattern" tie-break, the same
    discipline `_matching_root_import`'s longest-dotted-prefix rule
    already uses for root packages, generalized to pattern keys. No
    package-name awareness of any kind - purely a string-pattern match
    against whatever `exports` map is on disk.

    Returns `(raw_target, captured)` - `raw_target` is the matching
    key's own unmodified value (string or condition object); the
    caller substitutes `captured` into it via `_substitute_export_
    pattern` before resolving it as a real path. `None` when no
    pattern key matches at all.
    """
    best_target: object = None
    best_captured = ""
    best_prefix_len = -1
    for key, target in exports.items():
        if not isinstance(key, str) or key.count("*") != 1:
            continue
        prefix, _, suffix = key.partition("*")
        if not request_key.startswith(prefix) or not request_key.endswith(suffix):
            continue
        if len(request_key) < len(prefix) + len(suffix):
            continue  # prefix/suffix would overlap - not a real match
        captured = request_key[len(prefix) : len(request_key) - len(suffix)] if suffix else request_key[len(prefix) :]
        if len(prefix) > best_prefix_len:
            best_prefix_len = len(prefix)
            best_target = target
            best_captured = captured
    if best_target is None:
        return None
    return best_target, best_captured


def _substitute_export_pattern(target: object, captured: str) -> object:
    """Every literal `*` in `target`'s own string value(s) replaced
    with `captured` - real Node.js subpath-pattern substitution
    (`"./dist/*.js"` + captured `"adapters/express"` ->
    `"./dist/adapters/express.js"`) - applied recursively so a
    conditional target (`{"types": "./dist/*.d.ts", "default":
    "./dist/*.js"}`) substitutes inside every one of its own string
    values, not just a bare string target. The result is the same
    shape `target` itself was, ready to hand to `_types_from_exports_
    target`/`_default_js_target` exactly like a real, non-pattern
    `exports` value already is.
    """
    if isinstance(target, str):
        return target.replace("*", captured)
    if isinstance(target, dict):
        return {key: _substitute_export_pattern(value, captured) for key, value in target.items()}
    return target


def _typed_entry_point_for_subpath(data: dict, pkg_dir: Path, subpath: str) -> Path | None:
    """`_typed_entry_point`'s own subpath-first attempt (`feature/
    subpath-export-resolution`): checked before the root chain, never
    instead of it, and with no package-name special-casing anywhere -
    every step here is a generic `exports`-map/filesystem rule that
    resolves `lodash/fp`, `rxjs/operators`, `@tanstack/react-query/
    devtools`, and `@trpc/server/adapters/express` through the exact
    same code path. Tried in this order, matching real Node.js `exports`
    resolution precedence:

      1. An exact `exports["./" + subpath]` key.
      2. A single-`*` pattern key (`exports["./features/*"]`,
         `_best_export_pattern_match` - Node's own real "subpath
         pattern" mechanism), substituted via `_substitute_export_
         pattern`, only once an exact key lookup has already failed -
         exact keys always win over a pattern match, per spec.
      3. Whichever real target step 1 or 2 found is resolved the same
         way a root `exports["."]` target already is: `_types_from_
         exports_target`'s full `types` -> `import.types` ->
         `require.types` -> `default.types` precedence first; if that
         finds nothing, `_default_js_target` + `_adjacent_dts_for_
         js_path` (a bare `default`/string target names a JS file, not
         a types file, so its own adjacent `.d.ts` is tried next -
         mirrors the root chain's `main`-adjacent-`.d.ts` fallback).
      4. A real file directly on disk at `<pkg_dir>/<subpath>.d.ts` or
         `<pkg_dir>/<subpath>/index.d.ts` - the same "no exports map at
         all, just resolve the path" degrade `main`-adjacent `.d.ts`
         resolution already applies at the root, generalized to a
         subpath a package's own `exports` map doesn't mention (or
         mentions via a pattern that still doesn't resolve to a real
         file, e.g. a stale/aspirational entry).

    Returns `None` - never a guess - when nothing above resolves to a
    real file; `_typed_entry_point` itself falls through to the
    ordinary root chain from there.
    """
    exports = data.get("exports")
    if isinstance(exports, dict):
        key = f"./{subpath}"
        target = exports.get(key)
        if target is None:
            pattern_match = _best_export_pattern_match(exports, key)
            if pattern_match is not None:
                raw_target, captured = pattern_match
                target = _substitute_export_pattern(raw_target, captured)

        if target is not None:
            declared_types = _types_from_exports_target(target)
            if declared_types is not None:
                resolved = _resolve_declared_path(pkg_dir, declared_types)
                if resolved is not None:
                    return resolved

            js_path = _default_js_target(target)
            if js_path is not None:
                adjacent = _adjacent_dts_for_js_path(pkg_dir, js_path)
                if adjacent is not None:
                    return adjacent

    direct_dts = pkg_dir / f"{subpath}.d.ts"
    if direct_dts.is_file():
        return direct_dts
    index_dts = pkg_dir / subpath / "index.d.ts"
    if index_dts.is_file():
        return index_dts
    return None


def _typed_entry_point(data: dict, pkg_dir: Path, subpath: str | None = None) -> Path | None:
    """The real declaration-file priority chain (Scope 1): `types`/
    `typings` field first (the simplest, most direct signal), then
    `exports["."]`'s own conditional `types` mapping, then a same-named
    `.d.ts` sitting next to the declared (or conventional default)
    `main` JS file. Returns `None` - never a guess - when none of these
    resolve to a real file; the caller falls back further from there
    (`@types/<pkg>`, then the plain JS `main` itself).

    `subpath` (`feature/subpath-export-resolution`): when given and
    non-empty, `_typed_entry_point_for_subpath` is tried first - a real
    npm subpath export (`@trpc/server/adapters/express`) lives in its
    own, separate declaration file, never the root chain below, so a
    root-only lookup silently searches the wrong file for a symbol that
    only exists at the subpath. Falls through to the ordinary root
    chain when the subpath itself doesn't resolve, exactly like a
    caller with no subpath at all - a subpath miss is not treated as a
    reason to skip a package's real root types.
    """
    if subpath:
        subpath_entry = _typed_entry_point_for_subpath(data, pkg_dir, subpath)
        if subpath_entry is not None:
            return subpath_entry

    for field in ("types", "typings"):
        declared = data.get(field)
        if isinstance(declared, str):
            resolved = _resolve_declared_path(pkg_dir, declared)
            if resolved is not None:
                return resolved

    exports = data.get("exports")
    if isinstance(exports, dict):
        target = exports["."] if "." in exports else exports
        declared_types = _types_from_exports_target(target)
        if declared_types is not None:
            resolved = _resolve_declared_path(pkg_dir, declared_types)
            if resolved is not None:
                return resolved

    main_path = pkg_dir / _declared_or_default_main(data)
    adjacent_dts = main_path.with_name(main_path.name + ".d.ts") if not main_path.suffix else main_path.with_suffix(".d.ts")
    return adjacent_dts if adjacent_dts.is_file() else None


def _declared_or_default_main(data: dict) -> str:
    main = data.get("main")
    return main if isinstance(main, str) and main else "index.js"


def _main_js_entry(data: dict, pkg_dir: Path) -> Path | None:
    """No typed entry point resolved anywhere - the last, honest fallback:
    the package's own real, declared (or conventional default) JS `main`
    file, so tree-sitter parses genuine JS source directly rather than
    returning nothing at all. Mirrors `PythonSourceLocator`'s own "falls
    back to the package's own `.py` source when it ships no stubs at
    all" precedent - deliberately narrow (the one declared/default entry
    file, not a full-tree scan for any `.js` file anywhere in the
    package) since a real npm package routinely bundles a large `dist/`
    tree that a full scan would drag in wholesale, unlike a typical
    unbundled Python package's much smaller, flatter source layout.
    """
    main_path = pkg_dir / _declared_or_default_main(data)
    return main_path if main_path.is_file() else None
