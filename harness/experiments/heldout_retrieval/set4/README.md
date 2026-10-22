# Held-out set 4 (DRAFT - not frozen)

48 questions across four repositories that no earlier set, corpus or tuning
run has used. It is the go/no-go set for the locked purpose-search candidate
(`harness/experiments/purpose_search/CANDIDATE.md`): it is scored **once**,
after it is frozen, and nothing in the candidate changes after seeing it.

| repo | language | pinned | root | candidates (name-only index) | questions |
|---|---|---|---|---|---|
| `pallets/flask` | Python | 3.1.2 (`2c1b30d`) | `src` | 416 | fl01-fl12 |
| `httpie/cli` | Python | 3.2.4 (`2105caa`) | `.` | 787 | hp01-hp12 |
| `fastify/fastify` | JavaScript | v5.6.1 (`91414fe`) | `.` | 922 | fy01-fy12 |
| `colinhacks/zod` | TypeScript | v3.23.8 (`ca42965`) | `src` | 373 | zd01-zd12 |

Categories: 31 feature_logic, 9 io_sink, 8 routing (zod is all feature_logic:
it has no I/O or routing).

## How it was written

- From each repo's source at the pinned commit. The only tool run on these
  repos was a name-only index (the candidate pool's names) to check that every
  gold and decoy symbol is a candidate. No search, ranking, embedding or
  summarizer has seen them.
- Gold = the function(s) that directly answer; four questions have two gold
  symbols where the behaviour is split between an entry point and its helper
  (fy03, fy05, zd04, zd10).
- Decoys = plausible wrong answers: the inverse operation (save vs. open), the
  caller or the helper, a sibling with the same stems.
- Paraphrase rule (set 3's): no question contains its gold symbol's name or
  all of the name's words, checked automatically (0 violations).
- Gold is library code, never test or example files.

## Repos considered and dropped

`honojs/hono` was the first TypeScript choice: most of its API is
`export const name = (...) => {...}`, and PRISM does not index arrow
functions assigned to constants (`utils/url.ts:getPath`, `middleware/cors`'s
`cors` have no symbol). Hono would have measured that indexing gap rather than
retrieval, so zod (classes and function declarations) replaced it.

## Review checklist (before freezing)

1. Does each question read like something a developer would ask?
2. Is the gold the function you would want returned? Should any question have
   another gold symbol?
3. Are the decoys plausible but wrong?
4. Does any question give away its answer through a too-specific term?

To freeze: set `"status": "frozen"` and `"version": 1` in `dataset.json`,
commit, and record that commit in the set-4 run before any engine runs.
