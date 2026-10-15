# Held-out retrieval benchmark: first results

## Setup

- **Repositories**, never used for tuning, pinned:
  - `miguelgrinberg/microblog` @ `a975ef6`: Flask, SQLAlchemy, Elasticsearch, rq/redis jobs, a translation HTTP API, email.
  - `nsidnev/fastapi-realworld-example-app` @ `029eb77`: FastAPI, asyncpg, aiosql, pypika, JWT.
  - Each repo's dependencies were installed into a private target directory, so Prism builds its depth-1 dependency summaries.
- **Questions**: 50 in `dataset.json`, 25 per repo.
  - Categories: 21 I/O sink, 20 feature logic, 9 routing.
  - Each has gold function(s) and 1–3 decoys: the inverse operation, the route versus the repository layer, or same-stem names.
  - The questions were written from the source before any system ran and committed in `209edc9` to freeze them.
- **Candidates**: every system ranks the same units, Prism's lexical pool. That is every indexed non-test function, method or class: 131 in microblog and 180 in realworld.
- **Systems**: see `run.py` for the exact definitions.

| system | what it is |
|---|---|
| ripgrep | real `rg -i -F -w` per question keyword, line → innermost candidate, rank by distinct keywords then hits |
| bm25 | BM25Okapi over qualified name + source, identifier-split tokens, no stemming |
| bm25_stem | *post-hoc ablation, added after the first run*: the same BM25Okapi with Prism's tokenizer (one-suffix stemming, Prism's stop words) |
| prism_bm25 | Prism's own lexical leg alone (NB documents, repo-relative paths) |
| prism_triad | the same, re-ranked by the triad (stems × action × capability, dependency summaries on); intent from the question text by the deterministic `rule_intent` |
| embedding | sentence-transformers cosine similarity: **not run**, because huggingface.co is denied by this environment's network policy |

## Results (50 questions)

| system | top-1 | recall@5 | MRR | gold above all decoys |
|---|---|---|---|---|
| ripgrep | 0.34 | 0.53 | 0.43 | 0.40 |
| bm25 | 0.44 | 0.81 | 0.61 | 0.70 |
| bm25_stem | **0.68** | **0.93** | **0.78** | **0.84** |
| prism_bm25 | **0.68** | 0.92 | **0.78** | **0.84** |
| prism_triad | 0.66 | 0.92 | 0.78 | 0.80 |

Paired sign tests (top-1 / separation, wins–losses):

| comparison | top-1 | separation |
|---|---|---|
| triad vs prism_bm25 | 1–2, p = 1.0 | 0–2, p = 0.5 |
| prism_bm25 vs bm25 | 12–0, p < 0.001 | 8–1, p = 0.04 |
| triad vs ripgrep | 19–3, p = 0.001 | 21–1, p < 0.001 |

## What this says

1. **The triad adds no measurable value on held-out code.** It changed the ranking on 15 of 50 questions. Top-1 netted one fewer hit (1 gained, 2 lost) and separation two fewer (2 lost). It helps when the question's action verb matches the function name: `remove_from_index` moved from rank 4 to 2 and `launch_task` from 2 to 1. It hurts when the sink and its caller both reach the resource: the capability leg counts transitive reach, so the `notifications` route outranks `User.add_notification` and the `login` route outranks `check_password`. That is exactly the route-versus-repository distinction the decoys test.
2. **Prism's lead over plain BM25 is entirely its stemmer.** A stock BM25 with the same one-suffix stemming matches Prism's lexical leg to within one recall point (0.93 vs 0.92). Nothing on this benchmark credits the call graph, the dependency summaries or the capability profiles.
3. **The intent rule is thin.** `rule_intent` produced an action on 35 of 50 questions but a capability on only 5, all DATABASE_IO. Its capability lexicon has no words for email, search, queue, cache or HTTP API, and some actions are misread: "newly submitted post" became SEND and "pool closed" became CONNECT. I did not change it, because that would be tuning on the test set.
4. **Ripgrep is the weakest baseline.** Agents commonly start with it, and every BM25 variant beats it on separation by a wide margin.

## Limitations

- 50 questions over two small services (1.4k and 2.2k lines). Differences of one or two questions are noise.
- One author wrote the questions, gold and decoys, and that author knows how Prism works. Freezing before any run guards against tuning, but not against phrasing bias.
- No embedding baseline was run. To run it, allow `huggingface.co` in the environment's network settings, then run `run.py` with `--embed-model`.
- Candidates include class nodes, whose documents span the whole class body; this affects every system equally.

## Reproduce

```
python -m harness.experiments.heldout_retrieval.run --repos-dir R --sites-dir S --out OUT
```

`R/microblog` and `R/realworld` are checkouts at the pinned commits. `S/<repo>` is a `pip install --target` directory holding that repo's requirements. `results.json` is the run reported here, and the output is deterministic: two runs were identical.

---

# Round 2: hop attenuation and intent oracle

## What changed

- **Internal-hop attenuation** (`hop_gamma`, opt-in): `CapabilityProfile.internal` counts reach in repo-internal call edges only.
  - A function calling a summarized dependency, or an L2 primitive, is the sink and scores γ⁰ = 1. Each repo caller above it scores γ^h, with γ = 0.5.
  - Note that `HOP_DECAY = (1.0, 0.5, 0.25)` already decayed by graph hops. The difference is that a dependency call no longer costs a hop: the sink used to score 0.5, the same as a caller one hop above a primitive.
  - Default scoring is unchanged. The profile cache version is now 3. Tests are in `tests/test_hop_attenuation.py`.
- **Intent oracle** (`intent_oracle.json`): an action and capability per question, frozen in `b32c606` before any oracle run.
  - 39 questions have a capability: 28 DATABASE_IO, 9 network, 1 process, 1 file write.
  - The 11 pure-computation questions are null by rule, so they rank as plain BM25.

## Results (same 50 frozen questions)

| system | top-1 | recall@5 | MRR | separation |
|---|---|---|---|---|
| BM25 + stemming | **0.68** | **0.93** | 0.782 | **0.84** |
| Triad, rule intent | 0.66 | 0.92 | 0.777 | 0.80 |
| Triad, rule intent, attenuated | 0.66 | 0.92 | 0.777 | 0.80 |
| Triad, oracle intent | 0.66 | 0.92 | **0.786** | 0.82 |
| Triad, oracle intent, attenuated | 0.66 | 0.92 | **0.786** | 0.82 |

- **Attenuation changed nothing measurable.** The top 5 changed on only mb06 and rw01, and no top-1 or separation outcome changed in either intent mode (0 wins, 0 losses).
- **The oracle intent alone is a wash.** Against the rule intent it gains 1 and loses 1 on top-1, and gains 1 on separation. Against stemmed BM25 it loses one more than it wins. Every paired sign test has p = 1.0.

## Why: the capability leg has almost no evidence on these repos

- Of the 39 gold functions the oracle gives a capability, about 4 carry a matching bit.
- **Microblog:** database work goes through Flask-SQLAlchemy's `db.session`, and the SQLAlchemy constructors are OPAQUE (compiled extensions). Elasticsearch and rq calls go through the `current_app.*` runtime proxy, so they are PROXY.
- **Realworld:** every SQL call is an aiosql method generated at runtime from `.sql` files (`queries.create_new_user(...)`), on a connection injected by FastAPI `Depends`. Only `connect_to_db` (`asyncpg.create_pool`) has a DB bit.

With zero capability on both sink and caller, decay has nothing to attenuate.

## The two regressions were not caused by hop distance

- **mb20** (`add_notification` loses to the `notifications` route): both have capability 0.
  - The route reaches the `flask_login.current_user` proxy, so its capability is "unknown". The triad then moves the capability weight onto the action leg, giving a boost of 0.40 against 0.15 for the true sink.
  - Route handlers touch `request`/`current_user` proxies constantly. This rule therefore systematically favours callers over sinks in Flask apps.
- **rw16** (`check_password` loses to `login`): the rule intent misread the question as MUTATE, and the action leg matched `login`. The oracle correctly emits no intent here, and the regression disappears.

## Implications

1. The bottleneck is **evidence coverage on framework-mediated I/O**: ORM sessions, app-context proxies, query builders generated at runtime, and injected connections. Neither hop decay nor intent quality can recover a signal that was never computed. Under the Rule of 3 these land in OPAQUE/PROXY, and the triad can only score them through action verbs.
2. **The unknown-to-action weight transfer** should not reward a function just because it touches *any* proxy. Candidate fixes, untested:
   - transfer the weight only when the proxy is the I/O receiver itself;
   - transfer only `capability_weight × γ^h` of the weight;
   - transfer nothing.
3. With today's evidence, the triad does not beat stemmed BM25 on held-out code. The unit-harness separation came from synthetic packages built to carry the bits.

---

# Round 3: three targeted fixes and entry-point context (in-sample)

> **Read this round as in-sample.** The three fixes were designed after reading this question set's failures, so these numbers are an upper bound on what they do on unseen code. A fresh frozen question set is needed before any claim of improvement.

## Fixes

Each is generic, with no repo or framework names.

1. **Unknown capability is neutral** (`triad_rerank`, now the default). OPAQUE or PROXY reach scores 0 on the capability leg and moves no weight onto the action leg; a compiled driver's DB verb no longer counts as the action. Provenance is still recorded. `unknown_to_action=True` restores the old behaviour.
2. **A class never outranks its own best-matching method** (`LexicalIndex(member_over_class=True)`). A class's document contains its methods' text, so it is placed just below its best member.
3. **Migration files go below every application hit** (`LexicalIndex(demote_migrations=True)`): files under `migrations/`, `alembic/` or `migrate/`, unless the query names migrations or schema.

Priors 2–3 default off in `LexicalIndex`, so the recorded NLQ probe still reproduces. The benchmark arms turn them on.

## Results

| system | top-1 | recall@5 | MRR | separation |
|---|---|---|---|---|
| BM25 + stemming | 0.68 | **0.93** | 0.782 | **0.84** |
| Prism triad, previous (rule intent) | 0.66 | 0.92 | 0.777 | 0.80 |
| + neutral unknown only | 0.68 | 0.92 | 0.787 | 0.82 |
| + class prior only | 0.66 | 0.92 | 0.780 | 0.80 |
| + migration demotion only | 0.66 | 0.92 | 0.782 | 0.80 |
| **Prism triad, all three fixes (rule intent)** | **0.68** | 0.92 | **0.795** | 0.82 |
| Prism triad, all three fixes, oracle intent | **0.74** | 0.92 | **0.837** | **0.84** |

- **The previous arms reproduce round 2 exactly.** They keep `unknown_to_action=True`.
- **Neutral unknown** is the only fix that changes a top-1 outcome. It fixes mb20: `add_notification` now beats the `notifications` route.
- **The class prior and migration demotion** improve ranks below the top: rw04 3→2 (`UsersRepository` falls below `create_user`), mb19 4→2. They change no top-1 outcome.
- **Fixed triad vs stemmed BM25 (rule intent):** top-1 +mb06 / −rw16, which nets to a tie; separation −rw16.
- **Fixed triad with oracle intent vs stemmed BM25:** top-1 +mb08, +mb13, +mb16, no losses (sign test p = 0.25). With a correct intent, the triad now beats the lexical baseline on 3 of 50 questions without losing any. With the rule intent it does not. Intent quality is now the limiting factor.

## Entry-point context on hits (display only)

`LexicalIndex.hits(...)` returns each hit with its upstream entrances (`prism.graph.entry_points`).
- **Detection:** a function counts as an entry point when it has a non-builtin decorator and no in-repo caller. Test code is excluded.
- **Labels:** the HTTP method comes from the decorator name or its `methods=[...]` argument; a string argument is shown as the path only when it starts with `/`.
- **Ranking:** order is exactly `ranked(...)`.

```
mb20  Where are notifications saved to the database, replacing older ones with the same name?
  #1 app.models.User.add_notification  (app/models.py:202)
        [Entry: GET POST /send_message/<recipient>] -> send_message() -> User.add_notification()
  #2 app.main.routes.notifications  (app/main/routes.py:230)
        [Entry: /notifications] -> notifications()
rw04  Which repository method inserts a new user row into the database?
  #2 app.db.repositories.users.UsersRepository.create_user  (app/db/repositories/users.py:29)
        [Entry: POST @router.post] -> register() -> UsersRepository.create_user()
mb16  Which web route handles following another user?
  #2 app.main.routes.follow  (app/main/routes.py:114)
        [Entry: POST /follow/<username>] -> follow()
  #3 app.models.User.follow  (app/models.py:144)
        (no entry point reaches it through call edges)
```

- **Coverage:** 135 of 250 top-5 hits carry an entry path.
- **The rest:** classes, module-level helpers, and sinks reached only through proxies, `Depends`, enqueue-by-string or event registration. `User.follow` above is one: it is called as `current_user.follow`.
- **Mount prefixes are not resolved.** Blueprint `url_prefix` and `include_router(prefix=...)` are framework configuration, so `/api` is not prepended.
