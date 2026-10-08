# Combined verification, implementation plan and ablation design

Analysis and plan only. No repository code was modified and nothing was run on Kaggle. Everything below
is offline: the M4 bundles (Arm 5 Turn-1 responses, seeds 42–44), regenerated M4 manifests, and an
offline replay of the real Arm 5 code path.

Scripts and raw outputs are in the session scratchpad under `chg/`: `cab_v123.py`, `cab_v4.py`,
`mf_extract_v4.py`, `cab_replay.py`, `cab_tables.py`, plus the corresponding `*.json` / `*.jsonl`.

## Summary

* **The prior findings hold, with two small corrections.** In the M4 T5 manifests, `caller` rows are 77%
  gold, `callee` 1% and `transitive` 0%. The M4 model picked 57% of gold callers, 73% of non-gold callers,
  64% of callee rows (prior report: 65%) and 20% of transitive rows (prior: 28%; it had counted only rows
  inside the 3-hop neighbourhood). The 73% vs 57% caller gap is not significant (Wilson intervals overlap).
  The finding that matters is that the model does not prefer callers over callees on T5 (60% vs 64%). On
  T2 it plainly follows the role column: callers picked 2%, gold callees 94%.
* **The prompt can fix this without a schema change.** The direction fact is already in the `role` column,
  and on T2 the model already acts on it.
* **Body size remains the clearest extra signal beyond hop + direction, on the corrected pool too.** In the
  Design C lists, body tokens add +0.041 AUC and body lines +0.039; parameter count +0.035, Design C
  position +0.028 and type annotations +0.024 also clear this pool's noise floor (max +0.007). Caller
  weight sits at the floor (+0.008); fan-in/out, `is_test`, docstrings and decorators do not clear it. Among upstream rows only, body lines
  (AUC 0.79), parameter count (0.73) and hop (0.69, lower = more gold) separate gold callers from the rest.
  Hop is invisible to the model in Design C manifests (every upstream row says `caller`), but gold falls
  from 51–69% at hops 1–3 to 5% at hop 5 and 0% at hop 6.
* **Offline replay** reproduces M4 exactly: 96/96 T5 and 267/267 T2 cells, identical delivered symbol sets.
  * Design C raises the T5 gold reachable in the delivered context from 0.303 to 0.888.
  * A deterministic "select every `caller` row" rule (the no-LLM control C0) reaches that ceiling: 0.888,
    selection precision 0.51.
  * The M4 model's picks reach 0.174 on the M4 manifest.
  * The 13,000-token budget never binds on T5 (0 over-budget cells; mean 4.2k tokens delivered). So
    metadata cannot raise T5 context coverage above what "select all callers" gives. It can only improve
    selection precision, and through it the answer.
* **Regression found: T2 on Express, caused by my earlier graph change.** Discounting best-guess edges in
  the causal graph pushed five Express T2 gold stages (`app.set`, `app.enable`, `app.enabled`, `res.get`)
  past the manifest's weighted-distance cutoff (3.33 > 3.0). They are only 2–3 structural hops from the
  seed. This lowers T2 delivered coverage from 0.975 to 0.950 pooled, and from 0.972 to 0.792 on Express.
  Proposed universal fix (needs approval, Part 2-pre): admit downstream candidates by structural hop count
  and use the weighted distance only for ranking.
* **Minimum Kaggle ablation** (Part 5): T5 only, Arm 5 only, four configs × 96 cells (G0K, C1, C2, C3),
  about 3–3.5 h of cells in total, plus C0K (rule selector + LLM answer) as the ceiling arm. Metadata enters
  only as C3 (M1 + P1). M2 adds no field above noise beyond M1, so C4 and C6 are dropped; C5 is optional.

---

## Part 1 — Verification (V1–V6)

Data: the 32 T5 and 89 T2 tasks M4 ran. Manifests regenerated with the pre-change PRISM tree; all 121 match
the candidate counts recorded in the M4 bundles. Selections come from the Arm 5 bundles for seeds 42, 43
and 44: every manifest row is counted once per seed run. 95% intervals are Wilson score intervals.

### V1–V3

**T5_blast_radius — V1 gold rate by role (rows, gold, rate [Wilson 95% CI])**

| role | fastapi | django | express | trpc | pooled |
|---|---|---|---|---|---|
| caller | 13/15 = 87% [62%, 96%] | 14/24 = 58% [39%, 76%] | 1/2 = 50% [10%, 90%] | 25/28 = 89% [73%, 96%] | 53/69 = 77% [66%, 85%] |
| callee | 1/25 = 4% [1%, 20%] | 0/29 = 0% [0%, 12%] | 0/8 = 0% [0%, 32%] | 0/38 = 0% [0%, 9%] | 1/100 = 1% [0%, 5%] |
| transitive | 0/41 = 0% [0%, 9%] | 0/88 = 0% [0%, 4%] | 0/7 = 0% [0%, 35%] | 0/32 = 0% [0%, 11%] | 0/168 = 0% [0%, 2%] |

**T5_blast_radius — V2 share of manifest rows the M4 model selected (row × seed-run)**

| rows | fastapi | django | express | trpc | pooled |
|---|---|---|---|---|---|
| gold callers | 29/39 = 74% [59%, 85%] | 26/42 = 62% [47%, 75%] | 3/3 = 100% [44%, 100%] | 32/75 = 43% [32%, 54%] | 90/159 = 57% [49%, 64%] |
| non-gold callers | 5/6 = 83% [44%, 97%] | 24/30 = 80% [63%, 90%] | 0/3 = 0% [0%, 56%] | 6/9 = 67% [35%, 88%] | 35/48 = 73% [59%, 83%] |
| callee rows | 57/75 = 76% [65%, 84%] | 18/87 = 21% [14%, 30%] | 24/24 = 100% [86%, 100%] | 92/114 = 81% [72%, 87%] | 191/300 = 64% [58%, 69%] |
| transitive rows | 45/123 = 37% [29%, 45%] | 18/264 = 7% [4%, 10%] | 9/21 = 43% [24%, 64%] | 29/96 = 30% [22%, 40%] | 101/504 = 20% [17%, 24%] |
| all gold rows | 31/42 = 74% [59%, 85%] | 26/42 = 62% [47%, 75%] | 3/3 = 100% [44%, 100%] | 32/75 = 43% [32%, 54%] | 92/162 = 57% [49%, 64%] |

**T5_blast_radius — V3 deterministic selectors on the M4 manifest (recall of gold in manifest / precision; picks)**

| selector | fastapi | django | express | trpc | pooled |
|---|---|---|---|---|---|
| all caller rows | 0.93 / 0.87 (15) | 1.00 / 0.58 (24) | 1.00 / 0.50 (2) | 1.00 / 0.89 (28) | 0.98 / 0.77 (69) |
| caller + transitive rows | 0.93 / 0.23 (56) | 1.00 / 0.12 (112) | 1.00 / 0.11 (9) | 1.00 / 0.42 (60) | 0.98 / 0.22 (237) |
| caller + callee rows | 1.00 / 0.35 (40) | 1.00 / 0.26 (53) | 1.00 / 0.10 (10) | 1.00 / 0.38 (66) | 1.00 / 0.32 (169) |
| every row | 1.00 / 0.17 (81) | 1.00 / 0.10 (141) | 1.00 / 0.06 (17) | 1.00 / 0.26 (98) | 1.00 / 0.16 (337) |
| M4 model, all requested names (3 seeds) | 31/96 hits, P 0.16 | 26/135 hits, P 0.30 | 3/15 hits, P 0.08 | 32/288 hits, P 0.17 | 92/534 hits, P 0.18 |

**T2_localization — V1 gold rate by role (rows, gold, rate [Wilson 95% CI])**

| role | fastapi | django | express | trpc | pooled |
|---|---|---|---|---|---|
| caller | 1/27 = 4% [1%, 18%] | 0/38 = 0% [0%, 9%] | 2/11 = 18% [5%, 48%] | 0/30 = 0% [0%, 11%] | 3/106 = 3% [1%, 8%] |
| callee | 32/77 = 42% [31%, 53%] | 39/60 = 65% [52%, 76%] | 14/42 = 33% [21%, 48%] | 55/63 = 87% [77%, 93%] | 140/242 = 58% [52%, 64%] |
| transitive | 1/126 = 1% [0%, 4%] | 15/198 = 8% [5%, 12%] | 8/87 = 9% [5%, 17%] | 7/86 = 8% [4%, 16%] | 31/497 = 6% [4%, 9%] |

**T2_localization — V2 share of manifest rows the M4 model selected (row × seed-run)**

| rows | fastapi | django | express | trpc | pooled |
|---|---|---|---|---|---|
| gold callers | 3/3 = 100% [44%, 100%] | – | 6/6 = 100% [61%, 100%] | – | 9/9 = 100% [70%, 100%] |
| non-gold callers | 0/78 = 0% [0%, 5%] | 0/114 = 0% [0%, 3%] | 2/27 = 7% [2%, 23%] | 4/90 = 4% [2%, 11%] | 6/309 = 2% [1%, 4%] |
| callee rows | 156/231 = 68% [61%, 73%] | 125/180 = 69% [62%, 76%] | 54/126 = 43% [35%, 52%] | 163/189 = 86% [81%, 90%] | 498/726 = 69% [65%, 72%] |
| transitive rows | 147/378 = 39% [34%, 44%] | 125/594 = 21% [18%, 24%] | 67/261 = 26% [21%, 31%] | 126/258 = 49% [43%, 55%] | 465/1491 = 31% [29%, 34%] |
| all gold rows | 99/102 = 97% [92%, 99%] | 142/162 = 88% [82%, 92%] | 66/72 = 92% [83%, 96%] | 176/186 = 95% [90%, 97%] | 483/522 = 92% [90%, 94%] |

**T2_localization — V3 deterministic selectors on the M4 manifest (recall of gold in manifest / precision; picks)**

| selector | fastapi | django | express | trpc | pooled |
|---|---|---|---|---|---|
| all caller rows | 0.03 / 0.04 (27) | 0.00 / 0.00 (38) | 0.08 / 0.18 (11) | 0.00 / 0.00 (30) | 0.02 / 0.03 (106) |
| caller + transitive rows | 0.06 / 0.01 (153) | 0.28 / 0.06 (236) | 0.42 / 0.10 (98) | 0.11 / 0.06 (116) | 0.20 / 0.06 (603) |
| caller + callee rows | 0.97 / 0.32 (104) | 0.72 / 0.40 (98) | 0.67 / 0.30 (53) | 0.89 / 0.59 (93) | 0.82 / 0.41 (348) |
| every row | 1.00 / 0.15 (230) | 1.00 / 0.18 (296) | 1.00 / 0.17 (140) | 1.00 / 0.35 (179) | 1.00 / 0.21 (845) |
| M4 model, all requested names (3 seeds) | 99/102 hits, P 0.26 | 142/162 hits, P 0.47 | 66/72 hits, P 0.40 | 176/186 hits, P 0.57 | 483/522 hits, P 0.42 |

Reading V1–V3:

* **V1 confirmed.** `caller` 77% gold [66%, 85%]; `callee` 1% [0%, 5%]; `transitive` 0% [0%, 2%]. Django is
  the weakest corpus for callers (58%; 24 rows over 8 tasks, the M4 cap of 3 direct callers per seed).
* **V2 confirmed with the corrections above.** Per corpus the pattern differs:
  * Django: the model picks callers (69%) more than callees (21%).
  * fastapi, express and trpc: it picks callees at 76–100%, at least as often as callers.
  * trpc gold callers are picked only 43% of the time.
* **V3 confirmed.** "All caller rows" recovers 53 of 54 gold in the M4 manifests at precision 0.77. Adding
  transitive rows keeps recall (0.98) and drops precision to 0.22. Every rule here is capped by reach: only
  30% of T5 gold is in an M4 manifest at all.

### V4 — field prediction on the clean pool

Pool: T5 = the actual Design C candidate list per task (fixed graph, 13,000-token budget, 559 rows,
158 gold, 32 tasks). T2 = the actual default manifest per task (777 rows, 171 gold, 84 tasks).

Measures:
* within-seed single-field AUC;
* AUC inside each direction × hop stratum;
* conditional mutual information with gold given direction × hop (bits; share of H(gold) in brackets);
* increment of a leave-one-task-out logistic model, hop + direction + field over hop + direction.

Noise floor for the increment: random fields, 8 draws: max +0.007 (T5), +0.016 (T2).
Base AUC, hop + direction: 0.889 (T5), 0.889 (T2).

| field | T5: increment / stratum AUC / single AUC / cond. MI | T2: same | above noise? |
|---|---|---|---|
| body tokens | +0.041 / 0.839 / 0.65 / 0.0742 (0.0864) | -0.007 / 0.546 / 0.545 / 0.0268 (0.0352) | T5 |
| body lines | +0.039 / 0.829 / 0.653 / 0.1078 (0.1255) | -0.003 / 0.587 / 0.535 / 0.0337 (0.0443) | T5 |
| parameter count | +0.035 / 0.664 / 0.585 / 0.098 (0.1105) | +0.033 / 0.577 / 0.561 / 0.0117 (0.0147) | T5, T2 |
| Design C rank | +0.028 / 0.407 / 0.326 / 0.1424 (0.1657) | – | T5 |
| has type annotations | +0.024 / 0.614 / 0.544 / 0.1335 (0.1554) | +0.024 / 0.584 / 0.544 / 0.0199 (0.0262) | T5, T2 |
| axis output | +0.018 / 0.564 / 0.674 / 0.1307 (0.1522) | +0.041 / 0.61 / 0.513 / 0.0682 (0.0897) | T5, T2 |
| caller weight (chain) | +0.008 / 0.562 / 0.657 / 0.1096 (0.1097) | -0.001 / – / 0.5 / 0.0176 (0.232) | at the T5 noise floor (+0.008 vs +0.007) |
| caller weight (hop 1) | -0.016 / 0.624 / 0.636 / 0.091 (0.091) | -0.004 / – / 0.5 / 0.0307 (0.1714) | no |
| binds return | -0.029 / 0.601 / 0.591 / 0.029 (0.029) | -0.006 / – / 0.5 / 0.0 (0.0) | no |
| call sites | -0.005 / 0.483 / 0.53 / 0.019 (0.0216) | +0.023 / 0.504 / 0.506 / 0.0093 (0.0118) | T2 |
| has docstring | -0.045 / 0.716 / 0.56 / 0.0109 (0.0127) | -0.008 / 0.55 / 0.543 / 0.0068 (0.009) | no |
| fan in | -0.021 / 0.577 / 0.31 / 0.0403 (0.0469) | -0.004 / 0.485 / 0.596 / 0.0213 (0.028) | no |
| fan out | -0.027 / 0.708 / 0.81 / 0.0228 (0.0266) | +0.006 / 0.628 / 0.536 / 0.0276 (0.0363) | no |
| is dunder | +0.002 / 0.503 / 0.515 / 0.0061 (0.0071) | +0.01 / 0.484 / 0.483 / 0.0028 (0.0037) | no |
| is private | -0.006 / 0.489 / 0.463 / 0.0126 (0.0147) | +0.002 / 0.546 / 0.518 / 0.0036 (0.0048) | no |
| is property | -0.003 / 0.526 / 0.515 / 0.006 (0.007) | +0.0 / 0.5 / 0.499 / 0.0 (0.0) | no |
| is static or classmethod | +0.001 / 0.473 / 0.496 / 0.0113 (0.0132) | +0.0 / 0.5 / 0.5 / 0.0021 (0.0027) | no |
| is decorated | +0.002 / 0.49 / 0.502 / 0.0156 (0.0181) | -0.002 / 0.482 / 0.478 / 0.0076 (0.0099) | no |
| module depth | -0.015 / 0.463 / 0.588 / 0.0703 (0.0818) | +0.008 / 0.532 / 0.525 / 0.0188 (0.0248) | no |
| axis substance | -0.017 / 0.418 / 0.557 / 0.0124 (0.0144) | +0.014 / 0.564 / 0.5 / 0.0148 (0.0195) | no |
| axis form | -0.012 / 0.674 / 0.681 / 0.1186 (0.138) | +0.029 / 0.624 / 0.558 / 0.0788 (0.1036) | T2 |
| axis role | -0.053 / 0.535 / 0.695 / 0.0534 (0.0622) | +0.03 / 0.52 / 0.597 / 0.0136 (0.0179) | T2 |
| mask popcount | -0.027 / 0.453 / 0.728 / 0.0521 (0.0606) | +0.01 / 0.527 / 0.444 / 0.0271 (0.0357) | no |
| is test | +0.0 / 0.5 / 0.498 / 0.0 (0.0) | +0.0 / 0.5 / 0.489 / 0.0022 (0.0029) | no |

Notes:
* "caller_weight" is PRISM's `W_upstream`: hop1 = the existing field, defined for direct callers only;
  chain = the same formula applied to each upstream row and the callee it reaches the seed through.
* `is_test` has no variance in the T5 pool (Design C excludes test callers).
* Design C rank exists only for T5.
* Single-field AUCs below 0.5 mean "lower is more gold". High single-field values for fan-out, fan-in,
  mask popcount and the Role axis re-encode direction and vanish within strata.

Within upstream rows only (T5, 19 tasks with both gold and non-gold callers), the within-seed AUCs are:

| field | AUC |
|---|---|
| body lines | 0.79 |
| parameter count | 0.73 |
| Design C rank | 0.75 (lower rank = more gold) |
| hop | 0.69 (lower hop = more gold) |

Gold rate by upstream hop in the Design C lists:

| hop | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|
| rows | 138 | 74 | 47 | 17 | 20 | 8 |
| gold | 51% | 69% | 57% | 35% | 5% | 0% |

Keeping hops ≤ 4 keeps 157 of 158 gold.

**Verdict.** Fields that add signal beyond hop + direction, by size:

| task type | fields above the noise floor |
|---|---|
| T5 | body tokens/lines (+0.04), parameter count (+0.035), Design C rank (+0.028), type annotations (+0.024), Output axis (+0.018, marginal) |
| T2 | Output axis (+0.041), parameter count (+0.033), Role axis (+0.030), Form axis (+0.029), annotations (+0.024), call sites (+0.023) |

All of these are small; nothing approaches the effect of direction itself (base 0.89).

On the T2 manifest pool the 4-axis bits clear the noise floor, unlike on the earlier 3-hop pool. That makes
them weak T2 candidates, not ruled out.

### V5 — signature truncation

20 rows sampled reproducibly (5 per corpus) from the M4 manifests. Truncated: 2.
* fastapi `jsonable_encoder` is shown as `def jsonable_encoder(`. The real header is hundreds of tokens of
  `Annotated[..., Doc("""...""")]` parameters.
* trpc `rpc.parseTRPCMessage.assertIsRequestId` is shown as `function assertIsRequestId(`. The real
  header is `function assertIsRequestId( obj: unknown, ): asserts obj is number | string | null {`.

The other 18 sampled rows are complete.

| corpus | manifest rows | rows cut at `(` | unique symbols cut |
|---|---|---|---|
| django | 465 | 5 (1%) | 2 / 345 |
| fastapi | 348 | 46 (13%) | 16 / 133 |
| express | 179 | 0 | 0 / 65 |
| trpc | 316 | 85 (27%) | 19 / 68 |

Cause: `_signature_stub` keeps header lines until one ends in `:`, up to 10 lines; otherwise only the
first line. That covers every multi-line TS/JS header and Python headers over 10 lines.

Full-header length in cl100k tokens, median / p90 / max:

| corpus | median | p90 | max | over 128 tokens |
|---|---|---|---|---|
| django | 11 | 25 | 81 | 0 |
| express | 10 | 15 | 21 | 0 |
| trpc | 21 | 65 | 133 | 1 |
| fastapi | 23 | 109 | 6,463 | 12 of 133 (docstrings inside annotations) |

### V6 — is the prompt the cause?

**Turn-1 system prompt** (`benchmarks/run_two_pass_benchmark.py:325`, verbatim):

> You are a senior software engineer investigating a codebase. You will be given a compact <candidate_index> - every symbol reachable from a seed function, one per line as qualified_name|role|kind|signature|calls=[...] (role is one of seed/callee/caller/transitive; signature is the symbol's own raw declaration line; calls lists the names it directly invokes in its own body, deterministically extracted, never a docstring or comment) - followed by a real task. Examine the symbol signatures and their direct call targets to trace the complete causal execution path from the seed to termination. Request all necessary intermediate and helper symbols required to form an unbroken execution chain. Only name symbols that appear in the index - never invent one.

**Turn-1 user prompt tail** (`_turn1_user_prompt`, line 491, verbatim):

> Respond with a JSON object: {"thought_process": "1-2 sentences on why", "requested_symbols": ["qualified.name", ...]} - requested_symbols ordered seed first, then the causal stages in execution order, using each symbol's own full qualified_name exactly as given in the index (never a bare name from a calls=[...] list). Respond with this JSON object and nothing else.

**Manifest schema** (`src/prism/packer/candidate_index.py`, `build_candidate_manifest`), verbatim shape:

```
<candidate_index>
qualified_name|role|kind|signature|calls=[q1,q2,...]
qualified_name|caller|kind|signature|calls=[...]|binds_return=true/false|nontrivial_args=true/false
</candidate_index>
```

Rows are sorted alphabetically by qualified name.

**Verdict.** Yes, the prompt can be changed to fix the T5 behaviour without touching the manifest.

* Direction is fully encoded in `role`: in default mode `callee`/`transitive` rows are downstream and
  `caller` rows upstream; in Design C every upstream row is `caller`.
* The T2 evidence shows the model reads and obeys the column (2% caller picks, 94% gold-callee picks).
* Both instructions point downstream on every task type: "trace the complete causal execution path from
  the seed to termination" and "the causal stages in execution order".

One caveat for P1: as specified, it appends a T5 sentence but leaves both downstream phrasings in place. The
model therefore gets conflicting instructions. P1 is kept as specified, so the ablation changes one thing
at a time. If C2 shows a weak effect, a P1b that replaces the downstream sentence for T5 is the follow-up.

---

## Part 2-pre — Regression fix to land first (needs approval)

**Finding.** My earlier change priced best-guess (`TENTATIVE_CALL`) edges at their documented 0.60 discount
in the causal graph. `build_candidate_manifest` admits downstream candidates by weighted distance ≤ 3.0,
so a stage two best-guess hops away (2 × 1.67 = 3.33) is now excluded, even though it is 2 structural hops
from the seed.

Affected (T2, Express):

| task | seed | lost gold |
|---|---|---|
| `express_t02_001` | `lib.application.init` | `lib.application.enable`, `lib.application.set` (both 2 hops, distance 3.33) |
| `express_t02_002` | `lib.application.use` | `lib.application.enabled` (2 hops, 3.33), `lib.application.set` (3 hops, 5.0) |
| `express_t02_010` | `lib.response.redirect` | `lib.response.get` (2 hops, 3.33) |

Effect in the replay with M4's own picks: Express T2 delivered coverage 0.972 → 0.792; pooled T2
0.975 → 0.950. T5 is unaffected: T5 gold is upstream.

**Fix (universal).** In `build_candidate_manifest`, admit a downstream candidate if it is within 3
structural CALLS/INSTANTIATES hops (`_real_call_chain_reachable`, already computed by the scope filter) or
within weighted distance 3.0. Ordering and role labels keep using the weighted distance. About 3 LOC plus
1 test, the Express `this.set` shape on a synthetic repo.

The fix is expected to restore the boundary nodes dropped earlier (Django `Q` / `Query.add_q`, FastAPI
`UploadFile.read`, all within 3 structural hops), so the two pinned counts updated earlier
(t02_002 51, t02_009 dry run 12) should return to their M4 values (52, 14). That is to be confirmed by
the fix's own test run.

## Part 2 — Manifest variants M0 / M1 / M2 (plan, not implemented)

**Flag.** `harness/config.py`:
`MANIFEST_VARIANT: Literal["m0", "m1", "m2"] = os.environ.get("HARNESS_MANIFEST_VARIANT", "m0")`,
validated against the three values. Env overrides are also needed for `PRISM_BLAST_MODE`
(`HARNESS_PRISM_BLAST_MODE`) and the prompt flag, so each Kaggle config is one process with its own
environment. About 12 LOC.

**Serializer** (`src/prism/packer/candidate_index.py`): new `schema` argument on `build_candidate_manifest`
and `PrismEngine.build_candidate_manifest`, default `"m0"`. Traversal, candidate set, scope rule, budget
interleave and Turn-2 hydration are untouched; only the row text changes.

| variant | row format (fields appended after today's row, `key=value`) |
|---|---|
| M0 | unchanged, byte-identical to today |
| M1 | full signature in the `signature` slot + `|hop=N|lines=N` (+ `|sig_truncated=true` when capped) |
| M2 | M1 + `|caller_w=X.XX|fan_in=N|fan_out=N|is_test=true/false|params=N` |

Field definitions:
* `hop`: structural hops from the seed, direction implied by `role`. Downstream rows use
  `_real_call_chain_reachable`; when only the causal graph reaches the row, `ceil(dist_w)`. Upstream rows
  use `_upstream_walk`. Default-mode direct callers get 1.
* `lines`: `line_range` length.
* `caller_w`: `W_upstream` of the row with respect to the callee it reaches the seed through. Upstream
  rows only; blank otherwise.
* `fan_in` / `fan_out`: CALLS/INSTANTIATES degree in the structural graph.
* `is_test`: `SymbolInfo.role == VERIFICATION`.
* `params`: `builder._param_count`.

**Full signatures (M1).** New `_full_declaration(builder, qname, max_tokens=128)`:
1. Take the source from the symbol's first line (decorators included) to the start of its `body` node.
2. Collapse string literals longer than 24 characters to `"…"`. This is language-agnostic and handles
   FastAPI's `Doc("""…""")`.
3. Collapse whitespace to one line.
4. If still over 128 cl100k tokens, cut at 128 and append `…`, and set `sig_truncated=true`.

`_signature_stub` itself is not changed, because Turn 2 uses it to render stubs.

**Schema self-description.** M1/M2 add a `fields="…"` attribute to the opening `<candidate_index>` tag
naming every field. The P0 system prompt stays byte-identical while the manifest still documents its
extra fields. An attribute is used rather than a comment line because `strict_manifest` drops unknown
lines.

**Arm** (`harness/arms/arm5_prism.py`): pass `schema=C.MANIFEST_VARIANT` on both manifest calls, record
`build_meta["manifest_variant"]` and `build_meta["manifest_schema"]` (the field list string).

**LOC.** Serializer ~70; engine pass-through 3; arm 6; config/env 12. About 90 production LOC.

**Tests** (new `tests/test_manifest_variants.py`, 10):
1. M0 output is byte-identical to the current serializer on a synthetic repo (golden string).
2. M0 spot-check on the real FastAPI corpus: `fastapi_t02_016` equals the M4 text quoted in
   `manifest_enrichment.md`.
3. M1 rows all carry `hop` and `lines`; values correct on a 3-hop chain.
4. M1 joins a multi-line Python signature into one line with params and return annotation.
5. M1 does the same for a multi-line TS signature.
6. M1 caps an over-budget signature at 128 tokens with `…` and `sig_truncated=true`; string literals are
   collapsed first.
7. M2 rows carry every M1 field plus `caller_w`, `fan_in`, `fan_out`, `is_test`, `params` with correct
   values on a synthetic graph.
8. The candidate universe is identical across M0/M1/M2 for the same seed (only text changes).
9. The arm records `manifest_variant` / `manifest_schema` in `build_meta`.
10. An unknown `HARNESS_MANIFEST_VARIANT` is rejected at import.

**Turn-1 token cost** (estimate from the replay's Design C manifests, mean 1,460 cl100k user-prompt tokens
for 17.5 rows): M1 ≈ +8 tokens/row plus the restored signatures (FastAPI/trpc only) ≈ +10–20%. M2 ≈ a
further +15 tokens/row ≈ +18%. Both are far inside the 18,432-token context window.

## Part 2b — Rule selector for the no-LLM control (plan)

`PRISM_TURN1_SELECTOR: Literal["llm", "rule_callers"] = "llm"` (env `HARNESS_PRISM_TURN1_SELECTOR`). On
`rule_callers`, Arm 5 skips the Turn-1 model call and requests every `caller` row. Hydration and the
answer turn are unchanged. This is the Kaggle form of C0. About 10 LOC, 2 tests (selection equals caller
rows; no Turn-1 LLM call is made).

## Part 3 — Prompt variants P0 / P1 (plan, not implemented)

**Flag.** `TURN1_PROMPT_VARIANT: Literal["p0", "p1"] = os.environ.get("HARNESS_TURN1_PROMPT_VARIANT", "p0")`.

**Change.** `turn1_system_prompt(base, strict, target_max, task_type=None, variant="p0")` in
`harness/arms/arm5_prism.py`. For `p1` and `task_type == "T5_blast_radius"` it appends exactly:
"For blast-radius queries, select the callers of the seed — functions that invoke the seed — not the
functions the seed invokes." Any other task type or `p0` returns today's string.

Composition with the existing strict-prompt flag is unchanged (strict suffix after the P1 sentence).
`build_meta["turn1_prompt_variant"]` is recorded.

**LOC.** ~15 production.

**Tests** (in `tests/test_turn1_prompt_variants.py`, 6 functions, 9 cases):
1. P0 equals `TURN1_SYSTEM_PROMPT` byte-for-byte for every task type (5 cases).
2. P1 for T5 contains the caller sentence verbatim.
3. P1 for T1/T2/T3/T4 equals P0.
4. The strict flag still appends after P1.
5. The arm sends the P1 system prompt to the LLM on a T5 seed (stub LLM captures it).
6. `build_meta` records the variant.

## Part 4 — Offline ablation (replay)

**Method.** The replay drives the real `Arm5Prism.retrieve`: manifest → Turn-1 parse → Turn-2
`retrieve_requested` → harness `finalize_context` budget trim. A stub LLM returns either the stored M4
Turn-1 text or a deterministic rule's selection, and the stored Turn-2b text where M4 had one.

The harness tokenizer (Qwen2.5) can't be downloaded here (HTTP 403). Trimming uses cl100k × 0.8307,
fitted on 40,861 M4 items. Trimming never triggered (0 over-budget cells), so this approximation has no
effect on the results.

**Validation.** The B0 replay reproduces M4's delivered symbol sets exactly in 96/96 T5 and 267/267 T2
cells.

Replays:

| replay | PRISM code | Design C | what it stands for |
|---|---|---|---|
| B0 | pre-change | off | M4 |
| G0 | fixed graph | off | graph fixes only |
| C1 | fixed graph | on (T5) | graph fixes + Design C |

Policies:

| policy | what Turn 1 selects |
|---|---|
| `stored` | M4's Turn-1 picks, fixed |
| `rule_callers` | every `caller` row (C0) |
| `rule_callers_by_body_lines` | the same rows ordered by body size |
| `rule_callers_transitive` | caller + transitive rows |
| `rule_every_row` | every row |
| `bound_upstream_only` | M4 picks restricted to callers (bound) |
| `bound_plus_callers` | M4 picks plus every caller (bound) |

Metric: delivered gold coverage = |delivered ∩ gold| / |gold|, seed excluded, pooled over cells. This is
stricter than the harness's `uniform_cpi` (seed included) and `context_recall` (gold + required context +
boundary). Comparisons are within this table only.

**T5 delivered gold coverage** (pooled over the three seed runs; per corpus):

| replay | policy | fastapi | django | express | trpc | pooled | precision | recall (in list) | picks | gold in list |
|---|---|---|---|---|---|---|---|---|---|
| B0 | M4 picks (fixed) | 0.333 | 0.193 | 0.200 | 0.111 | 0.174 | 0.221 | 0.568 | 4.3 | 0.303 |
| B0 | C0: all caller rows | 0.438 | 0.311 | 0.200 | 0.260 | 0.303 | 0.768 | 0.981 | 2.2 | 0.303 |
| B0 | C0, ordered by body size | 0.438 | 0.311 | 0.200 | 0.260 | 0.303 | 0.768 | 0.981 | 2.2 | 0.303 |
| B0 | caller + transitive rows | 0.438 | 0.311 | 0.200 | 0.260 | 0.303 | 0.224 | 0.981 | 7.4 | 0.303 |
| B0 | every row | 0.438 | 0.311 | 0.200 | 0.260 | 0.303 | 0.160 | 1.000 | 10.5 | 0.303 |
| B0 | bound: M4 picks ∩ callers | 0.333 | 0.193 | 0.200 | 0.111 | 0.174 | 0.720 | 0.556 | 1.3 | 0.303 |
| B0 | bound: M4 picks ∪ callers | 0.438 | 0.311 | 0.200 | 0.260 | 0.303 | 0.323 | 0.994 | 5.2 | 0.303 |
| G0 | M4 picks (fixed) | 0.333 | 0.133 | 0.200 | 0.111 | 0.159 | 0.210 | 0.519 | 4.2 | 0.303 |
| G0 | C0: all caller rows | 0.438 | 0.311 | 0.200 | 0.260 | 0.303 | 0.768 | 0.981 | 2.2 | 0.303 |
| G0 | C0, ordered by body size | 0.438 | 0.311 | 0.200 | 0.260 | 0.303 | 0.768 | 0.981 | 2.2 | 0.303 |
| G0 | caller + transitive rows | 0.438 | 0.311 | 0.200 | 0.260 | 0.303 | 0.255 | 0.981 | 6.5 | 0.303 |
| G0 | every row | 0.438 | 0.311 | 0.200 | 0.260 | 0.303 | 0.168 | 1.000 | 10.1 | 0.303 |
| G0 | bound: M4 picks ∩ callers | 0.333 | 0.133 | 0.200 | 0.111 | 0.159 | 0.745 | 0.506 | 1.1 | 0.303 |
| G0 | bound: M4 picks ∪ callers | 0.438 | 0.311 | 0.200 | 0.260 | 0.303 | 0.324 | 0.994 | 5.2 | 0.303 |
| C1 | M4 picks (fixed) | 0.333 | 0.178 | 0.200 | 0.111 | 0.170 | 0.226 | 0.190 | 4.1 | 0.888 |
| C1 | C0: all caller rows | 0.875 | 0.733 | 0.400 | 0.990 | 0.888 | 0.510 | 0.994 | 9.6 | 0.888 |
| C1 | C0, ordered by body size | 0.875 | 0.733 | 0.400 | 0.990 | 0.888 | 0.510 | 0.994 | 9.6 | 0.888 |
| C1 | caller + transitive rows | 0.875 | 0.733 | 0.400 | 0.990 | 0.888 | 0.353 | 0.994 | 13.9 | 0.888 |
| C1 | every row | 0.875 | 0.733 | 0.400 | 0.990 | 0.888 | 0.283 | 1.000 | 17.5 | 0.888 |
| C1 | bound: M4 picks ∩ callers | 0.333 | 0.178 | 0.200 | 0.111 | 0.170 | 0.815 | 0.186 | 1.1 | 0.888 |
| C1 | bound: M4 picks ∪ callers | 0.875 | 0.733 | 0.400 | 0.990 | 0.888 | 0.390 | 0.998 | 12.6 | 0.888 |

No T5 cell exceeded the budget in any replay. Mean delivered tokens: B0 2.2k–2.7k, C1 2.1k–4.2k.

**T2** (stored picks; Design C and P1 do not apply to T2):

| replay | fastapi | django | express | trpc | pooled | precision | recall (in manifest) | gold in manifest | exact M4 match |
|---|---|---|---|---|---|---|---|---|
| B0 | 0.980 | 0.944 | 0.972 | 1.000 | 0.975 | 0.494 | 0.925 | 1.000 | 267/267 |
| G0 | 0.980 | 0.944 | 0.792 | 1.000 | 0.950 | 0.504 | 0.927 | 0.971 | 239/267 |
| C1 | 0.980 | 0.944 | 0.792 | 1.000 | 0.950 | 0.504 | 0.927 | 0.971 | 239/267 |

G0 and C1 are identical on T2 (Design C is T5-only). The whole T2 change is the Express regression in Part 2-pre.

**What each config can and cannot be measured offline:**

| config | manifest | prompt | Design C | offline result | needs Kaggle for |
|---|---|---|---|---|---|
| B0 | M0 | P0 | no | M4 exactly: coverage 0.174, precision 0.22, recall 0.57 of in-manifest gold | — |
| C1 | M0 | P0 | yes | ceiling 0.888 gold in the list. M4's picks projected onto the new list reach 0.170: the model never saw the new callers, so fixed picks say nothing about C1 | model picks on the new list |
| C2 | M0 | P1 | yes | not replayable (prompt change) | everything |
| C3 / C5 | M1 | P1 / P0 | yes | candidate set identical to C1 by construction; under fixed picks identical to C1/C2. Measured: Turn-1 tokens +10–20%, 13–27% of fastapi/trpc signatures restored | model picks |
| C4 / C6 | M2 | P1 / P0 | yes | as above; adds no field above noise beyond M1 | model picks |
| C0 | M0 | rule | yes | **exact: coverage 0.888, precision 0.51, 9.6 picks/cell** (B0 manifest: 0.303 / 0.77) | answer `tsr` only |

**Interaction (metadata × prompt), offline evidence only.**

* Metadata cannot raise T5 context coverage in any config. The budget never binds (mean 4.2k of 13k
  tokens delivered even when every row is requested), and "all callers" already delivers every reachable
  gold.
* Ordering callers by body size (`rule_callers_by_body_lines`) gives exactly the same coverage as plain
  "all callers".
* What metadata can change is which callers a selective model drops. Inside the upstream rows, body lines
  (AUC 0.79), parameter count (0.73) and hop (0.69) separate gold from non-gold. So metadata can only help
  if the prompt first makes the model select from the callers (P1) and the model is still selective. That
  predicts C3 − C2 ≥ C5 − C1 ≈ 0: metadata matters more with the prompt fix than without it. This is a
  prediction to test, not a result.
* The bounds bracket what P1 could do to coverage:

| what P1 does to the model | coverage |
|---|---|
| model keeps its picks and adds every caller | 0.888 |
| model keeps only the callers it already picked | 0.170 |

## Part 5 — Kaggle ablation design (for approval; do not run)

**Decision.** V4 shows small metadata signal above noise on the corrected pool (body size, parameter
count, hop). Part 4 shows it cannot change coverage, only precision. Include one metadata config, C3
(M1 + P1). Drop C4/C6: M2's extra fields (caller weight, fan-in/out, `is_test`) are at or below noise, and
its only above-noise extra (parameter count) is already visible in M1's full signatures. C5 (M1 + P0) is
optional: it answers the interaction question.

**Configs** (T5 only, Arm 5 only: `HARNESS_ACTIVE_ARMS=arm5`, `--task-types T5`, seeds 42,43,44):

| config | code / flags | purpose |
|---|---|---|
| B0 | M4 cells, already on disk; no rerun | historical baseline |
| G0K | fixed graph, `PRISM_BLAST_MODE=0`, M0, P0 | isolates Design C from the graph fixes |
| C1 | fixed graph, Design C, M0, P0 | Q1 |
| C2 | C1 + P1 | Q2 |
| C3 | C2 + M1 | Q3 |
| C0K | C1 + `PRISM_TURN1_SELECTOR=rule_callers` | Q4 ceiling (answer turn still uses the LLM) |
| C5 (optional) | C1 + M1 | interaction |

**Cells.** Per config: fastapi 24, django 24, express 6, trpc 42 = 96. Required configs G0K, C1, C2, C3,
C0K = 480 cells; with C5, 576. An optional T2 guard for M1 (C3 on T2 only: 267 cells) checks that the
manifest change doesn't hurt T2.

**Time.** M4 Arm 5 T5 cells averaged 23 s end-to-end (Django 26 s, fastapi 27 s, trpc 20 s, express 18 s).
Design C manifests are ~63% longer at Turn 1 (1,460 vs 894 cl100k tokens) and deliver ~2× the context
(4.2k vs 2.2k tokens), so the estimate is ~30 s per cell. C0K skips Turn 1 (~20 s).

| item | time |
|---|---|
| per config (96 cells) | ~48 min |
| five required configs (480 cells) | ~4 h of cells |
| Django index per process | +5 min |
| model warm-up per session | +~1 min |
| optional C5 | +48 min |
| optional T2 guard | +~1.7 h |

**Sharing a session.** Possible, run as sequential processes:
* The runner reads PRISM/prompt flags at import and checkpoints per `--out`. Give each config its own
  process, environment variables and `--out` directory.
* The `.prism` index cache is cleared at the start of each run (`prism_cache_clear` in the gate report);
  the flags don't affect indexing.
* All configs in one session must use the same repository commit. Ollama is shared and stateless across
  requests. The stored M4 bundles record `llm_seed: None`, so sampling reproducibility across configs
  should be checked on one repeated cell before comparing configs.

Per corpus, five configs in sequence (G0K → C1 → C2 → C3 → C0K):

| corpus | cells per config | estimated time |
|---|---|---|
| trpc | 42 | ~1.75 h |
| django | 24 | ~1.4 h (incl. 5 × 5-min index) |
| fastapi | 24 | ~1 h |
| express | 6 | ~0.25 h |

All four fit one session of about 4.5–5 h including model setup, well inside the 12 h limit. Running one
session per corpus, as in M4, also works if a single long session is a risk.

**Prerequisites before any Kaggle run** (all need approval):
1. The structural-hop admission fix (Part 2-pre).
2. M0/M1/M2 and P0/P1 flags with env overrides, plus the rule selector (Parts 2, 2b, 3).
3. The full test suite green except the known failures.

## Part 6 — What the combined ablation settles

Each question is answered by a direct comparison on the same 96 T5 cells (answer `tsr` primary,
`context_recall` / `uniform_cpi` secondary). Offline status is noted where the data already answer part
of it.

| question | comparison | offline status |
|---|---|---|
| Q1. Does Design C alone improve T5? | G0K vs C1 (Design C alone); B0 vs C1 (with graph fixes) | reachable gold 0.303 → 0.888 in the delivered-context ceiling; whether the LLM exploits it needs Kaggle |
| Q2. Does the prompt fix improve T5 selection? | C1 vs C2 | needs Kaggle |
| Q3. Does metadata add signal beyond the prompt fix? | C2 vs C3 (C5 vs C1 for the interaction) | cannot raise coverage (budget slack); can only change precision/answers; needs Kaggle |
| Q4. Is the LLM worth using at Turn 1? | C2 vs C0K | C0's context is known exactly (0.888 coverage, precision 0.51); its answer `tsr` needs Kaggle |

**Reference points from M4** (Arm 5 T5): `tsr` 0.199, `context_recall` 0.350, 23 s/cell. The Oracle arm on
the same cells: `tsr` 0.614, `context_recall` 0.805.
