"""Pre-flight for the raw tool path (`r3/mistral_raw.py`): does the harness render devstral's prompt as Ollama does?

    python -B -m harness.experiments.r4.raw_preflight --base-url URL --model M [--out preflight.json]

For a few message lists shaped like the loop's (system prompt of each arm, the task, past native calls with code that
has `<`, `>`, `&`, quotes and newlines in their arguments, tool outputs, a format reminder), the same messages and
tools go once to `/api/chat` (Ollama renders the template) and once to `/api/generate` with `raw: true` (the harness
renders it). Ollama's prompt token counts must be equal: the same template applied to the same messages gives the same
tokens. Between the two, a short unrelated request clears the server's prompt cache, so each count is the full prompt.
Also recorded: whether both replies (seed fixed, 64 tokens) give the same first call. Exit 0 if every count matches,
5 otherwise.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

from harness.experiments.r3.loop import native_messages, native_tools, system_prompt
from harness.experiments.r3.mistral_raw import parse_raw, render


def _post(url: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(url + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.loads(r.read())


def cases() -> list[tuple[str, list[dict]]]:
    out = []
    for version in ("PRISM", "BASELINE"):
        sys_p = system_prompt(version)
        task = "Remove the getter `shape` defined inside the function `object` from `src/a.ts`. Remaining tests must pass."
        call1 = {"tool": "read_file", "args": {"path": "src/a.ts", "start": 1, "end": 40}}
        out1 = "src/a.ts (lines 1-3 of 3)\n1\texport function object<T>(s: T): Obj<T> {\n2\t  return a && b;\n3\t}"
        call2 = {"tool": "edit_file", "args": {"path": "src/a.ts", "old": "  return a && b;\n",
                                                "new": "  return \"x\" < 'y' ? a : b;\n"}}
        out.append((f"{version}: task only", [{"role": "system", "content": sys_p}, {"role": "user", "content": task}]))
        out.append((f"{version}: two calls and a reminder", [
            {"role": "system", "content": sys_p}, {"role": "user", "content": task},
            {"role": "assistant", "content": json.dumps(call1)}, {"role": "user", "content": out1},
            {"role": "assistant", "content": json.dumps(call2)}, {"role": "user", "content": "edited src/a.ts at line 2"},
            {"role": "assistant", "content": "not a call"}, {"role": "user", "content": "Your reply was not one JSON "
                                                                                       "tool call."}]))
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--base-url", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--out")
    a = p.parse_args(argv)
    url = a.base_url.rstrip("/")
    opts = {"temperature": 0.2, "seed": 42, "num_ctx": 20480, "num_predict": 64}
    clear = lambda: _post(url, "/api/generate", {"model": a.model, "prompt": "Count to three.", "stream": False,
                                                  "options": {**opts, "num_predict": 4}})
    rows, ok = [], True
    for name, msgs in cases():
        tools = native_tools("PRISM" if name.startswith("PRISM") else "BASELINE")
        clear()
        chat = _post(url, "/api/chat", {"model": a.model, "messages": native_messages(msgs), "tools": tools,
                                        "stream": False, "options": opts})
        clear()
        raw = _post(url, "/api/generate", {"model": a.model, "prompt": render(msgs, tools), "raw": True,
                                           "stream": False, "options": opts})
        calls = (chat.get("message") or {}).get("tool_calls") or []
        chat_call = [calls[0]["function"]["name"], calls[0]["function"].get("arguments")] if calls else None
        raw_text, raw_meta = parse_raw(raw.get("response") or "")
        try:
            rc = json.loads(raw_text) if raw_meta.get("tool_calls") else None
        except ValueError:
            rc = None
        raw_call = [rc["tool"], rc["args"]] if rc else None
        same = chat.get("prompt_eval_count") == raw.get("prompt_eval_count")
        ok = ok and same
        rows.append({"case": name, "prompt_tokens_chat": chat.get("prompt_eval_count"),
                     "prompt_tokens_raw": raw.get("prompt_eval_count"), "counts_match": same,
                     "chat_call": chat_call, "raw_call": raw_call, "raw_head": (raw.get("response") or "")[:200],
                     "calls_match": chat_call == raw_call if chat_call or raw_call else None})
        print(json.dumps(rows[-1]), flush=True)
    rep = {"ok": ok, "cases": rows}
    if a.out:
        json.dump(rep, open(a.out, "w"), indent=1)
    print(json.dumps({"ok": ok}))
    return 0 if ok else 5


if __name__ == "__main__":
    sys.exit(main())
