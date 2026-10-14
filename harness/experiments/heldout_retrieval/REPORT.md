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
