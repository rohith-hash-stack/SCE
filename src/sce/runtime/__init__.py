"""Roadmap Step 3: the Dynamic Runtime Watcher.

Static analysis (`sce.graph`, `sce.tagger`, `sce.slicer`) can't see which
concrete implementation gets injected at runtime, which routes a framework
registers dynamically, or what external connections a function actually
opens - only what the source text itself proves. This package ingests real
execution evidence (a traced pytest run, or an imported OpenTelemetry
export) and reconciles it against the static Concrete Graph (`G_C`) and tag
matrix (`M`):

  - `tracer.py`: records `caller -> callee` call events during a real test
    run (a `sys.settrace` hook, wired in as a pytest plugin) to a
    `.sce/traces/run_<timestamp>.jsonl` file.
  - `reconciler.py`: loads a trace file (from either source) and merges it
    into the static graph - promoting an edge the static resolver already
    found to `confidence="CONFIRMED_RUNTIME"`, synthesizing a new
    `provenance="RUNTIME_DISCOVERED"` edge for a call the two-pass resolver
    missed (interface -> runtime-injected implementation, reflection,
    dynamic dispatch), and attaching `#external_io`/`#db_write`/`#db_read`
    to a caller whose external connection only OpenTelemetry could see.

Everything here is additive: it never removes or overrides a
statically-discovered edge, only confirms or extends what `G_C` already
has - and every persisted trace/state file lives under `.sce/`, this
package's own cache directory, never mixed into the indexed source tree.
"""
