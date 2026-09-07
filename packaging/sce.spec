# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for a single-file `sce` binary (`sce.exe` on Windows).

Build with:
    pyinstaller --clean --noconfirm packaging/sce.spec

(or `python scripts/build_standalone.py`, which also runs the smoke tests
below against the produced binary). Not meant to be imported - PyInstaller
`exec()`s this file with `Analysis`/`PYZ`/`EXE` already in scope.

Why the extra collection calls below are needed at all: PyInstaller's
default static analysis walks `import` statements it can see textually. It
does NOT see:
  - `sce.parser.tree_sitter_loader._load_language`'s per-language `import
    tree_sitter_xxx` calls, since each one is inside its own `if` branch
    picked at runtime by a string id, not a module-level import - hence the
    explicit `hiddenimports` for every `sce.*` submodule that's only ever
    reached this way (parsers, the tagger, the slicer, the MCP server).
  - the compiled grammar extension each `tree_sitter_xxx` package ships
    (`_binding.abi3.so`) or its `queries/*.scm` package data - neither is a
    `.py` file a normal import scan would find, hence `collect_all()`
    (submodules + data + binaries together) for every tree-sitter package.
  - `mcp[cli]`'s own dynamic plumbing (its stdio/SSE/streamable-HTTP
    transports select concrete `starlette`/`uvicorn` implementations at
    runtime) and `pydantic`'s compiled Rust core (`pydantic_core`, a
    separate package pydantic v2 imports internally) - both need the same
    `collect_all()` treatment as the tree-sitter grammars, for the same
    "no static import PyInstaller's scanner can see" reason.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

PACKAGING_DIR = Path(SPECPATH).resolve()  # noqa: F821 - injected by PyInstaller
PROJECT_ROOT = PACKAGING_DIR.parent
SRC_DIR = PROJECT_ROOT / "src"
ENTRYPOINT = SRC_DIR / "sce" / "cli.py"

# --------------------------------------------------------------------- #
# collect_all(): submodules + package data + compiled binaries, for every
# package whose contents PyInstaller's static import scan can't fully see
# on its own (per the module docstring above).
# --------------------------------------------------------------------- #
COLLECT_ALL_MODULES = [
    "tree_sitter",
    "tree_sitter_python",
    "tree_sitter_javascript",
    "tree_sitter_typescript",
    "tree_sitter_go",
    "tree_sitter_java",
    "tree_sitter_c_sharp",
    "mcp",
    "networkx",
    "pydantic",
    # Not in the original request, but load-bearing: pydantic v2's actual
    # validation engine is this separate compiled-Rust package, imported
    # internally by `pydantic` itself - `collect_all("pydantic")` alone
    # does not pull it in, and `sce mcp` fails at import time without it
    # (confirmed by an actual PyInstaller build+smoke-test of this spec -
    # see scripts/build_standalone.py).
    "pydantic_core",
]

datas: list[tuple[str, str]] = []
binaries: list[tuple[str, str]] = []
hiddenimports: list[str] = []

for _module_name in COLLECT_ALL_MODULES:
    _datas, _binaries, _hiddenimports = collect_all(_module_name)
    datas += _datas
    binaries += _binaries
    hiddenimports += _hiddenimports

# Belt-and-suspenders: the tree-sitter grammar packages' compiled grammar
# lives in a Python C-extension module (`_binding.abi3.so`), which
# `collect_all()` above already gathers as a binary via its own submodule
# scan - `collect_dynamic_libs()` is run too per the task's own spec, and
# is harmless if it finds nothing new for a given package/platform.
TREE_SITTER_GRAMMAR_MODULES = [
    "tree_sitter_python",
    "tree_sitter_javascript",
    "tree_sitter_typescript",
    "tree_sitter_go",
    "tree_sitter_java",
    "tree_sitter_c_sharp",
]
for _module_name in TREE_SITTER_GRAMMAR_MODULES:
    binaries += collect_dynamic_libs(_module_name)

# --------------------------------------------------------------------- #
# hiddenimports: sce's own modules only ever reached via a dynamic
# (string-keyed or lazy) import PyInstaller's static scanner can't follow.
# --------------------------------------------------------------------- #
hiddenimports += [
    # sce.mcp.server: imported lazily inside cli.py's `mcp` command (so
    # `sce index`/`query`/`trace`/`status` work with zero MCP dependency
    # cost) - PyInstaller's entrypoint scan starts at cli.py and would
    # otherwise never see it.
    "sce.mcp.server",
    "sce.mcp.cache",
    "sce.runtime.tracer",
    "sce.runtime.reconciler",
    "sce.runtime.trace",
    "sce.slicer.universal_slicer",
    "sce.slicer.compressor",
    "sce.slicer.distance",
    "sce.slicer.knapsack",
    # Every language-specific parser module - each is only ever imported
    # from inside tree_sitter_loader._load_language's per-language `if`
    # branch, selected by a runtime string id, never by a module-level
    # `import` statement PyInstaller's scanner would see.
    "sce.parser.tree_sitter_loader",
    "sce.parser.lang_config",
    "sce.parser.queries",
    "sce.tagger.engine",
    "sce.tagger.rules",
    "sce.serializers.markdown",
    "sce.serializers.json_debug",
    "sce.graph.concrete_builder",
    "sce.graph.metamodel",
    "sce.graph.symbol_table",
]

block_cipher = None

a = Analysis(  # noqa: F821 - injected by PyInstaller
    [str(ENTRYPOINT)],
    pathex=[str(SRC_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)  # noqa: F821 - injected by PyInstaller

# Onefile distribution: `a.binaries`/`a.zipfiles`/`a.datas` are passed
# straight into EXE() (rather than a separate onedir-style COLLECT() step),
# which is what makes this a single self-extracting executable instead of
# a directory of loose files.
exe = EXE(  # noqa: F821 - injected by PyInstaller
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="sce",
    debug=False,
    bootloader_ignore_signals=False,
    strip=sys.platform != "win32",  # PyInstaller/the platform strip tool doesn't support Windows PE stripping
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
