# Stage 1 (summarizer candidates): outcome and diagnostics

Binding result: `STAGE1.md`, under the gates approved before the run
(CANDIDATE_V3.md). **Both candidates fail and are dropped; v2 stays locked
and shipped. Stages 2 and 3 do not run for v3 or v3-name.**

| | v3-name | v3 |
|---|---|---|
| flagged on the first pass | 886 / 3,569 (25%) | 1,540 (43%) |
| rewritten by repair | 499 | 734 |
| restates_name after repair (gate <= 10%) | 10.3% - FAIL | 12.3% - FAIL |
| too_short after repair (gate <= 15%) | - | 20.7% - FAIL |
| v2 flags (gate <= 5% each) | pass (<= 0.5%) | pass |
| lost / unchecked | 0 / 0 | 0 / 0 |

**Run health.** All 30 steps (10 repositories x v2, v3-name, v3) completed,
about 70 minutes on T4 x2, no errors in any step log; every v2 index resolves
to the shipped weight 0.5; no credentials in the artifacts.

**What the repair pass does with these flags (diagnostics, not part of the
gates).** Of v3-name's 499 rewrites, 210 (42%) add three or more new content
words - real additions ("Decodes Google ID token." -> "Decodes Google ID token
using JWT library."); 90 (18%) pass the lint by adding at most one word,
mostly a synonym for the name's own verb ("Handles errors for a specific
routing layer." -> "Processes errors for a specific routing layer."); and 387
of the 886 flagged summaries are not rewritten at all (neither repair attempt
lowered the flag count, so the original is kept). The repair cleared about
58% of what it was asked to fix; the gate needed about 63%. v3's length rule
adds mostly padding ("...for TRPC in Fastify server." -> "Handles error
formatting for TRPC in a Fastify server.") and leaves 20.7% short.

**Reading.** The lint finds the thin summaries; the repair prompt mostly
rephrases them instead of adding behaviour, because the card it sees is the
same card that produced the thin sentence. A next candidate would change
what the model sees or is asked for (for example the summary prompt itself,
or a card with the function body), not only which summaries go to repair -
and would need its own fresh validation.
