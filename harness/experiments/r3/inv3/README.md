# R3a floor investigation: scripts and outputs (`../R3A_FLOOR_INVESTIGATION.md`)

Run in the session sandbox on 2026-11-10. The scripts name that sandbox's scratch paths (local clones, environment
caches) at the top; change them to rerun elsewhere. Neither script changes the repository.

| File | Step | What |
|---|---|---|
| `gold_score.py TASK_ID` | 1 | applies the developer's fix after the run's own setup and scores it with `runner.score()` |
| `step1.jsonl` | 1 | its output for the 10 tasks |
| `step2.txt` | 2 | the 130 early-done runs: tool sequences, and the 10 sampled transcripts |
| `start_tests.py TASK_ID [hidden]` | 2, 5 | the agent's `run_tests` at the start state, through `SharedTools`; `hidden` also runs the fail-to-pass files with the hidden patch applied |
| `start_r3a-B-*.txt` | 2 | full start-state `run_tests` output for the 18 "all pass" tasks |
| `start_class.json` | 2 | per task: what start-state `run_tests` shows (from the Kaggle logs' heads) |
| `step4.txt` | 4 | the 10 sampled one-caller runs: edits made, and the developer's diff |
| `step5_r3a-B-*.txt` | 5 | `run_tests` at the start, and with the hidden tests applied |
