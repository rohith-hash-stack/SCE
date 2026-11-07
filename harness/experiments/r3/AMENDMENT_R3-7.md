# Amendment R3-7: the memory test's pass rule, re-specified (2026-11-07)

Written after the first 20-run memory test (BUILD_PLAN section 7) and before its rerun. No model call has been made.
It changes only the memory test, a harness check before the pilot. It changes no rule of R3a's analysis.

**The first result stands in the record.** At code `ca9b4368` (`memtest/memtest_2026-11-07.json`):
- 4 of 5 checks passed;
- it failed "no late growth within a run" on 3 of 20 runs (+186, +101 and +167 MB). Each was a single step late in
  the run, flat afterwards, and repeated within 3% in the other pass.

**Why the first rule was mis-specified** (not "because it failed"):
- The instruction was "memory flat within a run and released after each". Its concern is accumulation: memory
  that grows with work done, and so compounds over a long run or over 640 runs.
- The check written for it compared the peak of a run's last quarter of samples with the peak of the rest. That
  measures *where* in a run the peak falls, not whether memory grows with work.
- In a 35-60 second scripted run, the heaviest phase (the final test run and scoring) falls in the last quarter by
  construction. So the check fails an ordinary working set and cannot tell a leak from no leak.

**The new rule** (decision of 2026-11-07, verbatim):

"Memory test passes if: (a) within a run, memory plateaus before the run ends and does not grow with the number of
tool calls; (b) across runs, peak memory repeats within 10%; (c) memory is released after each run exits; (d) no
orphaned processes survive. A single heavy phase late in a run is expected, not a failure."

**How `memtest.py` measures it:**
- (a), plateau: neither of the child's last two steps between samples rises by more than max(2% of its peak, 10 MB).
  A flat or falling end passes; a still-climbing end fails.
- (a), tool calls:
  - every run first makes 24 read-only calls (`dryrun.pad_calls`), cycling `read_file`, `grep`, `list_dir` and the
    version's index-backed tool (`prism_change_context`, or `related_code`). The tree is unchanged by them.
  - The loop logs the process's RSS after each call (`rss_mb`, a new field of the turn log).
  - The least-squares slope over pad calls 5 to 24 (the first 4 include building the index) must be at most 1 MB per
    call. 1 MB per call bounds a run at the 30-turn cap to 30 MB, and each R3a run is its own process.
  - The turn cap is 60 in this test only, so that 24 pad calls and the gold edits fit.
- (b): each (task, version, script) runs twice; the second peak is within 10% of the first (as before).
- (c): the child has exited and holds no memory before the next run starts, and the driver's RSS grows by less
  than 50 MB over the 20 runs (as before).
- (d): no process is left whose command line or working directory is in a run's `/tmp/r3run_*` worktree.
- Every run must also end as its script should: gold resolved, empty unresolved (as before).
- The first rule's late-growth figure is still recorded per run, as a diagnostic only.

**The rerun:** fresh, 20 runs on the widened pilot tasks, after this amendment is committed. Its result is
recorded in BUILD_PLAN section 7 and IMPLEMENTATION_LOG.
