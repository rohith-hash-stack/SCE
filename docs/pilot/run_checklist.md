# Pilot Pre-Run Checklist

Run through this immediately before starting the pilot (the real,
1,800-call STOP-condition run, per `docs/pilot/stop_condition.md`).
Every item must be checked before the first call goes out.

- [ ] Working tree clean (`git status`)
- [ ] `.prism/cache/` deleted before the run
- [ ] `DEEPSEEK_API_KEY` set in environment
- [ ] Off-peak confirmed (check `date -u` against DeepSeek peak hours -
      avoid weekday 01:00–04:00 UTC and 06:00–10:00 UTC, per
      `docs/pilot/stop_condition.md` Section 5)
- [ ] Model identifier matches `stop_condition.md`'s pin
      (`deepseek-v4-flash`)
- [ ] `--pragmatic-oracle` is included in the invocation (Oracle is
      NOT included in the engine sweep unless one of
      `--oracle-packages`/`--pragmatic-oracle` is passed - see the
      "Oracle engine" note below)

## Notes

- **Oracle engine**: `benchmarks/runner.py`'s `_build_engines` only
  includes an Oracle variant when the CLI invocation passes
  `--oracle-packages <path>` (a hand-curated file) or
  `--pragmatic-oracle` (the zero-annotation-cost substitute - the one
  this pilot uses, since no hand-curated oracle-packages file exists
  for this repo). Omitting both silently runs only the other 4 engines
  (Prism v1.1, BM25, BFS-forward, BFS-bidirectional) - no error, no
  warning, since running without an Oracle is itself a legitimate
  eval-mode use case. The pilot's own plan calls for 5 engines
  including Oracle, so the real invocation must include
  `--pragmatic-oracle`:

      python -m benchmarks.runner --mode=pilot --pragmatic-oracle \
        --repo=django --budgets 2000 4000 8000 \
        --seeds=42,43,44,45,46 --output=reports/pilot/

  (`--budgets` is `nargs="+"` - space-separated values, not a
  comma-separated string; `--budgets=2000,4000,8000` fails argparse
  with `invalid int value: '2000,4000,8000'`. `--seeds` *is*
  comma-separated by design - the two flags don't share a convention,
  confirmed directly by running both forms through `build_arg_parser()`.)

- **Working tree clean**: uncommitted changes to the four-axis modules
  mid-run would (after G43's fix) correctly bust the feature-bitmask
  cache key rather than silently serve a stale value, but a clean tree
  is still the simplest way to guarantee the run reflects exactly the
  commit it's attributed to in the report.
- **`.prism/cache/` deletion**: `rm -rf .prism/cache/` in each corpus
  checkout the pilot will index. Zero downside, cheap insurance - see
  `docs/pilot/stop_condition.md` Section 7.
- **`DEEPSEEK_API_KEY`**: `echo $DEEPSEEK_API_KEY | head -c 8` to
  confirm it's set without printing the full key to a shared terminal.
- **Network reachability**: separately from all of the above,
  `api.deepseek.com` must actually be reachable from wherever the pilot
  runs. It is **not** reachable from this development environment
  (organization egress policy blocks it - confirmed directly via both
  `WebFetch` and `curl` during Phase 1 setup, `CONNECT tunnel failed,
  response 403`). The real pilot run must happen from an environment
  where this has been separately verified, not assumed.
- **Checkpointing**: the checkpoint file (`reports/pilot/checkpoint.json`
  by default) is saved every 100 completed cells (`CHECKPOINT_INTERVAL`
  in `benchmarks/runner.py`), plus one final save if the run completes
  normally. A hard kill (SIGKILL, power loss, Ctrl+C included) mid-batch
  loses up to 99 completed-but-unsaved cells. Those cells' API calls were
  already paid for; `--resume` will re-execute them on the next run -
  wasted cost, not a correctness risk (checkpoint cell keys are
  idempotent, so a re-run never double-counts). `runner.py` has no
  signal handler of any kind (confirmed directly - no `signal`/`SIGINT`/
  `KeyboardInterrupt` handling anywhere in the module), so Ctrl+C and a
  hard kill behave identically for checkpoint purposes: there is
  currently no way to force an out-of-cycle save on interruption. The
  only real mitigation available today is the 100-cell interval itself
  bounding the loss.
