# Roadmap: Public Release Benchmark Portfolio

**Status: committed plan, not yet executed.** This document records the
target portfolio, exit criteria, and sequencing agreed after Phase C's
closure (`d7a8d2f`) and the multi-repo external-indexing validation
pass (`f2db664`) - written to be checked against as work proceeds, the
same discipline every other milestone in this project has held itself
to (Phase B's own closure debrief, the Phase C architecture spec).
Nothing in Sections 2-4 below has been executed yet; Section 1's
portfolio choice and Section 5's Go gate are the product of real,
already-measured data cited inline, not projection.

## 0. Why this exists

Prism has one validated formal PASS (FastAPI, Python) and a real,
working cross-boundary external-dependency retrieval system (Phase C).
Neither, on its own, proves Two-Pass retrieval generalizes beyond a
single language and ecosystem. Before any public release or benchmark
publication, the claim "Prism is multi-language and production-ready"
needs the same standard of evidence every other claim in this project
has been held to: real corpora, real ground truth, the existing
statistical gate, and a willingness to publish an honest "not yet" on
whatever doesn't clear it - exactly the standard that caught the
Django "gate PASS" error and the `t018`/`t019`/`t020` construct-validity
gap rather than publishing around them.

## 1. Target portfolio

**Python + TypeScript, 3-4 repos, 2 languages.** Go is explicitly
excluded from this portfolio - see Section 5 for why and what would
change that.

| Repo | Language | Role | Corpus status |
|---|---|---|---|
| **FastAPI** | Python (Tier 1) | Primary anchor - already has a formal PASS (headroom-aware gate, 900-cell aggregate + holdout replication, `reports/fastapi_seed42_closure_debrief.md`) and the validated Phase C external-dependency fix (`t018`/`t019`/`t020`). | Pinned (`0.116.1`), 25 ground-truth tasks already authored. |
| **Django** | Python (Tier 1) | Second architectural profile within the same Tier-1 language (OOP inheritance vs. FastAPI's decorator-driven DI) - re-run through the *current* headroom-aware gate rather than treated as spent; its own Phase B result predates that gate. | Pinned (`4.2.30`), 24 ground-truth tasks already authored. |
| **Express** | TypeScript/JS (Tier 2) | First TypeScript-ecosystem target - single-package, mature, authored in plain JavaScript with a separately-versioned `@types/express` DefinitelyTyped package. Deliberately the *simpler* half of the TS story - proves the mechanism before tRPC's monorepo complexity. | Pinned (`4.21.0`), **zero ground-truth tasks authored yet**. |
| **tRPC** | TypeScript (Tier 2) | Second, harder TypeScript-ecosystem target - real TypeScript, heavy generics, bundled `.d.ts`, but a monorepo (`packages/*`) - real npm-workspace-hoisting friction Express doesn't have (Section 4). Sequenced *after* Express, not in parallel. | Pinned (`v10.45.4`), **zero ground-truth tasks authored yet**. |

All four repos are already pinned in `benchmarks/corpora/
pinned_commits.json` - zero new corpus-acquisition risk. Ground-truth
task authoring for Express and tRPC is real, uncosted work (see
Section 3's pilot stage) - `benchmarks/ground_truth/tasks/` currently
holds only `django/` and `fastapi/`.

**Why not a 3rd language for the public story right now**: Java and C#
(Tier 2, per `src/prism/language_tiers.py` - real constructor-based
instance binding, but no `EXTENDS`/`IMPLEMENTS` walk and no barrel
resolution) are the honest next candidates if a 3-language claim is
wanted sooner than Go's own gate (Section 5) can close - their
documented gaps are narrower and already characterized, unlike Go's.
Neither is committed here; this section names Python+TypeScript as the
committed portfolio.

## 2. Exit criteria

The existing headroom-aware gate (`scripts/apply_gate.py`) applies
**unchanged** to every repo in this portfolio - no new gate, no
per-language threshold rewrite. A public benchmark's credibility comes
partly from never moving the goalposts between corpora, and this gate
already survived a real Django/FastAPI cross-check (Phase B's own
closure debrief, Section 7).

A repo is "production-ready" only when **all** of the following hold:

1. **ΔTSR ≥ +15pp vs. baseline, 95% CI strictly excludes zero** (the
   gate's own flat, non-headroom-adjusted primary threshold).
2. **ΔCPI_answer non-negative and gate-cleared**: the flat +15pp bar
   when baseline headroom ≥ 20%, or the headroom-aware ≥35%-of-
   remaining-headroom bar when saturated - in both cases with its own
   95% CI strictly excluding zero. Never a raw "did not get worse"
   pass with no CI check.
3. **No `CPI_retrieval` regression** - Two-Pass must never newly
   exclude a real, previously-reachable correct answer relative to the
   baseline engine on the same task.
4. **A manual construct-validity audit of every FAIL/MIXED cell**,
   required, not optional. This is exactly how `t018`/`t019`/`t020`
   were found - skipping it on a new repo means shipping with an
   unknown number of "gate says fail, but it's actually a ground-truth
   bug" cases baked in, undetected.
5. **A latency/turn-count SLA, checked, not just argued**: p50/p95
   wall-clock for two-pass vs. single-pass on the same hardware, and
   the fraction of cells that trip the conditional three-pass branch
   reported as a real number (Phase C Section 1.5 argues this cost
   qualitatively; this criterion makes it a checked, reported figure
   per repo, not an assumption).

**Before trusting a PASS on any new repo**: re-verify the gate's own
constants (the 12.5%/256/1024 external-budget clamp, the 35%/20%
headroom split) against that repo's own real baseline numbers first -
they were disclosed as calibrated to the FastAPI/Django dataset, not
derived from first principles (Phase C spec, Section 7 item 5). Never
assume they transfer; check, the same way Django's own numbers were
checked against the FastAPI-derived rule before either was trusted.

## 3. Evaluation pipeline (per repo, staged)

Applied to each new repo (Express, then tRPC) independently and in
sequence - never two repos' locator work and benchmark runs in
parallel, matching how Phase B sequenced Django then FastAPI and Phase
C sequenced four steps on Python before ever touching a second
language.

1. **Graph-quality spike, zero LLM cost.** Before any locator work,
   confirm Turn 1's candidate manifest actually contains real
   ground-truth pipeline symbols on the target repo - the same dry-run
   recall check `benchmarks/experiments/inspect_manifest_sizing.py`
   already did for Django before any live grid was run. If Tier 2's
   lack of instance binding produces a real recall gap here, it needs
   to be known before a single task is authored against it.
2. **Locator implementation** (`TypeScriptSourceLocator` for Express/
   tRPC), gated on the import-alias prerequisite in Section 4 - not
   before it, since a receiver-based-only resolver is expected to
   underperform on TypeScript's own dominant named-import style even
   more than it already does in Python (Phase C Section 8.1).
3. **Small pilot, 5-8 tasks.** Author a small, targeted ground-truth
   suite before the full 20-25-task commitment - mirroring how Phase C
   validated against exactly 3 targeted tasks (`t018`/`t019`/`t020`)
   before claiming anything broader. Seed count staged, not fixed:
   start with a dry run or single seed to catch a broken Turn-1 prompt
   cheaply, then a small multi-seed confirmation once the pipeline
   looks right.
4. **Full benchmark: 20-25 tasks, 5 seeds** - matching FastAPI's own
   precedent (25 tasks, 5 seeds, the run that produced the only formal
   PASS on record), not a reduced seed count adopted for cost reasons.
   A validated pipeline run once at 5 seeds is cheaper than an
   under-seeded run that needs redoing.
5. **Gate + manual audit** (Section 2, items 1-5) against this run's
   real results.

## 4. Prerequisite: import-alias tracking, before the TypeScript rollout

Phase C Section 8.1 documented a real, disclosed gap: `build_external_
candidate_manifest`'s two resolution paths (a `root_imports`-package
receiver, or a `self`/`this` receiver) both require a real two-segment
call. A name imported directly - Python's `from X import Y; Y(...)`,
TypeScript's `import { X } from 'Y'; X(...)` - produces a
**single-segment** call that matches neither path, by construction.

This is a **prerequisite for the TypeScript rollout, not a backlog
item to fold in later**: named imports (`import { z } from 'zod'`) are
the dominant, idiomatic style in modern TypeScript/JavaScript, more so
than Python's own `from x import y`. Porting the current receiver-based
resolver to TypeScript as-is is expected to underperform on exactly the
calls it most needs to catch - shipping the TypeScript portfolio without
this fix risks a real, avoidable Tier-2-specific credibility gap on top
of the tier's own already-known structural-linking-only limitation.

Resolving it requires reading each call site's own file for its real
import statements and mapping an alias back to its true origin module
(the `import X as Y` aliasing case, and the "imported from a package's
own re-export, not its defining submodule" case both apply) - real,
scoped design and implementation work, not a small patch. This
prerequisite should land, and be verified against a real repo, before
Section 3's pilot stage begins on Express.

## 5. Go milestone: explicitly gated, not scheduled

Go remains on the long-term roadmap but is **excluded from this
portfolio** on the strength of a real, already-measured number, not a
guess: the README's own Language Capability Matrix states **49.75%
receiver-call resolution measured on `gin-gonic/gin`**
(`src/prism/graph/concrete_builder.py`'s Issue B1 receiver/parameter-
typed binding, `PrecisionTier.TIER_3_LEXICAL` in `src/prism/
language_tiers.py`). If half of Go's real call edges never reach the
graph, a Two-Pass-vs-baseline comparison on Go conflates two
indistinguishable failure modes - "the model chose badly" and "the
edge was never in the candidate manifest to begin with" - and produces
a result that cannot be trusted or defended.

**The gate**: no `GoSourceLocator` implementation, no Go ground-truth
task authoring, and no Go benchmark run begins until receiver-call
resolution on `gin-gonic/gin` is re-measured past **80%** (a concrete,
checkable number - not "meaningfully improved," a specific bar to
clear before this workstream opens at all). This is a prerequisite
investment in Go's own core call-resolution graph fidelity
(`concrete_builder.py`'s Go-specific binding logic), independent of and
prior to any Phase-C-style external-dependency work for Go.

**Additional, structural friction beyond the resolution-rate gate**,
recorded here so it isn't rediscovered mid-implementation once the gate
is cleared:

- **Structural interfaces**: a Go call through an interface method
  doesn't name the concrete external type at the call site at all -
  even at 80%+ receiver-call resolution, this is not a locator gap but
  a fundamentally different resolution problem than either of the two
  paths Phase C already built for Python handle. External-dependency
  retrieval for Go may need its own, third resolution strategy, not a
  port of the existing two.
- **Module cache vs. `vendor/`**: modern Go modules don't always commit
  a `vendor/` directory - a real `GoSourceLocator` needs to handle the
  `$GOPATH/pkg/mod/<module>@<version>/` cache path too, which has no
  Python or npm analogue (neither version-suffixes its install
  directory the way Go's module cache does).

Closing the 80% gate is its own, separate piece of work and is not
scheduled by this document - this section exists so the bar is written
down before anyone is tempted to benchmark Go against a lower one.

## 6. Summary sequencing

```
Phase C (done, d7a8d2f)
  |
  v
Django re-run through current gate  ---\
                                         >-- Python portfolio confirmed
FastAPI already PASS (banked)      ---/
  |
  v
Import-alias tracking (Section 4, prerequisite)
  |
  v
Express: graph-quality spike -> locator -> 5-8 task pilot -> full 20-25/5-seed run -> gate + audit
  |
  v
tRPC:    graph-quality spike -> locator -> 5-8 task pilot -> full 20-25/5-seed run -> gate + audit
  |
  v
Python + TypeScript portfolio complete -> public release readiness review

(separately, unscheduled, gated on its own bar:)
Go receiver-call resolution on gin-gonic/gin: 49.75% -> re-measure past 80%
  |
  v
GoSourceLocator + Go ground-truth suite (only once the above clears)
```
