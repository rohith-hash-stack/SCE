# Contributing to Prism

This document is the maintenance guide for the engine's branching model, release tagging, and
the checks a change must pass before it lands on `main`.

## Branching model

`main` is the single default branch - always releasable, always green. There is no long-lived
`develop`/`staging` branch.

All new work branches off the latest `main`:

```bash
git checkout main
git pull origin main
git checkout -b feat/upstream-blast-radius   # a new feature
git checkout -b fix/java-scaffold-threshold  # a bug fix
git checkout -b chore/rebrand-to-prism       # a maintenance/tooling task
```

- **`feat/<enhancement-name>`** - a new capability (a new CLI command, a new MCP tool, support
  for another language, ...). Example: `feat/upstream-blast-radius`.
- **`fix/<bug-name>`** - a bug fix or correctness fix to existing behavior. Example:
  `fix/java-scaffold-threshold`.
- **`chore/<task-name>`** - maintenance work that isn't a feature or a bug fix: dependency
  bumps, renames/rebrands, CI/tooling changes, doc reorganization. Example:
  `chore/rebrand-to-prism`.

Name the branch after what it does, in `kebab-case`, specific enough that `git branch` alone
tells you what's in flight. Merge back into `main` (a regular merge or squash-merge, whichever
keeps `main`'s history readable) once the change meets the merge checklist below, then delete
the branch - `feat/`/`fix/`/`chore/` branches are short-lived, not archived.

## Merge checklist

Before merging any `feat/*`, `fix/*`, or `chore/*` branch into `main`:

1. **`pytest tests/` passes.** The full suite, not a subset - 300+ tests as of `v0.1.0`. A
   handful are gated behind `PRISM_LIVE_NETWORK_TESTS=1` (real clones of django/hono/gin/express/
   spring-petclinic/eShopOnWeb) and skip by default; run them too if the change touches parsing,
   linking, or anything the polyglot benchmark harnesses exercise.
   ```bash
   pytest tests/
   ```
2. **`python scripts/verify_uvx_execution.py` exits 0.** This is the one check that actually
   proves the packaging is intact, not just the code: it builds an ephemeral wheel from the
   working tree via `uvx --from .` (the identical path `uvx --from git+https://github.com/
   rohith-hash-stack/SCE.git` takes for an end user) and confirms the `prism` console script
   resolves, `prism mcp --help` documents `stdio`/`sse`/`--repo`, and a real MCP stdio
   `initialize` -> `tools/list` handshake returns all 5 tools. A change to `pyproject.toml`,
   `[project.scripts]`, or a new `prism.*` submodule that isn't wired into packaging correctly
   will pass `pytest` and still fail this - that's exactly the gap it exists to catch.
   ```bash
   python scripts/verify_uvx_execution.py
   ```
3. **`version` in `pyproject.toml` is bumped**, and `src/prism/__init__.py`'s `__version__` plus
   `src/prism/mcp/server.py`'s `MCPServer(..., version=...)` are updated to match - all three
   should always read the same version. See "Cutting a release" below for how the bump and the
   tag relate.

Both checks 1 and 2 must be run locally (or in CI, once configured) before merging - neither is
optional, and "the diff looks small" is not a substitute for actually running them.

## Release tagging protocol

Releases are tagged directly on `main`, following [Semantic Versioning](https://semver.org/):
`vMAJOR.MINOR.PATCH` - always with the `v` prefix, always three numeric components (`v0.1.0`,
not `v0.1` or `0.1.0`).

- **MAJOR** - a breaking change: a CLI flag removed/renamed, an MCP tool's parameters or return
  shape changed incompatibly, a supported language dropped.
- **MINOR** - a backward-compatible addition: a new CLI command, a new MCP tool, a new
  supported language, a new architectural tag.
- **PATCH** - a backward-compatible fix: a correctness fix in the parser/linker/tagger/slicer,
  a packaging fix, a documentation fix.

`v0.1.0` is the project's first tracked release - the engine is still pre-1.0 and actively
evolving (see the git history: each `roadmap Step N` commit and the `benchmarks/README.md`
"real engine bugs found and fixed" sections), so version numbers stay in the `0.x` range until
the CLI/MCP surface is considered stable enough to commit to backward compatibility across
releases. `v0.1.0` predates the SCE -> Prism rebrand - its tree still has the old `sce` package/
command, not `prism` - so `v0.2.0` (the rebrand release) is the earliest tag anyone pinning
`prism` specifically should use; see below.

### Cutting a release

1. Merge every `feat/*`/`fix/*` branch intended for the release into `main`, each having passed
   the merge checklist above on its own.
2. On `main`, bump the version in all three places together (`pyproject.toml`'s `version`,
   `src/prism/__init__.py`'s `__version__`, `src/prism/mcp/server.py`'s `MCPServer(...,
   version=...)`), commit.
3. Re-run the full merge checklist (`pytest tests/` and `python scripts/verify_uvx_execution.py`)
   one more time against that exact commit - the one that will be tagged, not an earlier one.
4. Tag it and push the tag:
   ```bash
   git tag -a v0.2.0 -m "v0.2.0"
   git push origin v0.2.0
   ```

### How users pin a release

`docs/mcp_setup.md`'s `uvx`-based VS Code configs (Roo Code/Cline's `mcp_settings.json`, native
VS Code's `.vscode/mcp.json`) point at `git+https://github.com/rohith-hash-stack/SCE.git` with
no ref, which always resolves to `main`'s current tip. To pin a specific, immutable release
instead - recommended for anyone who wants reproducible behavior rather than "whatever `main`
is today" - append `@v<VERSION>` to the git URL:

```
git+https://github.com/rohith-hash-stack/SCE.git@v0.2.0
```

`uv` resolves `@v0.2.0` to that exact tagged commit and builds from it, the same way it builds
from `main` otherwise - nothing else about the config changes. Verify a pinned ref the same way
as any other change to the packaging surface, substituting the tag for `.`:

```bash
uvx --refresh --from git+https://github.com/rohith-hash-stack/SCE.git@v0.2.0 prism --help
```

## Quick reference

| Action | Command |
|---|---|
| Start a feature | `git checkout -b feat/<name> main` |
| Start a fix | `git checkout -b fix/<name> main` |
| Run the full test suite | `pytest tests/` |
| Verify `uvx` packaging | `python scripts/verify_uvx_execution.py` |
| Cut a release | bump all 3 version strings -> re-verify -> `git tag -a vX.Y.Z -m "vX.Y.Z"` -> `git push origin vX.Y.Z` |
| Pin a release in a client config | `git+https://github.com/rohith-hash-stack/SCE.git@vX.Y.Z` |
