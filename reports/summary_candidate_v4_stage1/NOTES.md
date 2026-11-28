# Candidate v4, Stage 1 - audit notes

Kaggle run of `kaggle/summary_candidate_v4_cell.py` at code e02fa67 (STAGE1.md).

**Gates: PASS.** After escalation restates_name 5.8% pooled (gate 10%; pass 1
24.8%; v3-name's repair reached 10.3%); v2 flags 0-0.1%; nothing lost; all
entries checked. The gates were the ones proposed in CANDIDATE_V4.md; the cell
ran before they were explicitly approved.

Audit of the artifacts:

- **Clean conversion.** Every repo: `to summarize 0` (v2 pass 1 reused as is),
  no errors in the logs; the only changed summaries are escalated ones, and
  every escalated entry's `pass1` equals its v2 summary.
- **886 escalations tried, 676 kept (76%), 210 rejected** - all 886 recorded
  `effects`. A sample of the rejections is mostly genuinely trivial functions
  ("Returns current UTC datetime.", "Converts string to uppercase."), where
  restating the name is the truth.
- **Per repo, zod stays at 13.9%** (above 10%; the gate is pooled): its many
  one-line schema methods (`toUpperCase`, `keySchema`) are the trivial case.
- **Longer summaries**: kept rewrites median 11 words (pass 1: 7), p90 15; 33
  over 16 words, 6 over 20 (the prompt asks 8-14). Not a gate; it lengthens the
  summary field BM25 sees for those functions.
- **Type names** (PascalCase, e.g. "returns EmailData", "FileError"): 220
  summaries in v4 vs 216 in v2 - unchanged. The `identifier` rule does not
  catch PascalCase in either; a pre-existing lint gap, not a v4 effect.
- **Accuracy is not measured by any gate.** The samples read more specific
  and mostly correct (`httpie.uploads.wrapped`: v2 "Caches properties by
  wrapping a function." -> v4 "Wraps a function to compute a chunk, send a
  callback, and return the chunk."), but the lint cannot tell a right new
  detail from a wrong one; stage 2/3 retrieval is the test of that.

Next per CANDIDATE_V4.md: stage 2 (sets 2-5, diagnostic, V4 vs V2 at w = 0.5),
then the fresh set 6.
