# Set 5 (Hono) decision - pre-registered rules (set5/README.md)

**Rule 1:** KEEP the test-file exclusion  
**Rule 2:** NOT CONFIRMED on Hono: investigate (shipped setting unchanged)

| arm | recall@5 | recall@20 | MRR |
|---|---|---|---|
| A | 0.65 | 0.833 | 0.485 |
| B | 0.683 | 0.833 | 0.479 |
| C | 0.767 | 0.833 | 0.515 |

| comparison | better / worse / ties | sign p | recall@5 delta | MRR delta |
|---|---|---|---|---|
| B vs A | 5 / 4 / 21 | 1.0 | +0.033 | -0.006 |
| C vs B | 6 / 6 / 18 | 1.0 | +0.083 | +0.036 |

Summary index: coverage 1.0, weight 0.5, locked pipeline, coverage >= 0.95.
