"""The per-turn progress summary (amendment R4-SANITY-4, the ledger): built by the harness from tool results, never
written by the model, and shown at the end of the newest message on every turn, so it survives the window rule.

It states facts the model already has or could get itself, the same in both arms:
- the turn count;
- the edits applied and rejected, with reasons;
- the model's own last `run_tests` result, including any vanished-test warning;
- done rejections;
- for a delete task, the target's current status (the gate's file check, a parse of one file).
It never runs tests by itself, never names callers or places to fix, and never uses the hidden tests.
"""
from __future__ import annotations

import re
from collections import Counter

MAX_EDITS_LISTED = 8
MAX_TEST_LINES = 4


class ProgressLedger:
    def __init__(self, turn_cap: int, gate=None):
        self.turn_cap, self.gate = turn_cap, gate
        self.applied: list[str] = []
        self.rejected: Counter = Counter()
        self.last_tests: tuple[int, list[str]] | None = None
        self.done_rejections: list[tuple[int, str]] = []

    def update(self, turn: int, tool: str, args: dict, output: str, gate: dict | None = None) -> None:
        if tool == "edit_file":
            m = re.match(r"(edited|created) (\S+)(?: at line (\d+))?", output)
            if m:
                self.applied.append(f"{m.group(2)}" + (f" line {m.group(3)}" if m.group(3) else " (new file)"))
            else:
                path = (args or {}).get("path", "?")
                kind = ("not found" if "not found" in output else "duplicate match" if "occurs" in output
                        else "empty `old` on an existing file" if "cannot be empty" in output or "exists" in output
                        else "other error")
                self.rejected[(path, kind)] += 1
        elif tool == "run_tests":
            lines = [ln for ln in output.splitlines()
                     if re.match(r"^\S+: (\d+ passed|the test run produced no results|no such test file)", ln)
                     or ln.startswith("warning:") or ln.startswith("the build failed")]
            self.last_tests = (turn, lines[:MAX_TEST_LINES * 2])
        elif tool == "done" and gate is not None and not gate.get("accepted"):
            first = next((ln[2:] for ln in (gate.get("message") or "").splitlines() if ln.startswith("- ")), "")
            self.done_rejections.append((turn, first[:200]))

    def render(self, next_turn: int) -> str:
        out = [f"Progress so far (from the tool results, not from you). This is turn {next_turn} of {self.turn_cap}."]
        if self.applied:
            shown = self.applied[-MAX_EDITS_LISTED:]
            more = len(self.applied) - len(shown)
            out.append(f"- edits applied ({len(self.applied)}): " + "; ".join(shown) + (f"; … {more} earlier" if more else ""))
        else:
            out.append("- edits applied: none yet")
        if self.rejected:
            out.append("- edits rejected: " + "; ".join(f"{p}: {k} ×{n}" for (p, k), n in self.rejected.most_common(4)))
        if self.last_tests:
            t, lines = self.last_tests
            out.append(f"- your last test run (turn {t}): " + (" | ".join(lines) if lines else "no results"))
        else:
            out.append("- tests: you have not run them yet")
        if self.done_rejections:
            t, first = self.done_rejections[-1]
            out.append(f"- done rejected {len(self.done_rejections)}× (last at turn {t}: {first})")
        status = self.gate.target_status() if self.gate is not None and self.gate.task.get("family") == "delete" else None
        if status:
            out.append(f"- target: {status}")
        elif self.gate is not None and self.gate.task.get("family") == "delete":
            out.append("- target: no longer defined in its file")
        return "\n".join(out)
