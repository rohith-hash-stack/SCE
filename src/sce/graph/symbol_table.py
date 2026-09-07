"""Global symbol table, module-path resolution, and per-file scope maps.

This module implements the data structures used by the two-pass linker
described in HLD section 4.1:

  - `GlobalSymbolTable`: fully-qualified-name -> definition metadata,
    populated by Pass 1.
  - `LocalImportMap`: local token -> fully qualified symbol, built per file
    from `import` / `from ... import` statements.
  - `InstanceTypeMap`: local variable -> fully qualified class name, built
    per function from constructor-assignment statements (`v = Verifier()`).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def path_to_module(file_path: str, repo_root: str) -> str:
    """Convert a repo-relative or absolute file path into a dotted module name.

    ``src/auth/jwt.py`` (relative to `repo_root`) -> ``src.auth.jwt``.
    ``src/auth/__init__.py`` -> ``src.auth``.
    """
    rel = os.path.relpath(file_path, repo_root)
    rel_no_ext, _ext = os.path.splitext(rel)
    parts = [p for p in rel_no_ext.split(os.sep) if p not in ("", ".")]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


@dataclass
class SymbolInfo:
    qualified_name: str
    kind: str  # "class" | "function" | "method"
    file: str
    line_range: tuple[int, int]  # 1-indexed, inclusive [start, end]
    language_id: str
    module: str
    enclosing_class: str | None = None


class GlobalSymbolTable:
    """Fully-qualified-name -> `SymbolInfo`, populated during Pass 1."""

    def __init__(self) -> None:
        self._symbols: dict[str, SymbolInfo] = {}
        # module -> {simple_name: qualified_name}, for intra-file Rule D lookups.
        self._module_index: dict[str, dict[str, str]] = {}

    def add(self, symbol: SymbolInfo) -> None:
        self._symbols[symbol.qualified_name] = symbol
        simple_name = symbol.qualified_name.rsplit(".", 1)[-1]
        self._module_index.setdefault(symbol.module, {})[simple_name] = symbol.qualified_name
        # Also index by the class-qualified name (Class.method) so `self.method()`
        # resolution can look up `EnclosingClass.method` without the module prefix.
        if symbol.enclosing_class is not None:
            class_simple = symbol.enclosing_class.rsplit(".", 1)[-1]
            local_name = f"{class_simple}.{simple_name}"
            self._module_index.setdefault(symbol.module, {})[local_name] = symbol.qualified_name

    def get(self, qualified_name: str) -> SymbolInfo | None:
        return self._symbols.get(qualified_name)

    def __contains__(self, qualified_name: str) -> bool:
        return qualified_name in self._symbols

    def __iter__(self):
        return iter(self._symbols.values())

    def __len__(self) -> int:
        return len(self._symbols)

    def resolve_in_module(self, module: str, simple_name: str) -> str | None:
        """Rule D fallback: look up a bare name defined in the same module."""
        return self._module_index.get(module, {}).get(simple_name)

    def all_qualified_names(self) -> list[str]:
        return list(self._symbols.keys())


@dataclass
class LocalImportMap:
    """local token -> fully-qualified symbol/module, for one source file."""

    aliases: dict[str, str] = field(default_factory=dict)

    def add(self, local_name: str, qualified_target: str) -> None:
        self.aliases[local_name] = qualified_target

    def resolve(self, local_name: str) -> str | None:
        return self.aliases.get(local_name)


@dataclass
class InstanceTypeMap:
    """local variable -> fully-qualified class name, scoped to one function."""

    bindings: dict[str, str] = field(default_factory=dict)

    def bind(self, var_name: str, qualified_class: str) -> None:
        self.bindings[var_name] = qualified_class

    def resolve(self, var_name: str) -> str | None:
        return self.bindings.get(var_name)
