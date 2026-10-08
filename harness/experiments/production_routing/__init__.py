"""Production routing experiment: query-intent classifier + pool shaping for
Arm 5 (R2, off unless HARNESS_PRISM_PRODUCTION_ROUTING is set), and the T5
rule selector (R0, Arm 5's default on T5 since the R1 vs R0 ablation;
HARNESS_PRISM_T5_RULE_SELECTOR=0 turns it off). See README.md."""
