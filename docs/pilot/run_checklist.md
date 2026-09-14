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

## Notes

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
