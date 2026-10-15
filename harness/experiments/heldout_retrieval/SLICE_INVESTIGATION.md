# Entry-to-sink path slicing: investigation

Everything here is read-only and changes nothing in Prism. It runs on `eval/held-out-benchmark` against the same 50 frozen questions. Script: `slice_investigation.py`. Numbers: `slice_investigation_results.json`.

## TL;DR

- **Feasible and cheap.**
  - Entry points can be found with a framework-agnostic rule, with no decorator lists. Detection takes about 1.5 ms per repo, and the reverse traversal for every function 2–3 ms. Per-query propagation is under 0.1 ms.
- **But propagating entry-point context hurts ranking.** Every variant at every weight lowers top-1. The best case is a +0.02 gain in separation, which is one question.
- **The premise only partly holds.** Of the 16 lexical-leg misses, 3 fit the "sink loses to its entry" shape. In 4 the gold *is* the entry point, and slicing makes those worse.
- **No capability is recovered.** Entry-point context cannot create bits where the I/O ends at a PROXY or OPAQUE boundary.
- **Recommendation:** do not index slices for ranking. Keep entry-point detection, which is generic and correct, for *presentation*: show the entry → sink path next to a ranked hit.

## 1. Graph feasibility

**Entry rule** (framework-agnostic): a function that has at least one decorator that is not a language builtin (`property`, `staticmethod`, `functools.*`, …) and has no in-repo `CALLS`/`INSTANTIATES` caller. Registration by decorator is how a framework invokes it.

| | microblog | realworld |
|---|---|---|
| functions | 113 | 127 |
| detected entries | 40 | 22 |
| decorated *and* called from repo code (would be false entries) | 0 | 0 |
| functions with an entry ancestor within 3 hops | 20 | 40 |
| detection / all-function traversal | 1.6 ms / 1.7 ms | 1.5 ms / 2.7 ms |

- The detected decorator heads are `bp.route`, `router.get/post/put/delete`, `app.get`, `bp.cli.group`, `translate.command`, `token_auth.verify_token`, `login.user_loader`, `bp.app_errorhandler`, `logger.catch`, `validator`, and others. There are zero framework names in the rule.
- **Cost:** reverse traversal is `graph.in_edges`, i.e. O(in-degree) per hop on a networkx DiGraph. Index-time precomputation for every function costs milliseconds. Search time is the same order, so either works.

**What the graph cannot reach.** The traversal is only as good as the call edges, and framework-mediated invocation leaves most sinks without an entry ancestor:

| unreachable shape | example | why |
|---|---|---|
| calls through a runtime proxy | `current_user.following_posts()`, `current_user.launch_task()` | `flask_login.current_user` is PROXY, so no edge reaches `User.*` |
| dependency injection | `Depends(_get_current_user)`, `Depends(get_repository(...))` | a default argument, not a call |
| task dispatch by string | `task_queue.enqueue('app.tasks.export_posts', ...)` | a string name, not a reference |
| event registration | `db.event.listen(db.session, 'after_commit', SearchableMixin.after_commit)` | registration, not a call |
| thread target in a host build | `Thread(target=send_async_email)` | callback-argument edges exist only in summary mode |

As a result, 14 of the 27 microblog gold functions are either entries themselves or have no entry ancestor at all. In realworld, every repository sink has one, because the routes call typed repositories directly.

## 2. Score-propagation simulation

The base is Prism's own BM25 leg (NB documents) with top-1 0.68, recall@5 0.92, separation 0.84. `a` is the propagation weight. The `a` values were swept on the test set itself, so the best cell overstates what you could expect.

| variant | a | top-1 | recall@5 | MRR | separation |
|---|---|---|---|---|---|
| base | – | **0.68** | 0.92 | **0.781** | 0.84 |
| down: sink += a·max(entry ancestors) | 0.25 | 0.60 | **0.94** | 0.745 | 0.82 |
| | 0.5 | 0.56 | 0.92 | 0.713 | 0.82 |
| | 1.0 | 0.46 | 0.84 | 0.619 | 0.72 |
| path: node += a·max(any caller within 3) | 0.25 | 0.64 | **0.94** | 0.764 | **0.86** |
| | 0.5 | 0.58 | 0.92 | 0.716 | **0.86** |
| | 1.0 | 0.52 | 0.82 | 0.650 | 0.82 |
| up: entry += a·max(descendants within 3) | 0.25 | 0.56 | 0.92 | 0.712 | 0.74 |
| | 0.5 | 0.46 | 0.92 | 0.648 | 0.68 |
| | 1.0 | 0.32 | 0.85 | 0.505 | 0.62 |

Top-1 changes against the base at a = 0.25:

| variant | won | lost |
|---|---|---|
| down | none | mb12, mb15, rw15, rw18 |
| path | mb08 | mb12, mb15, rw18 |
| up | none | mb12, rw09, rw10, rw11, rw12, rw16 |

**Mechanism.** Propagation adds the *same* entry score to every function under that entry. Shared helpers and siblings catch up with the precise answer, or with the entry when the entry is the answer:
- **mb15:** `User.from_dict` overtakes the `create_user` endpoint.
- **rw18:** the repository's `create_user` overtakes the `login` endpoint.
- **mb12:** `check_token` overtakes `get_token`.

The upward variant is worst, for the mirror-image reason: every route absorbs its repository's stems.

## 3. Dry-run traces

**mb20: "Where are notifications saved to the database, replacing older ones with the same name?"** The gold is `User.add_notification` and the decoy is the `notifications` route.
- The premise does not hold here. The `notifications` route is not a caller of `add_notification`; it only reads notifications, so it is a sibling, not an ancestor. The real callers are `send_message` (a direct edge, entry 1 hop up), `messages` (through the `current_user` proxy, so no edge) and `_set_task_progress` (an untyped `task.user`, so no edge).
- Lexically the sink already wins: rank 1 in every variant.
- Its round-1 triad loss came from the unknown-to-action weight transfer. The route touches the `current_user` proxy, so it got a 0.40 boost against 0.15 for the sink. Slicing does not touch that rule.

**rw08: "How are articles filtered by tag, author and favorited user with limit and offset?"** The gold is `ArticlesRepository.filter_articles`.
- Base: rank 4, behind the `get_articles_filters` dependency, the `list_articles` route and the `ArticlesFilters` schema.
- `down@0.25` lifts it to rank 2, because its entry `list_articles` matches the question. It still loses to `get_articles_filters`, which is an injected dependency with no entry ancestor, so the slice does not reach it.
- This is the one case shape the hypothesis targets: a sink beneath a lexically matching entry, against a decoy outside the slice. It gains a rank but not top-1.

**rw23: "Which endpoint unfollows a user?"** The gold is the `unsubscribe_from_user` route.
- Base: rank 22. The decorator is already in the indexed span, so this is not a document gap.
- It is a vocabulary gap: "unfollow" occurs nowhere in the code, which uses `unsubscribe` and `/follow`.
- `down@0.25` drops it to rank 38, because the repository sinks of other routes rise above it.
- Slicing cannot help when the entry is the answer.

## 4. Composite scoring and PROXY/OPAQUE

Proposed form:

`score = w_lex·(local + α·entry) + w_act·verb(local) + w_cap·cap(downstream)`

- **Lexical term:** measured above. Any α > 0 that is large enough to move a rank also moves shared helpers, so the net effect on top-1 is negative. If kept at all, it must be differential, crediting a sink only when it is the *only* function under its entry that the question matches. That is a ranking heuristic tuned on a 50-question set. We should not adopt it on this evidence.
- **Action term:** already local. Entry decorators do carry a generic verb (`methods=['POST']`, `router.delete`), and mapping HTTP methods to actions is a cross-framework convention, not per-library plumbing. But it is shared by every descendant, so it has the same dilution problem. It is only safe for the entry itself, where it is already in the indexed text.
- **Capability term:** slicing adds nothing. Of the 39 oracle-capability gold functions, about 35 have `0x0` because the I/O ends at a PROXY (`current_app.elasticsearch`, `current_user`) or OPAQUE (SQLAlchemy constructors) boundary, or at a runtime-generated method (aiosql `queries.*`). An entry point above them is equally `0x0`. A route verb can stand in for the action, but that is not capability evidence. Calling it capability would just relabel a lexical signal.

## Recommendation

1. **Do not index entry-to-sink slices for ranking.** On held-out code it lowers top-1 at every weight tried, and the mechanism (uniform credit to siblings) is structural, not a tuning problem.
2. **Keep the entry rule as a presentation feature.** "Decorated, not called" is framework-agnostic, needs no decorator catalog, so there's no whack-a-mole, and cost is negligible. Attaching the entry → sink path to each ranked hit gives an agent the business context without changing which function ranks first.
3. **Spend the next effort where the losses are:**
   - the unknown-to-action weight transfer (mb20-style caller wins);
   - class-node candidates outranking their methods (rw04, mb13, mb05);
   - migration-file noise (mb19);
   - add-vs-remove and generate-vs-verify sibling confusion (mb03, mb10, mb11), which no structural signal separates.
4. **The reachability gaps are the real slice blocker:** proxies, `Depends`, enqueue-by-string and event registration. Closing them means modelling framework registration conventions, which is the whack-a-mole trap. Under the Rule of 3 they stay at the boundary.

## Limitations

- 50 questions over two small services.
- `a` was swept on the test set.
- Only 3 of the 16 misses fit the hypothesis's case shape, so the investigation cannot rule out a benefit on codebases with deeper service layers. It found no evidence of one here.
