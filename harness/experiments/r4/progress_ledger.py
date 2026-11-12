"""The per-turn progress summary (amendment R4-SANITY-4, the ledger): built by the harness from tool results, never
written by the model, and shown at the end of the newest message on every turn after the first, so it survives the
window rule.

Seven deterministic items, the same in both arms:
1. the turn number (N of the cap);
2. the files read so far, with the line ranges shown (from `read_file`'s own header);
3. the edits applied (file, line, what changed: "replaced 7 lines with 1", "created, 40 lines");
4. the edits failed: the count, and the last 3 with turn, file and reason;
5. the latest test result against the start state: per file, how many of the tests that ran at the start pass now,
   and how many now fail or did not run. Only from the model's own `run_tests`: the summary never runs tests;
6. task progress: for delete, the target's status (the gate's file check); for feature, how many listed tests still
   fail, as of the model's last run;
7. a one-line reminder of the goal.

It never names callers or places to fix and never uses the hidden tests.
"""
from __future__ import annotations

import re

MAX_FILES_LISTED = 6
MAX_EDITS_LISTED = 6
FAILED_LISTED = 3


def _merge(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


class ProgressLedger:
    def __init__(self, turn_cap: int, gate=None, task: dict | None = None, shared=None):
        """`gate`: the DoneGate (delete's target check). `task`: for the goal line and the feature counts. `shared`:
        the SharedTools, for each file's last test results and the start lists (`expected`)."""
        self.turn_cap, self.gate, self.shared = turn_cap, gate, shared
        self.task = task if task is not None else (gate.task if gate is not None else {})
        self.read: dict[str, list[tuple[int, int]]] = {}
        self.read_total: dict[str, int] = {}
        self.applied: list[str] = []
        self.failed: list[tuple[int, str, str]] = []
        self.last_test_turn: int | None = None
        self.done_rejections: list[tuple[int, str]] = []

    # ------------------------------------------------------------------ updates, after each tool call
    def update(self, turn: int, tool: str, args: dict, output: str, gate: dict | None = None) -> None:
        args = args or {}
        if tool == "read_file":
            m = re.match(r"(\S+) \(lines (\d+)-(\d+) of (\d+)", output)
            if m:
                self.read.setdefault(m.group(1), []).append((int(m.group(2)), int(m.group(3))))
                self.read_total[m.group(1)] = int(m.group(4))
        elif tool == "edit_file":
            m = re.match(r"edited (\S+) at line (\d+): replaced (\d+) lines with (\d+)", output)
            c = re.match(r"created (\S+) \((\d+) lines\)", output)
            if m:
                self.applied.append(f"{m.group(1)}:{m.group(2)} (replaced {m.group(3)} lines with {m.group(4)})")
            elif c:
                self.applied.append(f"{c.group(1)} (created, {c.group(2)} lines)")
            else:
                kind = ("not found" if "not found" in output else "duplicate match" if "occurs" in output
                        else "empty `old` on an existing file" if "cannot be empty" in output or "exists" in output
                        else "no such file" if "no file" in output else "other error")
                self.failed.append((turn, args.get("path", "?"), kind))
        elif tool == "run_tests":
            self.last_test_turn = turn
        elif tool == "done" and gate is not None and not gate.get("accepted"):
            first = next((ln[2:] for ln in (gate.get("message") or "").splitlines() if ln.startswith("- ")), "")
            self.done_rejections.append((turn, first[:200]))

    # ------------------------------------------------------------------ the summary
    def _goal(self) -> str:
        t = self.task
        if t.get("family") == "delete" and t.get("must_be_absent"):
            f, q = t["must_be_absent"].split("::", 1)
            name = t.get("target_display") or f"`{q}`"
            return f"Goal: remove {name} from {f}; the tests that passed at the start must still pass."
        if t.get("family") == "feature":
            n = sum(map(len, (t.get("fail_to_pass") or {}).values()))
            return (f"Goal: make the {n} listed tests pass without breaking the other tests in their files; change the "
                    f"source code, not the tests.")
        return "Goal: as stated in the task; the tests that passed at the start must still pass."

    def _status(self, results: dict, test_id: str) -> str:
        from harness.experiments.r3.runner import id_status
        return id_status(results.get("tests") or {}, test_id, self.shared.work if self.shared else ".")

    def _tests_vs_start(self) -> list[str]:
        if self.last_test_turn is None or self.shared is None:
            return ["- tests: you have not run them yet"]
        out = [f"- your latest test results (last run at turn {self.last_test_turn}), against the start:"]
        expected = getattr(self.shared, "expected", {}) or {}
        for f, r in sorted(self.shared.last_results.items()):
            start = expected.get(f, [])
            if not r.get("parsed"):
                out.append(f"  - {f}: no results (the file failed to run)")
                continue
            if start:
                ok = sum(self._status(r, t) == "passed" for t in start)
                bad = len(start) - ok
                out.append(f"  - {f}: {ok} of the {len(start)} tests that ran at the start pass now"
                           + (f"; {bad} fail or did not run" if bad else ""))
            else:
                out.append(f"  - {f}: {r.get('passed', 0)} passed, {r.get('failed', 0)} failed")
        return out

    def _progress(self) -> str | None:
        fam = self.task.get("family")
        if fam == "delete" and self.gate is not None:
            status = self.gate.target_status()
            return f"- target: {status}" if status else "- target: no longer defined in its file"
        if fam == "feature":
            f2p = self.task.get("fail_to_pass") or {}
            n = sum(map(len, f2p.values()))
            if self.shared is None or not any(f in self.shared.last_results for f in f2p):
                return f"- listed tests: {n} to make pass; not run yet"
            lr = self.shared.last_results
            still = sum(1 for f, ids in f2p.items() if f in lr for t in ids if self._status(lr[f], t) != "passed")
            unrun = sum(len(ids) for f, ids in f2p.items() if f not in lr)
            files_unrun = [f for f in f2p if f not in lr]
            return (f"- listed tests: {still} of {n} still fail (as of your last run)"
                    + (f"; {unrun} of them are in files you have not run: {', '.join(files_unrun)}" if unrun else ""))
        return None

    def render(self, next_turn: int) -> str:
        out = [f"Progress so far (from the tool results, not from you). This is turn {next_turn} of {self.turn_cap}.",
               self._goal()]
        if self.read:
            files = list(self.read)[-MAX_FILES_LISTED:]
            parts = [f"{f} lines " + ",".join(f"{a}-{b}" for a, b in _merge(self.read[f]))
                     + f" (of {self.read_total[f]})" for f in files]
            more = len(self.read) - len(files)
            out.append("- files read: " + "; ".join(parts) + (f"; … {more} more" if more else ""))
        else:
            out.append("- files read: none yet")
        if self.applied:
            shown = self.applied[-MAX_EDITS_LISTED:]
            more = len(self.applied) - len(shown)
            out.append(f"- edits applied ({len(self.applied)}): " + "; ".join(shown) + (f"; … {more} earlier" if more else ""))
        else:
            out.append("- edits applied: none yet")
        if self.failed:
            last = "; ".join(f"turn {t}, {p}: {k}" for t, p, k in self.failed[-FAILED_LISTED:])
            out.append(f"- edits failed ({len(self.failed)}); the last {min(FAILED_LISTED, len(self.failed))}: {last}")
        out += self._tests_vs_start()
        prog = self._progress()
        if prog:
            out.append(prog)
        if self.done_rejections:
            t, first = self.done_rejections[-1]
            out.append(f"- done rejected {len(self.done_rejections)}× (last at turn {t}: {first})")
        return "\n".join(out)
