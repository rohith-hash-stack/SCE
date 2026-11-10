# Context compaction for Sanity 1: recommendation (2026-11-10; for the user's decision)

**Sanity 1 compares two PRISM-only arms, which differ in compaction alone:**
- **with compaction:** proactive, structured compaction (below);
- **without compaction:** overflow-only. R3's existing window rule applies only when the next call would not fit
  (§5).

## 1. Recommendation

| Question | Recommendation | Reasoning |
|---|---|---|
| **Type** | **Deterministic.** A harness rule, with no model call | Same tree and same seed, same context: runs are reproducible, and a difference between arms is the rule's, not a summarizer's. It adds no tokens, no GPU time and no second source of mistakes. In this loop most of the context is tool output, and the agent's own turns are one-line JSON calls. So there is little reasoning for a model to summarize, and what can be cut deterministically is the bulk |
| **Threshold** | compact when the next call's prompt would pass **70%** of the context window (14,336 of 20,480 tokens), down to a low-water mark of **50%** (10,240) | 70% leaves the 4,096-token answer room plus a margin for the next tool output (up to the 4,000-token cap). Compacting down to 50% gives hysteresis: about one compaction every several turns, not one every turn |
| **What is dropped first** | in tiers, oldest first within each tier (§2) | superseded information first, then old bulk, then old feedback. The agent's own calls are never dropped. The latest state of everything is always kept |
| **Recurring or one-shot** | **recurring.** Checked before every call; it fires whenever the prompt is above 70%, and runs down to 50% | one-shot cannot bound a 28-turn run |
| **Always preserved** | the system prompt; the task message; **the latest 3 turns in full**; the latest test result (pinned, C2); the latest check feedback (`CHECK_CHANGES_DESIGN.md` §8); a harness-written **edit ledger** (§3) | these are what the agent needs to act next: the goal, the current state of the tests and the check, and what it has already changed |
| **Symmetry between the two PRISM arms** | the arms differ only in this rule. The model, prompt, tools, gate, caps and seed are identical. Inside the compaction arm, tool types are treated alike: stubs are by size and age, never by tool name | Sanity 1 asks whether compaction helps PRISM. Any other difference would confound it |

## 2. Drop tiers (the compaction arm)

Applied in order until the prompt is at or below 50%. A dropped output becomes a one-line stub: "[`tool(args)` at
turn N: K tokens, removed by compaction; call it again if you need it]".

| Tier | What | Why first |
|---|---|---|
| 1 | **superseded outputs:** a `read_file` of a file edited since; a PRISM answer about a symbol queried again since, or whose file was edited since; a `run_tests` output older than the pinned latest; a check feedback older than the latest | stale by construction: the content no longer describes the tree |
| 2 | **old bulk:** outputs older than the latest 3 turns, oldest first | the largest share of tokens, and the least likely to be needed exactly |
| 3 | **old `edit_file` results** beyond the ledger (the ledger keeps the facts) | the ledger holds what changed |
| 4 | only if still above 50%: the oldest turns' call lines | last resort. The latest 3 turns, the task and the system prompt are never dropped |

## 3. The edit ledger

A harness-written block, updated after each edit and kept at the end of the task message. It holds, per edited file:
the lines changed and the latest check's boolean ("`src/a.py`: lines 40–52, 88 (3 edits); last check: clean").
- **Built from the harness's own state,** not by a model, so it is deterministic.
- **It is what lets tiers 1–3 drop old outputs** without the agent losing track of its own work.
- **It is in the compaction arm only.** It is part of what "compaction" means here.

## 4. Alternatives considered

| Alternative | Why not |
|---|---|
| **Model-based summarization** (at the threshold, the model summarizes old turns, as in Claude Code or Copilot) | It adds a second stochastic step, and its quality becomes a confound. It costs extra model calls on the T4s, slow with a 14B model and a 20k window. It is harder to pre-register exactly. A summarizer can also drop or blur PRISM's certainty labels ("possible" written as fact), which spec Q1 forbids. Worth testing only if the deterministic rule leaves the agent losing track of its goal, which Sanity 1 will show |
| **Hybrid** (the harness prunes tool outputs; the model summarizes reasoning) | Here the agent's reasoning is one-line JSON tool calls, so there is almost nothing to summarize. It carries the model-based costs for little gain |
| **Drop whole oldest turns** (FIFO) | It breaks call-and-output pairs, and loses early context that tiers 1–2 would keep as stubs |
| **One-shot compaction** | It does not bound long runs |
| **A lower per-call output cap** instead of compaction | A different mechanism: it cuts information before the agent has seen it. The 4,000-token cap stays the same in both arms |
| **A larger context window** instead | Kaggle's T4 memory limits the 14B model's window. It also changes every arm, so it is a separate decision |

## 5. What the arm without compaction does at the limit

**R3's window rule, unchanged:** it stubs the oldest tool outputs, then drops the oldest turns, only when the next call
would not fit 20,480 tokens. Without that fallback, Ollama silently truncates the front of the context, the system
prompt included. That is not a usable control.
- **Each overflow event is logged,** so Sanity 1 can see how often the arm without compaction is forced into it.

## 6. The contrast Sanity 1 measures

**Proactive, structured compaction** (at 70%, superseded first, a ledger, the latest state pinned) **against
overflow-only stubbing** (R3's rule at 100%).
