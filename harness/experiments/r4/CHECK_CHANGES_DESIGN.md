# `prism_check_changes` (B5): R4's multi-turn loop, design for execution (2026-11-10)

**Status:** the design is settled (the user, 2026-11-10). This file is the execution plan. Nothing in it is built
yet.

**Sources:** `SCOPE_INVESTIGATION.md` (the two-direction analysis on R3a's 80 tasks), and the spec
(`PRISM_REQUIRED_BEHAVIOR.md` §4, V1–V4).

## 1. What drives R4's multi-turn loop

- **`prism_check_changes` is R4's "retry" mechanism.** It is core to R4, not optional. The user's corrections:
  1. **Retry means iteration after each edit, not a pre-flight verdict.** After every AI edit, PRISM checks the
     working tree and returns facts. The AI reads them and makes its next edit. There is no undo, no rollback and no
     replan:
     - turn 1: the AI reads the task and edits;
     - turn 2: `prism_check_changes` returns what broke and what does not fit;
     - turn 3: the AI edits;
     - turn 4: check again, and so on, until clean or the turn cap.
  2. **Facts, not verdicts.** PRISM never says "proceed: no", "task failed" or "this cascades". It says which callers
     break, what is undefined, which caller 3 hops away now needs a change. The AI decides.
- **One computation, two consumers:**

| Consumer | When | Input | Output |
|---|---|---|---|
| **the gate** (task selection; the sanity check's Layer 1) | before any run, offline | the anchor change, and the developer's commit | a classification: bounded + clean, cascading, upstream-fail, beyond callers. Used to accept or reject a task |
| **the agent** (`prism_check_changes`) | after every `edit_file` | the working tree against `HEAD` | facts in four sections (§3) |

- **Timing:**
  - **When:** the check runs after every `edit_file`, always. Its output is conditional: the boolean gate, §8.
  - **Cost:** the analysis measured 0.003–0.015 s per anchor on an existing index. The live index's refresh after
    an edit is measured at verification (§7, step 4); the budget is at most 0.5 s per check.

## 2. Internal / external callee classification

**The rule:** each call target in new or changed code gets one of three origins.
- **Internal:** defined in the repository's own source.
- **External:** a third-party package, a standard-library or system module, or a runtime builtin. The fact says: do
  NOT edit; the anchor must conform.
- **Unresolved:** defined nowhere and imported from nowhere. This is U1, "the change is incomplete".

**What PRISM already has:**
- the graph marks unindexed targets `external=True` (`concrete_builder`);
- a per-file `LocalImportMap` maps each local name to its fully qualified origin (`_import_maps`);
- the repository's own top-level packages (`deps/host._host_local_roots`);
- the installed packages and their stubs (`deps/discover.find_distribution`, `find_stubs`).

**The decision, in order (the first match decides):**

| Step | Test | Origin |
|---|---|---|
| 1 | the call's name is a parameter, a local, or a loop or `with`/`as` binding in the enclosing function (from the parse tree's scopes) | not a callee: skipped |
| 2 | the target resolves to a symbol defined in a repository source file that is not vendored | **internal** |
| 3 | the local name is in the file's import map, and its origin's top-level module is not a repository root | **external** |
| 4 | the name is a language builtin (Python `builtins`; JS/TS globals such as `Promise`, `JSON`, `Object`, `fetch`) | **external** (builtin) |
| 5 | a method call on a receiver whose type is external (`requests.Session().get`, `str.join`) or unknown | **external** when the receiver's type is external; otherwise **unknown receiver** (stated as unknown, never guessed: spec Q1) |
| 6 | nothing above | **unresolved** (U1) |

**The edge cases** (the user's table):

| Case | Handling |
|---|---|
| Type stubs (`.pyi`, `.d.ts`) describing external APIs | **external**. A stub inside the repository describing an internal module (the golden `fmt.pyi`) marks its module internal. A stub of a third-party module is external |
| Vendored code (`vendor/`, `third_party/`, `node_modules/`, `_vendor/`, `site-packages/`) | **external**: physically present, logically external. The path rule applies before step 2 |
| Namespace packages or ambiguous imports (a star import; a top-level name that is both a repository root and an installed distribution) | **external** by default (the safer side). The fact names the ambiguity |
| The same name defined internally and installed externally | the file's import statement decides. With no import, or an import that can resolve either way, both are reported: "`parse` may be `shelf.core.parse.parse` (internal) or `dateutil.parser.parse` (external)" |

**What changes in the facts by origin:**

| Origin | Fact | Expected action |
|---|---|---|
| downstream caller | "`caller` passes 3 arguments; `anchor` now takes 2" | update the caller |
| internal callee | "internal `Y` requires `strict`; this call does not pass it" | the AI's choice: update the callee, or adapt the call |
| external callee | "you call `requests.get(..., timeout=None)`; the library expects a positive number. **Do NOT edit `requests`: change this call**" | adapt the anchor's call only |
| unresolved | "`limitRequestBody` is called but defined nowhere and not imported: define or import it" | define or import |

**How external facts are checked:**
- **The binding** needs the external signature: from installed package headers (`prism.deps`) or stubs.
- **Without a signature,** only existence is checked ("external `requests.get`: signature unavailable, arguments not
  checked"). That keeps spec L1: name what is not checked.
- **Value-level facts** (`timeout=None` against "a positive number") need the library's documented constraints. They
  are reported only where a stub's type excludes the value: `None` against a non-`Optional` parameter. Anything
  beyond that is out of scope for R4 (a guess).

## 3. Output format (four sections, under 800 tokens)

```
## Downstream (callers that break from your edit)
- `pkg.mod.caller` (pkg/mod.py:41): passes 3 positional arguments; `pkg.core.anchor` now takes at most 2 [certain]
- hop 2: `pkg.api.handler` calls `pkg.mod.caller`; check it if you change `caller`'s signature

## Upstream — internal
- `pkg.util.normalize` (internal) requires `strict`; your call at pkg/core.py:12 does not pass it [certain] — update the callee or adapt the call

## Upstream — external (do NOT edit; adapt anchor)
- `requests.get` (third-party `requests`): `timeout=None`, but the stub declares `timeout: float` — change this call, not the library

## Warnings
- `pkg.core._old_helper` is no longer called by anything (you removed its last call)
- `limitRequestBody` is called but defined nowhere and not imported (pkg/core.py:30): define or import it
```

**Rules:**
- **Empty sections are omitted.** When everything is clean, the whole answer is one line: "No downstream or upstream
  issues detected from the latest edit."
- **Every fact** carries file:line and its certainty: `[certain]`, `[possible]` (an ambiguous target), `[unknown]`
  (a receiver of unknown type). Possible and unknown are phrased as such, never as a bare claim (the golden
  invariant "formatting never turns possible into certain").
- **"Defined nowhere"** belongs in Warnings in this format. It blocks completion, so it is listed first.
- **Budget:** at most 800 tokens, so at most about 12 facts.
  - **The order:** certain downstream failures; unresolved calls; internal mismatches; external mismatches;
    possible facts; warnings.
  - **When cut:** the answer ends with "+N more (run `prism_check_changes` again after these edits)". It never cuts a
    fact in half.
- **Scope:** every function changed in the working tree against `HEAD` (spec V4), not only the latest edit. Facts
  about the latest edit's functions come first. So the answer changes whenever the tree changes, and an identical
  repeat of it is itself a signal (C1).

## 4. Spec update (S3 / V1)

Done in `PRISM_REQUIRED_BEHAVIOR.md` §4 (V1, and the new V1a for the internal/external rule) and `ROADMAP_R4_R6.md`
§1 (the S3 row).

## 5. Layer 1's parsed-code fix also gives the classification

**Yes.** Both come from the same parse tree:
- the parsed-code call detection (Correction 3) walks the anchor's syntax tree for call nodes;
- step 1 above (parameters and locals) uses that tree's scopes;
- steps 2–4 use the call node's resolved target, and the file's import map, both built from the same parse.

So the fix produces, per call, a callee with its origin in one pass. The gate uses the origin to decide "anchor calls
undefined" (unresolved only, not external). The agent's tool uses it to phrase the fact.

**The false alarms this removes** (`SCOPE_INVESTIGATION.md` §2):

| False alarm | Why it disappears |
|---|---|
| `async (` | not a call node |
| `VARCHAR` (inside a string) | a string literal, not a call |
| `oper`, `hook` (called local variables) | a parameter or local: step 1 |
| `id(...)` and `type(...)` resolved to repository methods (D1) | builtins in step 4, once A1 (D1) lands |
| `# type:` inside a signature | the signature is parsed from the tree, not from text |

## 6. Does the gate need the internal/external distinction?

**Mostly no. Your read is right:** the gate accepts or rejects, and phrasing is the agent's concern.
- **One place it matters for correctness:** the gate's "anchor calls an undefined function" must not fire on an
  external call. An external call is defined, just not in the repository. So the gate needs the three-way origin to
  tell **unresolved** (reject: the anchor is incomplete) from **external** (fine).
- **That comes free** from the same pass (§5).
- **The gate never needs** the "do not edit" phrasing, or external signatures.

## 7. Sequence

| Step | Work | Done when |
|---|---|---|
| 1 | **Parsed-code call detection:** call nodes from the parse tree, with parameter and local scopes. This is A3 (D2) in the build order, and the same code path the digest uses | the golden D2 traps list their unresolved calls; R3a's 8 "undefined" cases still flag |
| 2 | **Internal/external classification** (§2), with A1 (D1) so builtins never resolve to repository methods | golden fixtures: an external call, a vendored call, a stub, the same-name case |
| 3 | **`prism_check_changes`** (§3): V1–V3 over every changed function against `HEAD`, on the live index; the break check against the committed version (A2) | golden outputs for a clean tree and for each section; under 800 tokens; cost measured |
| 4 | **Verification on R3a's 80** (re-run `scope_inv/`'s analysis with steps 1–2) | all 8 "undefined" cases flag; the 9 false-alarm classes are gone; the cost is at most 0.5 s per check on the live index |
| 5 | **Ready for the R4 build**: the gate uses the pre-flight form; the agent loop calls the post-edit form after each edit | — |

**How this fits the code build order** (`BUILD_ORDER.md`):
- **Order:** steps 1–2 are A3 and A1 plus the classification. Step 3 is B5, which needs A2 and B2. Step 4 is the
  verification.
- **The golden recording still comes first.** It needs the tokenizer asset, so each fix's output change is visible.

## 8. The boolean gate (decided 2026-11-10; recorded in `PREREG_DRAFT.md` §2)

**The rule:**
1. After every `edit_file`, the check runs. Always.
2. It computes one boolean: is anything affected, upstream or downstream?
3. The output depends on the boolean:

| Condition | Boolean | What is sent after the edit's own result |
|---|---|---|
| any downstream break | true | the full four-section feedback |
| any internal upstream mismatch | true | the full four-section feedback |
| any external upstream mismatch (verified against a known signature) | true | the full four-section feedback, with the "do NOT edit" phrasing |
| any undefined call | true | the full four-section feedback |
| warnings only (dead code, a removed callee with no other callers) | true (soft) | **the warnings section only**, without the other headings |
| no issues | false | **`[check: clean]`** (one line, about 5 tokens) |

**The warnings-only format:**
```
## Warnings
- [file:line] [what]
```

**Why a clean line instead of silence:**
- Silence is ambiguous: was the edit clean, did the check fail, or is it late?
- `[check: clean]` costs about 5 tokens, removes that ambiguity, says the check ran on this edit, and tells clean
  apart from warnings-only.

**Token cost** (a task of about 10 edits, about 3 of them with issues):

| Protocol | Tokens per task |
|---|---|
| append the full feedback after every edit | 10 × 800 = **8,000** |
| the boolean gate with the one-line clean | 3 × 800 + 7 × 5 = **2,435** (about 70% less) |

**The rule's own failure:** if the check errors or times out, the line is `[check: unavailable]`, never
`[check: clean]`. An unverified edit is never reported as clean.

**Verification after the fixes** (§7, step 4; validation, not a decision input): on R3a's 80 tasks, replaying each
developer change as edits, count:
- edits that trigger true, and edits that trigger false;
- that `[check: clean]` is the common case;
- that warnings-only cases are distinguishable from clean.

## 9. Both arms: the same protocol, different content

| | PRISM arm | Baseline arm |
|---|---|---|
| When | after every `edit_file` | after every `edit_file` |
| Boolean, clean line, warnings-only, the four sections, 800-token cap, `[check: unavailable]` | identical | identical |
| What computes the facts | PRISM: the parse tree, the resolved call graph, the break check against `HEAD`, callee origin (V1a) | text only: `git diff HEAD`, regular expressions and `grep`; no parser, no index (below) |

**The baseline's grep-derived facts** (a fixed algorithm, written into the pre-registration):
- **Changed functions:** the functions whose lines the diff against `HEAD` touches. Their definition lines are found by
  pattern (`def name(`, `function name(`, `name = (…) =>`, `name(…) {`).
- **Downstream:** for each changed function whose parameter list changed (the definition line, before and after),
  `grep -nw 'name('` over the repository's source. Each call site whose comma-counted argument number does not fit
  the new list is reported: "`file:line` passes N arguments; `name` now takes M".
- **Upstream internal:** in the changed lines, each `name(` with a definition elsewhere in the repository (by the
  same patterns), comma-counted against that definition's parameters.
- **Upstream external:** a called name bound by an import line from a module that is not a repository path. Text
  cannot verify an external signature, so this section reports only the binding ("`get` comes from the external
  `requests`: do NOT edit it"), and only when the call's line also changed.
- **Undefined:** a called name in the changed lines with no definition and no import line in the repository.
  Builtins come from a fixed list per language.
- **Warnings:** a function whose last textual call site disappeared in the diff.

**The parity this keeps:** both arms receive the same kind of feedback, at the same moments, in the same format and
budget. What differs is precision, which is what R4 measures: parsed and resolved facts against text-matched facts.
