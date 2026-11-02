# R1 results: is it PRISM's context, or just a longer prompt?

The design is in `harness/experiments/r1/README.md`. The analysis rules are Amendment R1-A (`reports/suite/REPORT.md`,
commit `feac1f4`, made before any R1 answer was scored). Numbers come from `RESULTS.json`
(`python -m harness.experiments.r1.analyze`).

**Setup**
- 3,378 new model calls, with no errors, plus the suite's 792 seed-42 answers: 4,170 answers in all.
- Seeds 42, 1001 and 2002, all complete.
- The cell was pinned to `b0ebcc9`.
- Each task's value is the mean over the three seeds.
- Primary tests exclude tasks where either version was cut off. The all-tasks result is shown beside each.

## Scope: every result here is single-shot

**Every task type tested so far was tested single-shot:** one prompt, one answer, no tools, no iteration. That covers
the suite's six types and R1's three (forward, blast, newfn). The results, labels and rules in this report are valid
for that framing and are not changed by what follows.

| type | its natural structure | tested | workflow equivalent |
|---|---|---|---|
| forward | single-shot fits: find and update the callers of one change | suite, R1 | none needed; R3 keeps it as a secondary |
| concepts | single-shot fits: answer a question | suite | none |
| blast | multi-step: change, run the tests, follow the failures | suite, R1 | R3 (primary) |
| newfn | multi-step: draft, run, iterate | suite, R1 | R3 (primary) |
| tests | multi-step: write, run, fix | suite (invalid: truncation) | R3, if included |
| bugs | multi-step: search, read, narrow down | suite | not scheduled [TBD team] |

**This is a limitation of the method that R1 revealed, not a finding R1 produced.** R1 does not show that PRISM
helps more, or less, in multi-step work. It shows what PRISM's context does in one shot. R3 tests the multi-step
form.

- Blast2 (`harness/experiments/blast2/README.md`) is a single-shot **component test** of blast's retrieval. It is
  not the blast verdict.
- R2 stays the forward bundle ablation, single-shot, as pre-registered in Amendment R1-A, A6.

## Outcome (pre-registered labels)

| type | padded baseline | baseline | PRISM | difference [95% CI] | Holm p | label |
|---|---|---|---|---|---|---|
| forward: changed callers found | FILE_GREPPAD | 0.471 | **0.653** | +0.181 [+0.124, +0.240] | 1.5e-7 | **pass** |
| blast: traced-caller recall | GREP_PAD | 0.195 | 0.292 | +0.097 [+0.026, +0.170] | 0.059 | **not shown** |
| newfn: helper reuse | FILE_BM25PAD | 0.709 | 0.703 | −0.007 [−0.046, +0.033] | 0.99 | **not shown** |

- **forward passes.** PRISM's change context beats an equal-size prompt of grep lines plus the callers' code.
  That padded prompt held 97% of the callers' code (Amendment R1-A, A0). So the gain comes from how PRISM organises
  the callers, not from showing callers the baseline could not see.
- **blast is not shown.** The difference is above the floor and its interval is above 0, but it misses
  significance after Holm (raw p = 0.030, adjusted 0.059). By the committed rule, blast's suite win counts as not
  shown against an equal-size baseline.
- **newfn is not shown.** The equal-size keyword-search prompt gives the same helper reuse as PRISM. The suite's
  newfn gain (0.59 → 0.71) is explained by prompt size.

## forward

*Scope: single-shot. Natural structure matches; no workflow equivalent needed (R3 keeps forward as a secondary).*

| | FILE | FILE_BM25PAD | FILE_GREPPAD | PRISM |
|---|---|---|---|---|
| callers found per task | 0.289 | 0.325 | 0.468 | **0.655** |
| tasks with every caller found | 25% | 30% | 43% | **64%** |
| wrong functions named per task | 0.96 | 2.10 | 1.45 | **0.51** |

- **Every seed:** +0.22, +0.17 and +0.17, each with p < 3e-5.
- **Both languages:** Python +0.19 (0.59 → 0.78) and JavaScript +0.19 (0.26 → 0.45).
- **By tier** (PRISM against FILE_GREPPAD; secondary, not corrected for multiple tests):
  - easy (97 tasks): 0.43 → 0.60 (+0.17)
  - medium (64 tasks): 0.55 → 0.68 (+0.13, p = 0.016)
  - hard (29 tasks): 0.43 → 0.77 (+0.34)
- **Medium is where grep padding helps most:** FILE alone gets 0.03, grep padding 0.55. The callers are in other
  files but name the changed function.
- **Extra code with no structure makes the model name more wrong functions:** 2.10 with keyword-search padding, 1.45
  with grep padding. PRISM halves FILE's count (0.96 → 0.51).
- **Callers hidden from the grep prompt** (exploratory, 9 callers): PRISM finds 0.70, the grep version 0. Nine items
  give this no weight.
- **Validity:** 2 tasks excluded (cut-off answers 0.3% vs 0%). The all-tasks result is the same (+0.187).

## blast

*Scope: single-shot. Workflow equivalent: R3. Blast2 is a single-shot component test of the retrieval, not the
blast verdict.*

| | GREP | GREP_PAD | PRISM |
|---|---|---|---|
| traced-caller recall | 0.114 | 0.197 | **0.299** |
| direct callers | 0.41 | **0.73** | 0.64 |
| functions named | 6.8 | 8.7 | 20.4 |
| precision against traced callers (a lower bound) | 0.31 | **0.43** | 0.41 |
| recall within the first 10 names | 0.105 | 0.188 | 0.236 |

- **The difference is the same at every seed:** +0.10, p = 0.010, 0.021 and 0.021. Only Holm's correction across
  the three types puts it over 0.05.
- **At matched length** (k = the shorter answer): GREP_PAD 0.152, PRISM 0.193. The difference is +0.041
  [+0.005, +0.080], p = 0.35.
  - Within the first 5, 8 and 10 names, the differences are +0.01 to +0.04, none significant.
  - **Most of PRISM's recall advantage comes from naming more functions** (20 against 9), not from ranking better.
- **Direct callers favour the padded version** (0.73 against 0.64). It shows their code.
- **Exploratory (A3 allows this one claim, labelled exploratory):** on the 3,450 traced callers the padded prompt
  does not show, PRISM names 0.271, the padded version 0.103 (p = 4.6e-5). This is mainly the transitive callers:
  PRISM's context lists them, and grep cannot find them.
- **Validity:** 9 tasks excluded (5.3% vs 3.1% cut). With all 76 tasks the difference is +0.101, p = 0.016 before
  correction.

## newfn

*Scope: single-shot. Workflow equivalent: R3.*

| | FILE | FILE_BM25PAD | PRISM |
|---|---|---|---|
| helper reuse | 0.588 | 0.709 | 0.703 |
| invented calls per task | 0.21 | **0.21** | 0.29 |
| parses (strict) | 94.5% | 88.5% | 89.6% |
| parses, outer fence removed | 97.1% | 96.4% | 97.9% |

- **Keyword-search code at PRISM's size raises reuse exactly as much as PRISM** (+0.12 over FILE, p = 1e-5).
- ~~**Invented calls are PRISM's own cost:** the padded version keeps FILE's 0.21, PRISM has 0.29. The difference is
  +0.076 [−0.003, +0.174], so it is unresolved.~~ **Withdrawn** (post-hoc §5): most "invented" calls are scorer
  errors.
- **Helpers hidden from the padded prompt** (exploratory, 159 helpers): 0.59 against 0.60, no difference.
- No task was excluded; the same reuse holds for every seed and both languages.

## Repeatability

- Re-running 30 seed-42 prompts gave the identical answer 22 times: forward 7/10, blast 7/10, newfn 8/10.
- The model is not exactly deterministic on this setup, so the seed results measure run-to-run variation, not seed
  alone.
- The primaries hold at each seed separately.

## What this means (Amendment R1-A, A4)

> **forward passes:** on these 10 repositories, with this model, PRISM's change context is more useful per token
> than an equal-size prompt of grep results with the callers' code. The callers' code was already in that prompt,
> so this shows that PRISM's organisation helps (who depends on it, per-call-site facts). It does not show that
> PRISM finds callers that text search cannot.
>
> **blast and newfn:** no advantage over an equal-size baseline was shown.
> - For newfn, the suite's reuse gain is explained by prompt size. ("PRISM's extra invented calls remain" is
>   withdrawn: post-hoc §5.)
> - For blast, PRISM's advantage is in naming more of the transitive callers. That is reported as exploratory.

None of this shows that the results hold on other repositories or other models, or which section of the context
does the work. Those need held-out repositories, a second model, and the R2 ablation.

**What follows, by the committed rules:**
- R2 (section ablation) enters for **forward** only, the one type labelled `pass` or `below_floor`.
- PRISM's search ranking, the newfn helper list and the blast list are not re-tuned on these tasks.

## Post-hoc investigation of blast and newfn (not pre-registered)

Everything here uses R1's existing answers, with no new model calls. It is secondary to the pre-registered labels
above, which stand as computed. Where it changes how a result should be read, that is said explicitly.
- Scripts are in `harness/experiments/r1/investigate/`.
- Their outputs are in `reports/r1/investigation/`.
- Paired comparisons use the 67 blast tasks left after the truncation rule, and the mean over the three seeds
  unless a seed is named.

### 1. Test-file exclusion is symmetric

| what | test code | where |
|---|---|---|
| answer key: the tracer | excluded: only production frames are recorded; test and library frames between two production functions are skipped | `harness/experiments/suite/trace_edges.py:5-7, 42` |
| answer key: planning | excluded: the key and its direct callers keep production functions only | `harness/experiments/suite/blast.py:147, 165, 174` |
| PRISM's context | excluded: `prism_blast_radius(..., include_tests=False)` | `blast.py:166-167` |
| GREP's lines | excluded: grep runs over production files only | `blast.py:155, 175` |
| GREP_PAD's functions | excluded: production functions only (spans, BM25 results, nearby fill) | `harness/experiments/r1/pad.py:90, 106-107, 109` |
| scorer | names are not filtered. A test function can never match the production-only key, so it counts as a named function, never a found one. The same rule applies to every version. | `blast.py:212-223`; `run.py:111-117` |
| the answers, checked | GREP named 0 test functions of 1,556 names, GREP_PAD 0 of 1,983, PRISM 28 of 4,659 (0.6%) | `investigate/` |

**Verdict: symmetric.** No version is shown test code, the key has none, and the 28 test names in PRISM's answers can
only lower PRISM's precision. No re-scoring is needed.

**Valid scoring against the stated key ≠ the right key: 86% of key callers are 3+ hops away, the median anchor has 1 direct caller, and 24/76 anchors have no static callers. The key measures test coverage, not relevance. 70% of PRISM's names are real callers the key can't credit.**

### 2. What each blast version actually does: werkzeug-blast-002, anchor `wsgi._get_server`

**The answer key** is 6 traced callers:
- `Request.__init__` (1 hop), `Request.application` (2), `ProxyMiddleware.proxy_to` (3), and three WSGI `__call__`
  methods (4).
- Only `Request.__init__` is direct.
- The rest are reached through WSGI dispatch: one application calling the next through the server.

**PRISM's version** (`prism_blast_radius`):
1. Walks the static call graph upward from the anchor, up to 6 hops (`UPSTREAM_WALK_MAX_HOPS`), and lists every
   caller nearest first.
2. Then shows the callers' code, cut to the 6,000-token budget.
3. Here it lists 17 callers, hops 1 to 4.

The model's answer is that list, copied in order: 17 names. Two are in the key: `Request.__init__` and
`Response.__call__`. Most of the other 15 are real static callers the tests did not reach on this path. For example,
`wsgi._get_host` calls `_get_server` directly (`wsgi.py:135`), but it is not in the key. PRISM also cannot list
`ProxyMiddleware.proxy_to`: the graph has no edge through WSGI dispatch.

**GREP_PAD's version:**
1. Shows 4 grep lines mentioning `_get_server`.
2. Then the code of the one production function containing a call (`Request.__init__`).
3. Then the functions keyword search ranks highest for `_get_server`: functions about "server" and "host", including
   6 from the `examples/` folder. These are filled until the prompt matches PRISM's size (6,980 tokens).

The model names 6 functions: `Request.__init__` (in the key), then neighbours by name such as `Request.host`,
`MapAdapter.get_host` and `BaseWSGIServer.log_startup`.

**Are they naming the same kind of thing?** No.
- PRISM names static transitive callers.
- The padded version names the grep caller plus functions with similar names.
- The key holds runtime callers, 86% of them 3 or more hops away (§4).

This is the pattern across all tasks, as the classification shows (every name, all seeds):

| named function is | GREP | GREP_PAD | PRISM |
|---|---|---|---|
| a traced caller (in the key) | 19% | 28% | 26% |
| a real static caller, not traced | 8% | 14% | **70%** |
| an existing function that is not a caller | 31% | **46%** | 3% |
| not found in the repository by name | 43% | 12% | 0.6% |

- **97% of PRISM's names are real callers** (traced or static).
- **58% of the padded version's names are not callers at all.**
- Precision against the traced key (0.41 against 0.43) hides this: the key cannot credit a caller the tests did not
  run.
- "Not found by name" also includes names written in a form the matcher cannot resolve, so it is an upper bound on
  invented names.

### 3. Ranking or volume

| measure (traced key) | seed 42 | seed 1001 | seed 2002 | all seeds | p (all seeds) |
|---|---|---|---|---|---|
| precision of the first 9 names (P@9): GREP_PAD vs PRISM | 0.441 / 0.463 | 0.451 / 0.463 | 0.448 / 0.461 | 0.447 / 0.462 | 0.75 |
| recall within the first 9 names | 0.185 / 0.214 | 0.190 / 0.214 | 0.180 / 0.214 | 0.185 / 0.214 | 0.54 |
| recall within the first 20 names (R@20) | 0.193 / 0.246 | 0.198 / 0.246 | 0.187 / 0.246 | 0.193 / 0.246 | 0.28 |
| recall over all names | 0.193 / 0.293 | 0.199 / 0.290 | 0.187 / 0.291 | 0.193 / 0.291 | 0.028 |

**The two versions name about 9 and 21 functions on average, but the medians are 6.5 and 3.5.** A few tasks with
very long PRISM lists carry the mean.

**Audit of PRISM's names beyond the padded answer's length** (every task and seed: 3,462 names, classified by rule):

| | share |
|---|---|
| traced callers | 23% |
| real static callers, not traced | 77% |
| existing non-callers | 0.5% |
| not found | 0.2% |

**Verdict: volume, not spam.**
- Within the first 9 or 20 names, PRISM does not rank traced callers better.
- Its recall advantage comes from naming more functions.
- 99% of those extra functions are real callers. Most are callers the traced key cannot credit.
- The matched-length cut was not wrong, but under this key it can only show a tie.

**For future rounds:** blast needs a relevance-grade key (§4). Any length-matched measure must be scored against it,
not against the trace alone.

### 4. The blast key measures test coverage, not relevance

The question's comparison ("callers changed in the same commit") does not apply. Blast anchors are not taken from
commits: each repository was traced at one fixed commit, and the anchors were drawn from what its tests ran. What the
key does show:

| | blast key (traced) | forward key (developer-verified) |
|---|---|---|
| callers per anchor | median 13, mean 48, max 199 | median 1 changed caller |
| direct callers | median 1 | — |
| callers 3 or more hops away | 86% | — |
| largest | tornado `BaseAsyncIOLoop.add_callback` (199) and template code generation (159, 160) | — |

- Of the key's callers, a median of 15% are on PRISM's static list.
- Of PRISM's static callers, a median of 48% are in the key.
- **24 of the 76 anchors have no static callers at all.** They are reached only through dynamic dispatch (event
  loops, WSGI, callbacks).

**Verdict: coverage-grade.**
- The key is "every production frame above the anchor on some test's call stack". It is dominated by deep frames,
  and depends on what the tests happen to run.
- It is a valid record of what can reach the anchor at runtime, but not of what a developer would need to look at.
- **Blast cannot pass against a padded baseline under this key unless the static and runtime pictures happen to
  agree.**

**For future rounds:** a relevance-grade key, either
- the callers a developer changed together with the anchor, mined from history as forward does; or
- the union of traced and static callers, scored with the traced-only result reported beside it.

### 5. newfn: what each version shows, and the invented calls

**What each version does: tornado-newfn-006** (write `gen.coroutine`; the key is 8 helpers the developer's body
calls).

- **PRISM:**
  1. Ranks helpers by its search over the name, signature and purpose, then by nearness (same class, file,
     directory).
  2. Lists the top 12 with signature and digest only, not code. For example: `return_future` (calls
     `ArgReplacer`, defines `wrapper`), `gen.engine`, `wrapper`, `handle_exception`.
  3. Then the group's conventions, and the code of the two closest functions.
- **FILE_BM25PAD:**
  1. Ranks functions by plain BM25 over function code for the signature and purpose.
  2. Shows them with full code (up to 60 lines each), 7 of them, to 4,684 tokens.
  3. Five of the seven are on PRISM's list too: `return_future`, `wrapper`, `_auth_return_future`, `send_error`,
     `IOLoop.stop`.
- **What each prompt shows of the developer's helpers:** PRISM `handle_exception` and `wrapper`; the padded version
  those two and `add_future`.
- **Reuse at seed 42:** 0.50 for PRISM and 0.38 for the padded version.

**Correction:** ~~"the same 199 of 358 helpers"~~. Both versions show 199 of the developer's 358 helpers: the same
count, a different set.
- 167 are shown by both;
- 32 only by PRISM, 32 only by the padded version;
- 127 by neither.

Across tasks, a median of 42% of PRISM's helper list is also in the padded block. **The two versions largely show the
same functions, PRISM as digests and the padded version as code.** The tie in reuse is consistent with that.

**The invented calls are mostly scorer errors.** All 271 "invented" calls (every version, all seeds), checked against
the answer code:

| what the "invented" call really is | FILE | FILE_BM25PAD | PRISM |
|---|---|---|---|
| a call to one of the function's own parameters (`func(...)`, `cls(...)`) | 34 | 30 | 37 |
| a call to a local variable | 11 | 17 | 15 |
| a method on an expression, misread as a bare call (`"…".join(`) | 15 | 13 | **26** |
| a library function the file does not import (`cast`) | 2 | 1 | 6 |
| `self.x` not defined on the class (inherited, or invented) | 7 | 8 | 10 |
| left: candidates for a real invention | 11 | 12 | 16 |
| **total** | 80 | 81 | 110 |

- **At least 71% of the "invented" calls are not inventions.** These are the first four rows: calls the scorer
  cannot resolve.
- PRISM's excess over the padded version (110 against 81) is mostly expression-method misreads (+13) and library
  calls (+5). PRISM's answers use more chained calls.
- **Of 12 candidates sampled from PRISM** (5 distinct names), 4 names exist in the repository:
  - `websocket_mask`, a module-level alias in aiohttp's `_websocket/helpers.py`;
  - `self._socket_action`, an instance attribute holding a callable (tornado `httpclient.py:135`);
  - `self.get`, inherited, since werkzeug's `Authorization` subclasses `dict`;
  - ~~`a`, a variable~~ **corrected:** `a` is the word "a" in a comment ("a (`user`, …) tuple"), which the old scorer
    read as a call.
- One is genuinely invented: `parse_ansi` in rich (rich has `Text.from_ansi`, so this is a pattern copy, cause (a)).
- So nearly all the invented calls are scorer errors (calls to the code's own parameters, local variables, methods
  on expressions, aliases, attributes, inherited methods), not names the model made up.
- **No sampled case came from digest text** (cause (c)). Cause (b), real names outside the shown list, is what most of
  the remaining candidates turned out to be.

**Correction to the results above:** "PRISM's extra invented calls are its own cost" is withdrawn. The measure cannot
support it. The same applies to the suite's newfn cost (0.19 → 0.30) and to A3's `fails_invented_calls` rule, which
rely on this scorer.

**For future rounds (a scorer fix, before any model run):** invented calls exclude calls to the function's
parameters and local variables, method calls on any expression, module-level aliases and instance attributes
assigned a callable, inherited methods (including those of built-in base classes), and names imported anywhere in the
repository.

**Scorer v2 (done):** `harness/experiments/suite/invented.py`, used by `newfn.score_task`.
- The rules are in its docstring. The repository names for the existing tasks are in
  `harness/experiments/suite/repos/*/newfn_names.json`; new task sets carry them as `repo_names`.
- Helper reuse (`calls()`) is unchanged.
- The regression tests are in `tests/test_newfn_invented.py`: the four real-code cases are not flagged, and
  `parse_ansi` still is.
- Re-scored on all 1,152 saved answers (`reports/suite/newfn_invented_v2.json`). Only invented calls are recomputed;
  no pre-registered result changes.

| | FILE | FILE_BM25PAD | PRISM |
|---|---|---|---|
| calls flagged, v1 | 80 | 81 | 110 |
| calls flagged, v2 | 4 | 3 | 4 |
| per task, suite seed 42 (v1 → v2) | 0.19 → **0.008** | — | 0.30 → **0.008** |
| per task, R1 three seeds (v1 → v2) | 0.21 → 0.010 | 0.21 → **0.008** | 0.29 → **0.010** |

- PRISM vs FILE_BM25PAD under v2: +0.003 [−0.016, +0.023], p = 0.66.
- PRISM vs FILE at suite seed 42 under v2: +0.000, p = 1.
- **With a correct scorer there is no invented-call cost:** about 1 invented call per 100 answers in every version.
- Of v1's 271 flags, v2 clears:
  - 134 as names bound in the answer (parameters, locals, its own outside imports);
  - 74 as not bare calls once parsed (methods on expressions, comments, strings);
  - 27 as module-level names; 18 as inherited; 6 as imported elsewhere; 3 as attributes.
- **9 remain flagged:** `parse_ansi`, aiohttp `_create_direct_connection` and `_warn_about_tls_in_tls` called without
  `self.`, rich `cls` and `accumulate`, werkzeug `wraps` (not imported anywhere at that revision) and `responder`.

### 6. Callers hidden from the padded prompt: robust

The traced callers GREP_PAD's prompt does not show cover 3,450 callers in 67 tasks.

| | GREP_PAD | PRISM | difference [95% CI] | p |
|---|---|---|---|---|
| seed 42 | 0.104 | 0.273 | +0.169 [+0.091, +0.247] | 5.7e-5 |
| seed 1001 | 0.109 | 0.270 | +0.161 [+0.085, +0.237] | 9.0e-5 |
| seed 2002 | 0.095 | 0.271 | +0.176 [+0.105, +0.251] | 2.7e-5 |
| all seeds | 0.103 | 0.271 | +0.168 [+0.094, +0.244] | 4.6e-5 |
| without the top 2 tasks | 0.106 | 0.251 | +0.145 [+0.075, +0.217] | 1.3e-4 |
| without the top 5 tasks | 0.108 | 0.223 | +0.115 [+0.051, +0.182] | 6.5e-4 |

- PRISM is better on 32 tasks, equal on 26, worse on 9.
- **The hidden callers are mostly transitive:** 3,120 transitive and 86 direct.
- Of the hidden callers each version found (seed-pooled), PRISM found 796 transitive and 141 direct, the padded
  version 65 and 83.

**Verdict:** large, stable at every seed, not carried by a few tasks, and about transitive callers. It is the real
blast finding. It stays exploratory in R1 because it was not a primary. **It is the candidate primary for blast in the
next round,** on fresh tasks, with the relevance-grade key from §4.

### 7. Power: blast is underpowered, not null

- The per-task difference has mean +0.097 and standard deviation 0.307 (67 tasks).
- Holm's correction put blast second of three tests, so it needed raw p < 0.025.
- **With 80% power to detect +0.10:**
  - the normal approximation (paired, Wilcoxon efficiency 0.864) needs about 104 tasks, or 114 at p < 0.0167;
  - resampling the observed differences, power is 0.54 at 76 tasks, 0.68 at 100, 0.87 at 150, 0.94 at 200 and
    0.99 at 300.
- **R1's blast test had about a 54% chance of passing if the true effect is +0.10.**
- This power is computed at the observed effect, so it is optimistic if the true effect is smaller.

**For future rounds:** at least 150 blast tasks, or a primary with less variance (the hidden-caller measure's
interval is tighter).

### 8. Verdicts after the investigation

**forward:** accepted. Nothing here touches it.

**blast:** `not shown` is the correct pre-registered label. The test-file filter is symmetric and the scoring is
valid. **Valid scoring against the stated key ≠ the right key: 86% of key callers are 3+ hops away, the median anchor has 1 direct caller, and 24/76 anchors have no static callers. The key measures test coverage, not relevance. 70% of PRISM's names are real callers the key can't credit.** Its reading changes, though: the result is **underpowered** (54% power), and judged against a
**coverage-grade key** that cannot credit the 70% of PRISM's names that are real but untraced static callers. Within
the first 9 or 20 names, PRISM does not rank traced callers better; its advantage is naming more real callers. On
callers the padded prompt did not show, mostly transitive ones, PRISM is clearly ahead: +0.17, stable and robust. So
the blast claim is unresolved, not refuted. The next round should use a relevance-grade key, at least 150 tasks, and
the hidden-caller measure as primary.

**newfn:** `not shown` for reuse is correct and stands. Both versions largely show the same functions (about half of
PRISM's list is in the padded block), as digests or as code, and reuse ties at every seed and in both languages. The
investigation **withdraws the invented-call cost**: at least 71% of the "invented" calls are not inventions, and
PRISM's excess comes mostly from method calls on expressions that the scorer misreads. Genuine inventions are rare,
and the one found is a pattern copy of a real name. The scorer needs the fix in §5 before invented calls can be used
as a side condition again.

## Before R3: three analyses (post hoc; not pre-registered results)

Scripts: `harness/experiments/r3pre/`. These inform R3's design (`harness/experiments/r3/README.md`). They change no
R1 label.

### A1. Does forward's advantage grow with the number of callers? (no model calls)

R1 forward: callers found, PRISM vs FILE_GREPPAD, by how many callers the developer changed. The two cut-off tasks
are excluded.

| callers | tasks | seed 42 | seed 1001 | seed 2002 | all seeds: pad → PRISM | difference [95% CI] | p |
|---|---|---|---|---|---|---|---|
| 1 | 136 | +0.18 | +0.12 | +0.14 | 0.50 → 0.64 | +0.15 [+0.08, +0.22] | 7.3e-5 |
| 2 | 28 | +0.21 | +0.18 | +0.18 | 0.43 → 0.62 | +0.19 [+0.07, +0.31] | 0.013 |
| 3-4 | 18 | +0.43 | +0.39 | +0.24 | 0.31 → 0.66 | +0.35 [+0.09, +0.59] | 0.021 |
| 5+ | 8 | +0.26 | +0.38 | +0.41 | 0.58 → 0.93 | +0.35 [+0.13, +0.58] | 0.062 |
| all | 190 | +0.21 | +0.16 | +0.17 | 0.47 → 0.65 | +0.18 [+0.12, +0.24] | 4.8e-8 |

The rank correlation between a task's number of callers and its gap is ρ = 0.22, p = 0.003.

**Verdict:** the advantage grows with the number of callers, about doubling from 1 caller (+0.15) to 3 or more
(+0.35), and the direction holds at every seed. It is post hoc. The two deepest strata hold 26 tasks, and caller
count overlaps with R1's tier, so this is a preview of R3's scaling prediction, not a test of it.

### A2. The digest before and after the developer's change (no model calls)

For 20 R1 forward tasks (seed 5501, from the 9 repositories with full clones), the anchor and every changed caller
were indexed at the parent and at the commit, and their digests diffed. A fact is its kind, text and conditions;
line numbers are ignored.

| what the anchor's digest delta shows | tasks |
|---|---|
| a behaviour change: a raise, catch, state change or dynamic call added or removed, or a new condition | 14 |
| a textual change only: the same facts with renamed or re-written arguments | 5 |
| no change at all: a signature-only change | 1 |

- **Facts changed, all 20 anchors:** assign 51, call 40, return 36, state 25, call targets 22, dynamic 16, raise 13,
  catch 10.
- 16 of 20 anchors changed their signature.
- 24 of the 29 changed callers show a digest change at their call to the anchor.

**Worked examples:**
1. **tornado-forward-016, `_finish_read(self, size, streaming)` → `(self, size)`:** the anchor's digest does not
   change, because **the digest has no signature field**. The caller's digest shows it:
   `self._finish_read(pos, False)` → `self._finish_read(pos)`. That is the same information as the line diff.
2. **marshmallow-forward-012, `load(..., unknown=None)`:** return fact
   `self._do_load(data, many, partial=partial, postprocess=True)` → `…, unknown=unknown, …`. The caller
   `Nested._deserialize` changes `self.schema.load(value)` → `load(value, unknown=self.unknown)`. Textual: the digest
   restates the diff.
3. **mocha-forward-006, `formattedImport(file)` → `(file, esmDecorator = forward)`:** the digest adds "calls the
   function passed as argument `esmDecorator`" (a new dynamic call), under the condition `path.isAbsolute(file)`, and
   both returns change. The caller `requireOrImport` passes the new argument. A behaviour change that a reader of the
   caller would not see in the caller's own diff.
4. **werkzeug-forward-019, `get_current_traceback(..., skip=0)`:** a new loop `for each x in xrange(skip)` skips
   frames. The behaviour depends on a new parameter, visible as a new condition.
5. **Raise obligations:** 5 anchors gained a `raise`; in only 1 of those tasks did the changed caller add a `catch`.
   A rule "a new raise means the caller must catch" would be wrong 4 times out of 5, because errors usually
   propagate.

**Verdict:**
- **As a change summary, the digest is real:** 14 of 20 deltas state a behaviour change compactly, with its
  conditions, for callers in other files.
- **As a verifier of edits, it is not shown:**
  - it is blind to signature changes, the most common forward change (16 of 20);
  - at call sites it carries the same information as the diff;
  - its one obligation-like signal (raise → catch) would mostly raise false alarms.
- **Whether a digest check catches a wrong edit that a diff misses cannot be answered without running edits through
  a check loop.** R3 must measure it: report, per run, whether a digest comparison of the agent's edited callers
  flagged the runs that failed their tests.
- **Fix before relying on it:** add the parameter list to the digest, so a signature change is a fact.

### A3. Planning a multi-file change from digests alone (20 model calls)

The rubric was committed before any call (`harness/experiments/r3pre/README.md`, commit `390ecd0`). The 20 tasks are
frozen in `harness/experiments/r3pre/tasks/planner.json`. **Pending:** the 20 calls run on Kaggle with the suite's
model. The result and its verdict follow here, scored by the rubric.
