"""Phase 1, item 2: deterministic history compaction, applied before the window rule (`AgentRun(compact=True)`).

**Why:** the run budget counts every prompt token on every turn, and the window rule acts only at the 16K edge, so late
prompts sit at 12-17K and 9 of 40 Phase 0 runs ended on the budget. Compaction shrinks what is re-sent every turn
without changing how tokens are counted.

**Rules** (the same in both arms; built from tool outputs only, never by a model; the newest output of each kind is
never touched):

1. **Superseded reads:** a `read_file` output whose lines were read again later (the later range covers it) becomes one
   line pointing to the newer read.
2. **Stale reads:** a `read_file` output whose lines a later applied edit changed (the edit's replaced span overlaps
   the read's range) becomes one line saying the text changed. An edit elsewhere in the file only shifts line numbers,
   so it leaves the read alone. (Sanity 4: most failed edits reused text from before an earlier edit.)
3. **Repeated searches:** a `grep` (or `list_dir`) output repeated later with the same arguments becomes a pointer.
4. **Old outputs** (more than `keep_recent` tool outputs ago) are summarised by kind:
   - `read_file`: one line (file, lines, size; read again for the text);
   - `run_tests`: the per-file count lines and at most 5 failing test names per file;
   - `grep` / `list_dir`: the first lines;
   - an index tool (`prism_*`, `related_code`, and Phase 3's `find_references`, `semantic_search`, `ast_search`,
     `run_shell`): the first lines, the same rule in every arm.
   Short outputs (edits, done, errors) are kept.

The progress ledger (files read, edits, latest tests) is untouched and still appended to the newest message, so the
facts the summaries drop are kept there.
"""
from __future__ import annotations

import re

KEEP_RECENT = 6            # tool outputs kept in full, counting back from the newest
SUMMARY_LINES = {"grep": 8, "list_dir": 12, "index": 12}
#: the non-PRISM tools summarised as index tools: R1's control and, since Phase 3, BASELINE_LSP's IDE-agent tools
INDEX_TOOLS = ("related_code", "find_references", "semantic_search", "ast_search", "run_shell")
#: reads of numbered file lines: `prism_read` (Phase 2 D2) is `read_file`'s output with a PRISM footer
READS = ("read_file", "prism_read")
FAILING_NAMES = 5
SHORT_CHARS = 400          # outputs this short are never compacted

_READ = re.compile(r"^(\S+) \((?:lines (\d+)-(\d+) of (\d+)|lines (\d+)-(\d+) of (\d+) shown)")
_EDIT = re.compile(r"^edited (\S+) at line (\d+): replaced (\d+) lines")
_CREATED = re.compile(r"^created (\S+) ")
_TEST_LINE = re.compile(r"^\S+: (?:\d+ passed|no such test file|the test run produced no results)")


def _read_range(output: str) -> tuple[str, int, int] | None:
    m = _READ.match(output)
    if not m:
        return None
    a, b = (m.group(2), m.group(3)) if m.group(2) else (m.group(5), m.group(6))
    return m.group(1), int(a), int(b)


def _key(entry: dict) -> str:
    import json
    return f"{entry.get('tool')}|{json.dumps(entry.get('args') or {}, sort_keys=True)}"


def _summary(tool: str, output: str, ntok) -> str | None:
    """The kind-specific summary of an old output, or None to keep it."""
    if len(output) <= SHORT_CHARS or output.startswith("error"):
        return None
    if tool in READS:
        r = _read_range(output)
        where = f"`{r[0]}` lines {r[1]}-{r[2]}" if r else "a file"
        return f"(older output of {tool}, {where}: {ntok(output)} tokens, omitted. Read it again if you need the text.)"
    if tool == "run_tests":
        keep, names = [], 0
        for ln in output.splitlines():
            if _TEST_LINE.match(ln):
                keep.append(ln)
                names = 0
            elif ln.startswith("- ") and names < FAILING_NAMES:
                keep.append(ln)
                names += 1
            elif ln == "failing:":
                keep.append(ln)
        if not keep:
            return None
        return "(older test results, summarised)\n" + "\n".join(keep)
    kind = "index" if tool.startswith("prism_") or tool in INDEX_TOOLS else tool
    n = SUMMARY_LINES.get(kind)
    if n is None:
        return None
    lines = output.splitlines()
    if len(lines) <= n:
        return None
    return "\n".join(lines[:n]) + f"\n(… older output of {tool}: {len(lines) - n} more lines, {ntok(output)} tokens in all, omitted)"


def compact(history: list[dict], ntok, keep_recent: int = KEEP_RECENT) -> tuple[list[dict], dict]:
    """A compacted copy of `history` and counts of what was done. History entries are the loop's messages; tool
    outputs carry `tool_output`, `tool`, `args` and `turn`."""
    out = [dict(m) for m in history]
    idx = [i for i, m in enumerate(out) if m.get("tool_output")]
    counts = {"superseded": 0, "stale": 0, "repeated": 0, "summarised": 0}
    later_reads: dict[str, list[tuple[int, int, int]]] = {}      # path -> [(turn, a, b)] of reads after this point
    later_edits: dict[str, list[tuple[int, int, int]]] = {}      # path -> [(turn, first, last)] of later edits
    later_keys: dict[str, int] = {}                              # grep/list_dir args -> the later turn
    recent = set(idx[-keep_recent:]) if keep_recent > 0 else set()
    for i in reversed(idx):                                      # newest first, so "later" is what was seen already
        m = out[i]
        tool, text, turn = m.get("tool"), m["content"], m.get("turn", 0)
        new = None
        if tool in READS:
            r = _read_range(text)
            if r:
                path, a, b = r
                cover = next((t for t, x, y in later_reads.get(path, []) if x <= a and y >= b), None)
                edit = next((t for t, x, y in later_edits.get(path, []) if x <= b and y >= a), None)
                if cover is not None:
                    new, kind = (f"(superseded: `{path}` lines {a}-{b} were read again at turn {cover}; "
                                 f"that newer output is below)"), "superseded"
                elif edit is not None:
                    new, kind = (f"(stale: `{path}` was edited at turn {edit} after this read of lines {a}-{b}; "
                                 f"read it again for its current text)"), "stale"
                later_reads.setdefault(path, []).append((turn, a, b))
        elif tool == "edit_file":
            e = _EDIT.match(text)
            if e:
                first = int(e.group(2))
                later_edits.setdefault(e.group(1), []).append((turn, first, first + max(1, int(e.group(3))) - 1))
        elif tool in ("grep", "list_dir", "prism_search"):
            k = _key(m)
            if k in later_keys and len(text) > SHORT_CHARS:
                new, kind = f"(repeated: the same {tool} was run again at turn {later_keys[k]}; see below)", "repeated"
            later_keys.setdefault(k, turn)
        if new is None and i not in recent:
            s = _summary(tool, text, ntok)
            if s is not None:
                new, kind = s, "summarised"
        if new is not None and ntok(new) < ntok(text):
            m["content"], m["compacted"] = new, kind
            counts[kind] += 1
    return out, counts
