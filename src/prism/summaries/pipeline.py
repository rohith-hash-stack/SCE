"""The locked offline summarizer behind `prism summarize`.

For every indexed function and method: a context card (`cards.build_card`,
v2: no axes line), summarized in batches of `BATCH` by a local Ollama model
with `SUMMARY_PROMPT`; a summary that fails `lint.lint` is sent back with
its problems named (`REPAIR_PROMPT`, at most `REPAIR_ATTEMPTS` rewrites,
the candidate with the fewest flags kept). The result is written to
`.prism/summaries.json` (`prism.summaries.index`), each entry tied to its
card key and its function's source hash.

Every value here is the candidate locked in
`harness/experiments/purpose_search/CANDIDATE.md` and validated on set 4;
the harness imports the prompts from this module, so the shipped and the
evaluated pipeline cannot drift. Card keys equal the harness's v2 cache
keys, so a cache produced there (e.g. on a GPU notebook) can be installed
with `--from-cache` and is checked card by card.

Incremental and resumable: an entry whose card key is unchanged is kept;
only new or changed functions are summarized. While it runs, the index is
checkpointed (atomically, `index.write_index`) every `CHECKPOINT_BATCHES`
batches and during the repair pass, so a crash or a notebook timeout loses
at most that much work - rerunning the same command resumes from the last
checkpoint. Each entry records whether the repair pass has `checked` it, so
a summary written before an interruption is still linted and repaired on
the resumed run.
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import itertools
import json
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Callable

from prism.summaries.cards import build_card
from prism.summaries.index import function_symbols, index_path, locked_pipeline, read_index, source_sha, write_index
from prism.summaries.lint import lint, problems

LOCKED_MODEL = "qwen2.5-coder:14b-instruct-q8_0"
VARIANT = "v2"
BATCH = 12
#: Write the index every this many summary batches (and repairs) during a run.
CHECKPOINT_BATCHES = 25
SUMMARY_PROMPT = (
    "You are an expert software architect describing functions for a code search index.\n"
    "Below are the structural profiles of {n} functions, separated by '---'.\n\n{cards}\n\n"
    "For EACH function write one plain-English sentence of 8 to 14 words saying what it does and how, in the words "
    "a developer would use when asking about this behaviour.\n"
    "Rules:\n"
    "1. No code identifiers: no function, method, class, variable, module or file names, and no backticks, dots, "
    "underscores, camelCase or parentheses.\n"
    "2. Do not list the functions it calls; say in ordinary words what they achieve together.\n"
    "3. Do not simply restate the function's name; describe its behaviour.\n"
    "4. No internal analysis labels or filler, such as 'pure compute', 'no effect', 'leaf utility' or 'no return'.\n"
    "5. Only describe behaviour, protocols and data present in the profile; do not assume business domains.\n"
    "Example - bad: \"Loads config via os.environ.get, json.loads, no effect.\" "
    "good: \"Reads settings from environment variables and parses them as JSON.\"\n"
    "Respond with a JSON object mapping each Qualified Name exactly as given to its sentence.")
REPAIR_PROMPT = (
    "The sentence below describes the function profiled after it, but breaks the rules: {problems}.\n"
    "Rewrite it as one plain-English sentence of 8 to 14 words that keeps its meaning, with no code identifiers "
    "(no function, method, class, variable, module or file names; no dots, underscores, camelCase or parentheses), "
    "no list of the functions it calls, and no internal labels or filler.\n"
    "Sentence: {summary}\n\nProfile:\n{card}\n\n"
    "Respond with a JSON object {{\"summary\": \"<the rewritten sentence>\"}}.")
REPAIR_ATTEMPTS = 2
#: Candidate v4, pass 2: a summary the v2 pass left restating the function's name
#: is written again from the card plus the function's own source (at most
#: BODY_CAP lines), effects first.
BODY_CAP = 60
ESCALATION_PROMPT = (
    "You are an expert software architect describing a function for a code search index.\n"
    "Its profile alone gave too little to go on, so its source code follows.\n\n"
    "Profile:\n{card}\n\nSource:\n{body}\n\n"
    "First list, in a few words each, what the function checks, computes, changes, sends or returns (the effects).\n"
    "Then, from those effects, write one plain-English sentence of 8 to 14 words saying what it does and how, in the words "
    "a developer would use when asking about this behaviour.\n"
    "Rules for the sentence: no code identifiers (no function, method, class, variable, module or file names; no "
    "backticks, dots, underscores, camelCase or parentheses); do not list the functions it calls; do not simply restate "
    "the function's name; no internal analysis labels or filler.\n"
    "Respond with a JSON object {{\"effects\": \"<what it checks, computes, changes, sends or returns>\", "
    "\"summary\": \"<the sentence>\"}}.")


#: Summarizer candidates. "v2" is locked and shipped. The others are unvalidated:
#: v3 / v3-name add lint checks whose flagged summaries go to the same repair
#: pass (dropped at stage 1, CANDIDATE_V3.md); v4 keeps the v2 lint and repair
#: and escalates summaries that restate the function's name to a second pass
#: that also sees the function's source (CANDIDATE_V4.md).
CANDIDATES = {"v2": {"lint_flags": ()}, "v3": {"lint_flags": ("restates_name", "too_short")},
              "v3-name": {"lint_flags": ("restates_name",)},
              "v4": {"lint_flags": (), "escalate": ("restates_name",)},
              "v5": {"lint_flags": (), "escalate": ("restates_name",), "long_short": (20, 10), "index_effects": True,
                     "extends": "v4"}}
#: v5 (CANDIDATE_V5.md) = v4 + two changes: a function of at least
#: `long_short[0]` lines whose summary has fewer than `long_short[1]` words is
#: also escalated, and an escalated function's `effects` is indexed with its
#: summary (`prism.summaries.index.search_text`). "extends": a v4 index's
#: escalations (same prompt) are reused when converting it to v5.
#: Pipeline-record keys that belong to a candidate, not to the v2 summarizer.
_CANDIDATE_KEYS = ("lint", "escalation_sha", "body_cap", "long_short", "index_effects")


def _generation(pipeline: dict) -> dict:
    """The part of a pipeline record that decides the pass-1 summaries
    (model, prompt, card, repair prompt) - everything but the candidate's own
    keys (lint version, escalation prompt)."""
    return {k: v for k, v in pipeline.items() if k not in _CANDIDATE_KEYS}


def candidate_pipeline(candidate: str, model: str = None) -> dict:
    """The pipeline record an index built as `candidate` carries."""
    p = {**locked_pipeline(), **({"model": model} if model else {})}
    if candidate != "v2":
        p["lint"] = candidate
    c = CANDIDATES[candidate]
    if c.get("escalate"):
        p |= {"escalation_sha": hashlib.sha1(ESCALATION_PROMPT.encode()).hexdigest()[:16], "body_cap": BODY_CAP}
    if c.get("long_short"):
        p["long_short"] = list(c["long_short"])
    if c.get("index_effects"):
        p["index_effects"] = True
    return p


def long_short(summary: str, line_range: tuple[int, int], rule) -> bool:
    """v5's size trigger: a body of at least rule[0] lines, a summary under rule[1] words."""
    return bool(rule) and line_range[1] - line_range[0] + 1 >= rule[0] and len(summary.split()) < rule[1]


def function_body(lines: list[str], line_range: tuple[int, int], cap: int = BODY_CAP) -> str:
    """The function's source lines, at most `cap` of them."""
    body = lines[line_range[0] - 1:line_range[1]]
    more = len(body) - cap
    return "\n".join(body[:cap]) + (f"\n... ({more} more lines)" if more > 0 else "")


def card_key(model: str, card: str) -> str:
    """Identical to the harness's v2 summary-cache key."""
    return hashlib.sha1((model + "\n" + VARIANT + "\n" + SUMMARY_PROMPT + "\n" + card).encode()).hexdigest()[:16]


def _clean(text) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


class Ollama:
    """JSON replies from a local Ollama server (`/api/chat`), the locked
    sampling options; requests are spread round-robin over `base_urls`."""

    def __init__(self, model: str, base_urls: list[str]):
        self.model = model
        self._urls = itertools.cycle(base_urls or ["http://127.0.0.1:11434"])
        self._lock = threading.Lock()
        self.usage = {"calls": 0, "failed": 0}

    def json(self, prompt: str, max_out: int, seed: int = 42) -> dict | None:
        for attempt in range(6):
            with self._lock:
                url = next(self._urls)
            body = {"model": self.model, "messages": [{"role": "user", "content": prompt}], "format": "json",
                    "stream": False, "options": {"temperature": 0.4, "seed": seed, "num_ctx": 8192, "num_predict": max_out}}
            req = urllib.request.Request(f"{url}/api/chat", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=600) as r:
                    text = json.loads(r.read())["message"]["content"]
                with self._lock:
                    self.usage["calls"] += 1
                return json.loads(text)
            except json.JSONDecodeError:
                break
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, KeyError) as e:
                if getattr(e, "code", 503) in (429, 500, 502, 503, 504) and attempt < 5:
                    time.sleep(2 ** (attempt + 2))
                    continue
                break
        with self._lock:
            self.usage["failed"] += 1
        return None


def summarize_batch(llm, cards: dict[str, str], batch: list[str]) -> dict[str, str]:
    got = llm.json(SUMMARY_PROMPT.format(n=len(batch), cards="\n---\n".join(cards[q] for q in batch)),
                   max_out=60 * len(batch) + 200) or {}
    out = {q: _clean(got[q]) for q in batch if isinstance(got, dict) and got.get(q)}
    for q in batch:  # a card the batch reply left out is retried on its own
        if q not in out:
            one = llm.json(SUMMARY_PROMPT.format(n=1, cards=cards[q]), max_out=200) or {}
            v = one.get(q) or (next(iter(one.values())) if isinstance(one, dict) and len(one) == 1 else None)
            if v:
                out[q] = _clean(v)
    return out


def repair(llm, summary: str, card: str, extra: "tuple[str, ...]" = ()) -> str:
    """Up to REPAIR_ATTEMPTS rewrites of a summary that fails the lint
    (`extra`: a candidate's additional checks); the candidate with the fewest
    flags (the original on a tie)."""
    best, best_n = summary, sum(lint(summary, card, extra).values())
    for _ in range(REPAIR_ATTEMPTS):
        if not best_n:
            break
        got = llm.json(REPAIR_PROMPT.format(problems="; ".join(problems(best, card, extra)), summary=best, card=card),
                       max_out=200) or {}
        cand = _clean(got.get("summary") or "")
        n = sum(lint(cand, card, extra).values()) if cand else best_n
        if cand and n < best_n:
            best, best_n = cand, n
    return best


def escalate(llm, summary: str, card: str, body: str, flags: "tuple[str, ...]",
             line_range: "tuple[int, int] | None" = None, size_rule=None) -> tuple[str | None, str]:
    """Pass 2 (candidates v4, v5): `(new summary or None, effects)`. The new
    summary is kept only if it has no v2 lint flag and fewer flags (v2 plus
    `flags`, plus v5's size trigger) than the pass-1 summary - escalation
    never makes a summary worse by the lint's measure."""
    got = llm.json(ESCALATION_PROMPT.format(card=card, body=body), max_out=320) or {}
    cand, effects = _clean(got.get("summary") or ""), _clean(got.get("effects") or "")
    score = lambda s: sum(lint(s, card, flags).values()) + (long_short(s, line_range, size_rule) if size_rule else 0)
    if cand and not any(lint(cand, card).values()) and score(cand) < score(summary):
        return cand, effects
    return None, effects


def build_index(
    builder, repo_root: str, *, model: str = LOCKED_MODEL, llm=None, base_urls: "list[str] | None" = None,
    batch: int = BATCH, threads: int = 4, from_cache: "dict | None" = None, dry_run: bool = False,
    checkpoint_every: int = CHECKPOINT_BATCHES, candidate: str = "v2", log: Callable[[str], None] = lambda _m: None,
) -> dict:
    """Bring `.prism/summaries.json` up to date for `builder`'s functions.
    Returns counts; `dry_run` only counts what would be summarized (and, for
    a candidate with a stricter lint, how many kept summaries it flags).
    `checkpoint_every`: batches (and, in the repair pass, that many times
    `batch` repairs) between atomic checkpoint writes. `candidate`: `"v2"`
    (locked) or an unvalidated `CANDIDATES` entry, whose index search uses at
    weight 0 until it is validated and locked."""
    if candidate not in CANDIDATES:
        raise ValueError(f"unknown candidate {candidate!r}; one of {sorted(CANDIDATES)}")
    extra = CANDIDATES[candidate]["lint_flags"]
    from prism.graph.entry_points import find_entry_points
    from prism.query.nlq import NLQIndex
    nlq = NLQIndex(builder, repo_root)
    entries = find_entry_points(builder)
    fns = function_symbols(nlq.pool)
    cards = {s.qualified_name: c["card"] for s in fns if (c := build_card(builder, {}, entries, s.qualified_name, axes=False))}
    keys = {q: card_key(model, c) for q, c in cards.items()}
    escalate_flags = CANDIDATES[candidate].get("escalate", ())
    size_rule = CANDIDATES[candidate].get("long_short")
    pipeline = candidate_pipeline(candidate, model)
    path = index_path(repo_root)
    old = read_index(path) or {}
    old_pipeline = old.get("pipeline") or {}
    # Same summarizer (model, prompt, card): summaries carry over. From the
    # locked v2 lint to a candidate's (v2 -> v3) they are re-linted and repaired
    # (`checked` reset); any other lint change (v3 -> v2) starts over, so a
    # locked v2 index never holds summaries a candidate's repair rewrote.
    old_lint = old_pipeline.get("lint", "v2")
    same_generation = _generation(old_pipeline) == _generation(pipeline)
    same_lint = old_lint == candidate and old_pipeline == pipeline
    # A candidate that extends another (v4 -> v5) keeps its escalations when the
    # escalation itself (prompt, body cap) is unchanged; its own triggers re-run.
    parent = CANDIDATES[candidate].get("extends")
    from_parent = (old_lint == parent and parent is not None and candidate_pipeline(parent, model) == old_pipeline)
    previous = (old.get("functions") or {}) if same_generation and (same_lint or from_parent or old_lint == "v2") else {}
    by_name = {s.qualified_name: s for s in fns}
    current_sha = lambda q: source_sha(nlq._source(by_name[q].file), by_name[q].line_range)
    done: dict[str, dict] = {}
    for q in cards:
        e = previous.get(q, {})
        if e.get("key") != keys[q] or not e.get("summary"):
            continue
        entry = {"key": keys[q], "summary": e["summary"], "checked": bool(e.get("checked")) and same_lint}
        if escalate_flags and (((same_lint or from_parent) and e.get("v2_checked")) or (old_lint == "v2" and e.get("checked"))):
            entry["v2_checked"] = True  # v2's lint and repair already saw it: only the escalation is left
        if (same_lint or from_parent) and e.get("escalation_tried"):
            if e.get("source_sha") == current_sha(q):  # escalated from this very body
                entry |= {k: e[k] for k in ("escalation_tried", "escalated", "pass1", "effects") if k in e}
            else:  # the body changed: back to the pass-1 summary, escalate again
                entry = {"key": keys[q], "summary": e.get("pass1", e["summary"]), "checked": False, "v2_checked": True}
        done[q] = entry
    imported = 0
    for q, e in (from_cache or {}).items():  # harness cache: {qname: {"key", "summary", "raw"?}}, repaired under v2
        if q in cards and q not in done and isinstance(e, dict) and e.get("key") == keys[q] and e.get("summary"):
            done[q] = {"key": keys[q], "summary": _clean(e["summary"]), "checked": not (extra or escalate_flags),
                       **({"v2_checked": True} if escalate_flags and not extra else {})}
            imported += 1
    todo = [q for q in cards if q not in done]

    def wants_escalation(q: str) -> bool:
        return any(lint(done[q]["summary"], cards[q], escalate_flags).values()) or \
            long_short(done[q]["summary"], by_name[q].line_range, size_rule)
    counts = {"functions": len(fns), "cards": len(cards), "kept": len(done) - imported, "imported": imported,
              "to_summarize": len(todo), "summarized": 0, "repaired": 0, "failed": 0, "checkpoints": 0}
    unchecked = [q for q in cards if q in done and not done[q]["checked"]]
    counts["to_check"] = len(unchecked)
    log(f"{len(fns)} functions, {len(cards)} cards: kept {counts['kept']}, imported {imported}, to summarize {len(todo)}, "
        f"to re-lint {len(unchecked)} (candidate {candidate})")
    if dry_run:
        counts["to_repair"] = sum(1 for q in unchecked if any(lint(done[q]["summary"], cards[q], extra).values()))
        if escalate_flags:
            counts["to_escalate"] = sum(1 for q in unchecked if not done[q].get("escalation_tried") and wants_escalation(q))
        return counts

    def save() -> None:
        write_index(path, pipeline, {q: {**e, "source_sha": source_sha(nlq._source(by_name[q].file), by_name[q].line_range)}
                                     for q, e in done.items()})

    def checkpoint(what: str) -> None:
        save()
        counts["checkpoints"] += 1
        log(f"checkpoint: {len(done)}/{len(cards)} summaries ({what})")

    every = max(1, checkpoint_every)
    to_check = [q for q in cards if q in done and not done[q]["checked"]]  # left unrepaired by an interrupted run
    if todo or to_check:
        llm = llm or Ollama(model, base_urls or [])
    if todo:
        batches = [todo[i:i + batch] for i in range(0, len(todo), batch)]
        with cf.ThreadPoolExecutor(threads) as ex:
            for i, got in enumerate(ex.map(lambda b: summarize_batch(llm, cards, b), batches), 1):
                for q, s in got.items():
                    done[q] = {"key": keys[q], "summary": s, "checked": False}
                    counts["summarized"] += 1
                log(f"summarized {i}/{len(batches)} batches")
                if i % every == 0 and i < len(batches):
                    checkpoint(f"batch {i}/{len(batches)}")
        counts["failed"] = len([q for q in todo if q not in done])
        to_check = [q for q in cards if q in done and not done[q]["checked"]]
    if to_check:
        # Lint + repair. With an escalation pass still to come, an entry is only
        # `v2_checked` here; `checked` means every pass has seen it.
        mark = "v2_checked" if escalate_flags else "checked"
        pending = [q for q in to_check if not done[q].get(mark)]
        clean = [q for q in pending if not any(lint(done[q]["summary"], cards[q], extra).values())]
        for q in clean:
            done[q][mark] = True
        flagged = [q for q in pending if not done[q].get(mark)]
        if flagged:
            checkpoint("summaries done, repairing")
        step = every * batch
        with cf.ThreadPoolExecutor(threads) as ex:
            for j, (q, new) in enumerate(zip(flagged, ex.map(lambda q: repair(llm, done[q]["summary"], cards[q], extra), flagged)), 1):
                if new != done[q]["summary"]:
                    done[q]["summary"] = new
                    counts["repaired"] += 1
                done[q][mark] = True
                if j % step == 0 and j < len(flagged):
                    checkpoint(f"repair {j}/{len(flagged)}")
        if escalate_flags:
            esc = [q for q in to_check if not done[q].get("escalation_tried") and wants_escalation(q)]
            counts["escalation_tried"] = len(esc)
            counts.setdefault("escalated", 0)
            if esc:
                checkpoint(f"escalating {len(esc)}")

            def run(q):
                body = function_body(nlq._source(by_name[q].file), by_name[q].line_range)
                return escalate(llm, done[q]["summary"], cards[q], body, escalate_flags, by_name[q].line_range, size_rule)
            with cf.ThreadPoolExecutor(threads) as ex:
                for j, (q, (new, effects)) in enumerate(zip(esc, ex.map(run, esc)), 1):
                    done[q]["escalation_tried"] = True
                    if effects:  # kept even when the summary is not: what the model read in the body
                        done[q]["effects"] = effects
                    if new:
                        done[q] |= {"pass1": done[q]["summary"], "summary": new, "escalated": True}
                        counts["escalated"] += 1
                    if j % step == 0 and j < len(esc):
                        checkpoint(f"escalation {j}/{len(esc)}")
            for q in to_check:
                done[q]["checked"] = True
    save()
    counts["coverage"] = round(len(done) / len(fns), 4) if fns else 0.0
    counts["locked_pipeline"] = pipeline == locked_pipeline()
    counts["path"] = str(path)
    return counts
