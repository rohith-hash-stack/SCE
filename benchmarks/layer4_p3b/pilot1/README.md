# P3b pilot authoring run (record only)

The first Kaggle authoring run, under the original rule (3..15 gold within 2 hops, 24 specs per
corpus). It yielded 9 of 96 traced specs, so the freeze refused. No task here was frozen or shown to
any arm. Kept as the record behind amendment 1 (`harness/experiments/retrieval_pivot/P3.md`).

- `draw.json`, `specs_p3b.py`: the draw and specs it ran. The draw order is identical to the
  amended draw's.
- `authoring_log_<corpus>.json`, `author_<corpus>.log`, `AUTHORING.md`, `authoring_code.json`: its
  decisions and logs.
- `tasks/`, `manifests/`: the 9 tasks it kept. Their `trace_manifest` paths still name
  `benchmarks/layer4_p3b/manifests/`, where they were written. These are not P3b tasks: the
  amended run re-authors from rank 1.
