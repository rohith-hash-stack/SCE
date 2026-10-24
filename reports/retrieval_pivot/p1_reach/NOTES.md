# P1 extension: packer reach and 4-axis helper profiles (offline, no model)

Exploratory - no criterion was pre-registered for this step. Report: `P1.md` (this directory);
rows: `p1.json`. Feasible cells (every arm and reach fits the budget): 62 of 87 case x budget cells.

## Why the packer stops early

Of the 78 gold nodes outside the production packer's candidate pool (29 cases): 22 have no path
to the seed in the graph at all; 20 are reachable only sideways (a caller's other callees, and
similar); 18 are callers within weighted distance 3; 7 callers are farther; 11 callees are beyond
`d_max`. The packer only ever considers the seed's **direct** callers upstream
(`graph.predecessors(seed)`, `upstream_max_hops` 1.5, frontier cap 3), so raising its distance
limits cannot reach callers of callers.

R1 therefore keeps the production pack and its order, then fills ring by ring: nodes 2, 3 and 4
hops from the seed, in any direction, nearer rings first, and within a ring those with the most
links to already-packed nodes first, while they fit the remaining budget.

## Results (feasible cells)

| budget | R0 A0 (today) | R1 A0 (reach) | R1 A1n (reach + outer sig) | R1 A4 (+ helper profiles) | R1 A2 (helpers compressed) |
|---|---|---|---|---|---|
| 2k (71 gold) | 28, 28 full | 30, 30 full | 42, 32 full | 42, 32 full | 54, 0 full |
| 4k (111 gold) | 52, 52 full | 65, 65 full | 75, 53 full | 74, 53 full | 81, 0 full |
| 8k (144 gold) | 69, 69 full | 93, 93 full | 99, 67 full | 99, 67 full | 100, 0 full |

(gold present, of which at full body). Gold edges with both ends present at 8k: 36 -> 56 (R1 A0)
-> 58 (R1 A1n) of 59. Ceiling at 8k: 85 / 144 inside the production pool, 116 / 144 within ring 4.

1. **Reach is the large lever.** With full bodies everywhere (A0), filling the unused budget lifts
   gold present at 8k from 69 to 93 (48% -> 65%), all of it full code, and gold edges 36 -> 56.
   R0 used 5.0k of the 8k budget on average; R1 uses 7.9k.
2. **Reach and outer compression compound.** R1 A1n: 75 / 111 at 4k (today 52) and 99 / 144 at
   8k (today 69), at the cost of full bodies (67 vs 93 at 8k for R1 A0).
3. **4-axis helper profiles (A4, Direction A) are cheap and neutral offline.** 53-153 tokens per
   payload (about 2-4%), the same gold as A1n (+-1). No bloat - and, like summaries, their value
   (does the model understand the helpers better?) can only be measured with a model.
   **Profiles on every outer node (A5) bloat**: up to 1,245 tokens at 8k, slightly less gold.
4. **Noise rises with reach**: 75% -> 80% of tokens on nodes that are neither seed nor gold at 8k
   (gold lists are not exhaustive, so this is an upper bound). The fill orders by ring and links,
   not by relevance to the task.
5. Direction B (helper code centred, anchor as context) was not built: offline it only moves which
   node keeps its body, which A0/A2 already bracket.

## Next

- A shipped version of R1 belongs in `prism.packer.submodular_knapsack` (a budget-remaining
  pass after the existing loop). It needs the production renderer's budget enforcement and its
  own tests, and should be validated on a set other than these 29 cases, which shaped it.
- The model-facing test (P3) now has its arms: R1 A0 (reach, full code), R1 A1n (reach + outer
  signatures), R1 A4 (+ helper profiles), with R0 A0 as today's baseline.
