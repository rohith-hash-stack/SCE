# Phase 3: why coding agents miss the other files, and the question PRISM's tools answer

Written down before the Phase 3 proof of concept runs (2026-11-23). The design, the arms and the go/no-go rule are in
`AMENDMENTS.md` ("Phase 3, hidden dependencies, two stages" and amendments 1-3). This note says why the experiment is
shaped the way it is.

**The official framing (the user's decision, 2026-11-23):** PRISM is a suite of tools the agent must choose to use.
Nothing is pushed into the agent's context, no read or edit is intercepted, and none will be. The question is whether
the agent, given the choice, picks PRISM's map over a language server and grep, and whether it does better for it.

**POC 2 (the user's decision, 2026-11-23, after POC 1's NO-GO):** the pull framing stays for PRISM_LSP. A new arm,
PRISM_GW, adds PRISM's gateway (`src/prism/gateway`): on every turn it checks the agent's call against PRISM's graph,
can refuse an edit or `done` (each refusal overridable), and adds what the graph knows to the answer. Every intervention
and its outcome is logged, so that the gateway's effect (PRISM_GW − PRISM_LSP) can be told apart from the tools'
(PRISM_LSP − BASELINE_LSP). Every arm also gets harness guards against POC 1's failures. See AMENDMENTS "Phase 3, POC 2".

## The problem

Even with `grep`, `read_file` and a language server, coding agents tend to change the file the user named and stop.
They miss the places in other files that the change disturbs, unless the user tells them explicitly to fix those too.
Five reasons:

**1. Dependencies only point one way.** A file says what it uses, never what uses it. Reading `price()` in
`pricing.py` shows what `price` calls; nothing in it says `checkout.py` calls `price`. To find the callers, the agent
has to decide to look and know what to look for. If nothing prompts that thought, it never happens. These are
"unknown unknowns": you cannot search for something you do not suspect exists.

**2. Every tool answers only the question asked.** `grep`, `read_file`, a language server's Find All References,
and PRISM's own tools say nothing unprompted. Whether the context of other files ever arrives depends on the agent
asking the right question at the right moment.

**3. Search is noisy, so agents sample instead of being exhaustive.** When the agent does search, it often gets a
wall of hits. In Phase 3's mining, `to_header` matched 84 lines in 19 files, and `from_header` had 99 look-alikes,
other classes' methods of the same name. A model with limited turns and context skims the first few hits and moves
on. Phase 2's `p2-signature-02` showed it: BASELINE failed 10 of 10 because `examples/persistent_state/server.py`
was buried in grep noise. It was in the results; the agent never read it.

**4. "Done" is judged locally.** The usual signal is local: the edit applies and the tests near it pass. If no test
covers the broken caller in another file, nothing tells the agent it is wrong, so it declares success. Models are
also trained, and usually instructed, to make minimal changes and not to touch "unrelated" code. That pushes them to
stay in the file the user named.

**5. Context and turns are budgets.** Every file read costs tokens and a turn. An agent that read every possibly
affected file would run out of budget on a large repository. So it rations, and the file the user named wins by
default.

**Why asking explicitly works:** the user supplies the missing piece. "Also fix the callers" turns an unknown unknown
into a known question the agent can ask a tool. The capability was there all along; the trigger was not.

## What PRISM's tools offer, when the agent calls them

- **One question returns the whole map:**
  - `prism_change_context`: callers, dependencies, labelled other uses;
  - `prism_find_references`: every real use, at its line and labelled (call, import, type annotation, subclass,
    `__all__`...). Look-alikes are counted, not listed, and calls PRISM could not resolve by type are listed apart.

  An agent with grep and a language server needs several right questions to assemble that picture; with PRISM, one.
- **Search that says what each hit is:** `prism_search` labels every hit (import, call, definition, test, docs...)
  and names the function it is in, so the noise can be told from the signal.
- **What the harness does for every arm alike:** the done gate checks every place that has to change, not only the
  local tests, and says how many are still wrong (never where).

## How Phase 3 tests it

**The arms:** each agent chooses its own tools.
- **BASELINE_LSP:** an IDE agent's tools: `grep`, `read_file`, a language server's `find_references` (jedi),
  `semantic_search`, `ast_search`, a read-only shell.
- **PRISM_LSP:** the standard tools and PRISM's tools, `prism_find_references` among them, but no language server.
- **PRISM_ONLY:** PRISM's tools without `grep` and `read_file`.

**The tasks:** renaming a method, function or class with 15-30 places in 3+ files. Every answer key is built from
PRISM's resolved uses and agreed by jedi, place by place.

**The proof of concept:** 4 tasks plus 1 control, 3 seeds, 45 runs. Its pre-registered rule asks whether a PRISM arm
solves at least 3 more of its 12 hidden-task runs than BASELINE_LSP, while losing on at most 1 task. The report also
records which tools each arm chose, how often, and what they cost (`tool_calls_per_run`,
`tool_output_tokens_per_run`).

**What would count against PRISM:** with a language server, an agent that asks the right question already gets exact
places, cheaply (166 tokens for `find_references`, 287 for `prism_find_references`, on the same task). If
BASELINE_LSP asks its question as reliably as the PRISM arms ask theirs, PRISM's richer map buys nothing on these
tasks. The proof of concept is built to tell the two apart.
