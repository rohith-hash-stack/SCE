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

    def locate(self, package_name: str, package_version: str | None = None) -> list[Path]:
        """Tries, in the exact priority order Scope 1 specifies: the
        package's own typed entry point (`types`/`typings`, `exports["."]`,
        `main`-adjacent `.d.ts`); then, only if that fails, an `@types/
        <pkg>` sibling's own typed entry point (via that exact same
        chain); then, only if *that* also fails, the original package's
        own plain `.js` `main` - never earlier, so an untyped `express`
        with a real `@types/express` sibling installed correctly prefers
        the real types over its own untyped JS, rather than the plain-JS
        fallback short-circuiting before the `@types` lookup ever runs.
        """
        pkg_dir = self._find_package_dir(package_name)
        pkg_data = None
        if pkg_dir is not None:
            pkg_data = _read_package_json(pkg_dir / "package.json")
            if pkg_data is not None:
                entry = _typed_entry_point(pkg_data, pkg_dir)
                if entry is not None:
                    return [entry]

        if not package_name.startswith("@types/"):
            types_dir = self._find_package_dir(_types_package_name(package_name))
            if types_dir is not None:
                entry = self._best_effort_typed_entry(types_dir)
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
    def _best_effort_typed_entry(pkg_dir: Path) -> Path | None:
        data = _read_package_json(pkg_dir / "package.json")
        if data is not None:
            entry = _typed_entry_point(data, pkg_dir)
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
    """A single `exports["."]` (or top-level `exports`, for a package
    with no subpath map at all) value's own `types` condition - checked
    directly first (`{"types": "...", "import": "...", ...}`, the flatter
    shape a package like `zod` uses), then under its `import`/`require`
    sub-conditions (`{"import": {"types": "...", ...}, "require":
    {"types": "...", ...}}`, the shape `@trpc/server` uses) - `import`
    tried first as the more modern/forward convention, matching real
    `tsc` `moduleResolution: "bundler"` preference order. A bare-string
    target (`"exports": "./index.js"`) names no types condition at all
    and returns `None` here - `main`-adjacent `.d.ts` resolution is the
    right fallback for that shape, not this function's concern.
    """
    if not isinstance(target, dict):
        return None
    direct = target.get("types")
    if isinstance(direct, str):
        return direct
    for condition in ("import", "require"):
        nested = target.get(condition)
        if isinstance(nested, dict):
            nested_types = nested.get("types")
            if isinstance(nested_types, str):
                return nested_types
    return None


def _typed_entry_point(data: dict, pkg_dir: Path) -> Path | None:
    """The real declaration-file priority chain (Scope 1): `types`/
    `typings` field first (the simplest, most direct signal), then
    `exports["."]`'s own conditional `types` mapping, then a same-named
    `.d.ts` sitting next to the declared (or conventional default)
    `main` JS file. Returns `None` - never a guess - when none of these
    resolve to a real file; the caller falls back further from there
    (`@types/<pkg>`, then the plain JS `main` itself).
    """
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
