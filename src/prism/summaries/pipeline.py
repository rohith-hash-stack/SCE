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

Incremental: an entry whose card key is unchanged is kept; only new or
changed functions are summarized.
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


def repair(llm, summary: str, card: str) -> str:
    """Up to REPAIR_ATTEMPTS rewrites of a summary that fails the lint;
    the candidate with the fewest flags (the original on a tie)."""
    best, best_n = summary, sum(lint(summary, card).values())
    for _ in range(REPAIR_ATTEMPTS):
        if not best_n:
            break
        got = llm.json(REPAIR_PROMPT.format(problems="; ".join(problems(best, card)), summary=best, card=card),
                       max_out=200) or {}
        cand = _clean(got.get("summary") or "")
        n = sum(lint(cand, card).values()) if cand else best_n
        if cand and n < best_n:
            best, best_n = cand, n
    return best


def build_index(
    builder, repo_root: str, *, model: str = LOCKED_MODEL, llm=None, base_urls: "list[str] | None" = None,
    batch: int = BATCH, threads: int = 4, from_cache: "dict | None" = None, dry_run: bool = False,
    log: Callable[[str], None] = lambda _m: None,
) -> dict:
    """Bring `.prism/summaries.json` up to date for `builder`'s functions.
    Returns counts; `dry_run` only counts what would be summarized."""
    from prism.graph.entry_points import find_entry_points
    from prism.query.nlq import NLQIndex
    nlq = NLQIndex(builder, repo_root)
    entries = find_entry_points(builder)
    fns = function_symbols(nlq.pool)
    cards = {s.qualified_name: c["card"] for s in fns if (c := build_card(builder, {}, entries, s.qualified_name, axes=False))}
    keys = {q: card_key(model, c) for q, c in cards.items()}
    pipeline = {**locked_pipeline(), "model": model}
    path = index_path(repo_root)
    old = read_index(path) or {}
    previous = (old.get("functions") or {}) if old.get("pipeline") == pipeline else {}
    done: dict[str, dict] = {q: {"key": keys[q], "summary": previous[q]["summary"]}
                             for q in cards if previous.get(q, {}).get("key") == keys[q]}
    imported = 0
    for q, e in (from_cache or {}).items():  # harness cache: {qname: {"key", "summary", "raw"?}}
        if q in cards and q not in done and isinstance(e, dict) and e.get("key") == keys[q] and e.get("summary"):
            done[q] = {"key": keys[q], "summary": _clean(e["summary"])}
            imported += 1
    todo = [q for q in cards if q not in done]
    counts = {"functions": len(fns), "cards": len(cards), "kept": len(done) - imported, "imported": imported,
              "to_summarize": len(todo), "summarized": 0, "repaired": 0, "failed": 0}
    log(f"{len(fns)} functions, {len(cards)} cards: kept {counts['kept']}, imported {imported}, to summarize {len(todo)}")
    if dry_run:
        return counts
    if todo:
        llm = llm or Ollama(model, base_urls or [])
        batches = [todo[i:i + batch] for i in range(0, len(todo), batch)]
        with cf.ThreadPoolExecutor(threads) as ex:
            for i, got in enumerate(ex.map(lambda b: summarize_batch(llm, cards, b), batches), 1):
                for q, s in got.items():
                    done[q] = {"key": keys[q], "summary": s}
                    counts["summarized"] += 1
                log(f"summarized {i}/{len(batches)} batches")
        flagged = [q for q in todo if q in done and any(lint(done[q]["summary"], cards[q]).values())]
        with cf.ThreadPoolExecutor(threads) as ex:
            for q, new in zip(flagged, ex.map(lambda q: repair(llm, done[q]["summary"], cards[q]), flagged)):
                if new != done[q]["summary"]:
                    done[q]["summary"] = new
                    counts["repaired"] += 1
        counts["failed"] = len([q for q in todo if q not in done])
    by_name = {s.qualified_name: s for s in fns}
    write_index(path, pipeline, {q: {**e, "source_sha": source_sha(nlq._source(by_name[q].file), by_name[q].line_range)}
                                 for q, e in done.items()})
    counts["coverage"] = round(len(done) / len(fns), 4) if fns else 0.0
    counts["locked_pipeline"] = pipeline == locked_pipeline()
    counts["path"] = str(path)
    return counts
