"""The verification gate at done (amendment R4-SANITY-2, `AMENDMENTS.md`): a `done` call is accepted only if the
working tree meets the task's goal. Both arms get the same gate.

The gate checks only what the model could check itself, with its own tools:
- the file it was asked to change;
- the visible tests.
It never uses the hidden scoring tests or PRISM's output, and it says what is unmet, never where to fix it (no call
sites).

| Family | Check |
|---|---|
| every family | at least one file changed since the start |
| delete | the target is no longer defined in its file (the scorer's own `must_be_absent` check), and no visible test that passed at the start (3 runs, flaky dropped: `visible_start_pass`) now fails. A test or test file the model deleted does not count |
| feature | the listed fail-to-pass tests pass and the pass-to-pass tests still pass, with the test files restored to their start version for the check (the model's own versions are put back after) |
| B | no visible test that passed at the start now fails |
"""
from __future__ import annotations

import os
import re
import subprocess

from harness.experiments.r3.runner import id_status, snapshot_tree

MAX_NAMED = 5           # failing tests named in a rejection
ERROR_LINES = 3


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, errors="replace")


def _name_in(test_id: str, text: str) -> bool:
    """Whether a test's own name still appears in its file's text (pytest: the function name, parameters stripped;
    vitest/jest: the full name, or its last part)."""
    name = test_id.rsplit("::", 1)[-1].split("[", 1)[0].strip()
    if not name:
        return True
    if name in text:
        return True
    tail = name.split(" > ")[-1]
    return len(tail) >= 6 and tail[-40:] in text


def error_lines(text: str, n: int = ERROR_LINES) -> list[str]:
    """The first lines of a test run's output that state an error (pytest's `E ` lines, `...Error:` lines)."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", text or "")
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if (s.startswith("E ") or re.search(r"\b\w*(Error|Exception)\b[:(]", s) or "ERROR:" in s
                or s.startswith("Transform failed")) and s not in out:
            out.append(s[:200])
        if len(out) >= n:
            break
    return out


class DoneGate:
    def __init__(self, task: dict, work: str, env, start_tree: str):
        self.task, self.work, self.env, self.start_tree = task, work, env, start_tree

    # ------------------------------------------------------------------ helpers
    def changed_files(self) -> list[str]:
        now = snapshot_tree(self.work)
        diff = _git("diff", "--name-only", "--no-renames", self.start_tree, now, cwd=self.work).stdout.split("\n")
        return [f for f in diff if f and not f.startswith(".r3-")]

    def start_version(self, path: str) -> str | None:
        r = _git("show", f"{self.start_tree}:{path}", cwd=self.work)
        return r.stdout if r.returncode == 0 else None

    def _build(self) -> str | None:
        ensure = getattr(self.env, "ensure_current_build", None)
        if ensure is None:
            return None
        try:
            ensure()
            return None
        except Exception as exc:
            return str(exc)[-300:]

    def _run(self, files: list[str]) -> dict[str, dict]:
        return {f: self.env.run_test(f) for f in files if os.path.isfile(os.path.join(self.work, f))}

    def regressions(self, start_pass: dict[str, list[str]]) -> list[str]:
        """Problems: visible tests that passed at the start and now fail, and test files that now fail to run."""
        files = sorted(start_pass)
        runs = self._run(files)
        changed = set(self.changed_files())
        failing, broken, vanished, errs = [], [], [], []
        for f in files:
            if f not in runs:
                continue                                   # the model deleted the file
            r = runs[f]
            tests = r.get("tests") or {}
            present = [t for t in start_pass[f] if id_status(tests, t, self.work) != "missing"]
            absent = [t for t in start_pass[f] if t not in present]
            # R4-SANITY-4 (bug 2): an absent test counts as deleted by the model only if the model changed this test
            # file and the test's name is gone from it. Otherwise it vanished: e.g. a syntax error that breaks one of
            # the suite's projects (zod's type-checked copy) while the plain copy still passes.
            text = open(os.path.join(self.work, f), errors="replace").read() if f in changed else None
            gone = [t for t in absent if text is None or _name_in(t, text)]
            if not r.get("parsed") or (not present and (r.get("failed") or gone)):
                broken.append(f)                           # the file no longer loads or runs
                errs += error_lines(r.get("text") or r.get("tail", ""))
                continue
            bad = [t for t in present if id_status(tests, t, self.work) != "passed"]
            if bad:
                failing += [f"{f}::{t}" for t in bad]
                errs += error_lines(r.get("text") or r.get("tail", ""))
            if gone:
                vanished += [f"{f}::{t}" for t in gone]
                errs += error_lines(r.get("text") or r.get("tail", ""))
        out = [f"test file {f} now fails to load or run" for f in broken]
        if vanished:
            out.append(f"{len(vanished)} tests that passed at the start did not run (the file, or code it imports, may "
                       f"fail to load or compile): " + ", ".join(vanished[:MAX_NAMED])
                       + (" …" if len(vanished) > MAX_NAMED else ""))
        if failing:
            out.append(f"{len(failing)} tests that passed at the start now fail: "
                       + ", ".join(failing[:MAX_NAMED]) + (" …" if len(failing) > MAX_NAMED else ""))
        if errs:
            out.append("first errors: " + " | ".join(list(dict.fromkeys(errs))[:ERROR_LINES]))
        return out

    # ------------------------------------------------------------------ per family
    def target_status(self) -> str | None:
        """Delete: "`NAME` is still defined in FILE at line N" while it is, else None (a fact, not an instruction)."""
        f, q = self.task["must_be_absent"].split("::", 1)
        path = os.path.join(self.work, f)
        from harness.experiments.change_history.mine import functions
        if not os.path.isfile(path):
            return None
        defs = functions(f, open(path, "rb").read())
        if q not in defs:
            return None
        line = defs[q].get("start") or defs[q].get("line")
        name = self.task.get("target_display") or f"`{q}`"
        return f"{name} is still defined in {f}" + (f" at line {line}" if line else "")

    def _delete(self) -> list[str]:
        status = self.target_status()
        return ([status] if status else []) + self.regressions(self.task.get("visible_start_pass") or {})

    def _feature(self) -> list[str]:
        tests = sorted(set(self.task.get("visible_tests") or []) | set(self.task.get("test_files") or []))
        saved = {}
        for t in tests:                                     # the task's own tests, as at the start
            p = os.path.join(self.work, t)
            saved[t] = open(p, "rb").read() if os.path.isfile(p) else None
            start = self.start_version(t)
            if start is None:
                if os.path.exists(p):
                    os.remove(p)
            else:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                open(p, "w").write(start)
        try:
            err = self._build()
            if err:
                return [f"the project does not build: {err}"]
            files = sorted(set(self.task.get("fail_to_pass") or {}) | set(self.task.get("pass_to_pass") or {}))
            runs = self._run(files)
            f2p_bad, p2p_bad, errs = [], [], []
            for group, bad in (("fail_to_pass", f2p_bad), ("pass_to_pass", p2p_bad)):
                for f, ids in (self.task.get(group) or {}).items():
                    r = runs.get(f) or {}
                    for t in ids:
                        if id_status(r.get("tests") or {}, t, self.work) != "passed":
                            bad.append(f"{f}::{t}")
                    if bad and r:
                        errs += error_lines(r.get("text") or r.get("tail", ""))
            out = []
            if f2p_bad:
                out.append(f"{len(f2p_bad)} of the listed tests still fail: " + ", ".join(f2p_bad[:MAX_NAMED])
                           + (" …" if len(f2p_bad) > MAX_NAMED else ""))
            if p2p_bad:
                out.append(f"{len(p2p_bad)} other tests in those files that passed at the start now fail: "
                           + ", ".join(p2p_bad[:MAX_NAMED]) + (" …" if len(p2p_bad) > MAX_NAMED else ""))
            if errs:
                out.append("first errors: " + " | ".join(list(dict.fromkeys(errs))[:ERROR_LINES]))
            return out
        finally:
            for t, data in saved.items():                   # the model's own versions back
                p = os.path.join(self.work, t)
                if data is None:
                    if os.path.exists(p):
                        os.remove(p)
                else:
                    os.makedirs(os.path.dirname(p), exist_ok=True)
                    open(p, "wb").write(data)

    # ------------------------------------------------------------------ the check
    def check(self) -> tuple[bool, str]:
        if not self.changed_files():
            # R4-SANITY-3 (fix 1): a delete task's target status is stated here too
            status = self.target_status() if self.task.get("family") == "delete" else None
            return False, ("Done rejected: no file has been changed yet. The task has not been started."
                           + (f"\n- {status}" if status else "") + "\nContinue.")
        fam = self.task.get("family")
        if fam == "feature":
            problems = self._feature()
        else:
            err = self._build()
            if err:
                problems = [f"the project does not build: {err}"]
            elif fam == "delete":
                problems = self._delete()
            else:
                problems = self.regressions(self.task.get("visible_start_pass") or {})
        if not problems:
            return True, "done"
        return False, "Done rejected: the task is not complete.\n" + "\n".join(f"- {p}" for p in problems) + \
            "\nContinue."
