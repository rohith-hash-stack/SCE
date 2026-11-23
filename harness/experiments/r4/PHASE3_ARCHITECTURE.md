# Phase 3: why coding agents miss the other files, and what PRISM changes

The core hypothesis of the project, written down before the Phase 3 proof of concept runs (2026-11-23). The design,
the arms and the go/no-go rule are in `AMENDMENTS.md` ("Phase 3, hidden dependencies, two stages" and amendments
1-3). This note says why the experiment is shaped the way it is.

## The question

Even with `grep`, `read_file` and a language server, coding agents tend to change the file the user named and stop.
They miss the places in other files that the change disturbs, unless the user tells them explicitly to fix those too.
Why?

## Five reasons

**1. Dependencies only point one way.** A file says what it uses, never what uses it. Reading `price()` in
`pricing.py` shows what `price` calls; nothing in it says `checkout.py` calls `price`. To find the callers, the agent
has to decide to look and know what to look for. If nothing prompts that thought, it never happens. These are
"unknown unknowns": you cannot search for something you do not suspect exists.

**2. The tools are pull, not push.** `grep`, `read_file`, even an LSP's Find All References answer only the question
asked. None of them says, unprompted, "by the way, 14 other places depend on this". Whether the context of other
files ever arrives depends on the agent asking the right question at the right moment.

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
into a known question the agent can pull on. The capability was there all along; the trigger was not.

## What PRISM changes, and what it does not (yet)

- **One question returns the whole map:** `prism_change_context` (callers, dependencies, labelled other uses) and
  `prism_find_references` (every real use, at its line and labelled: call, import, type annotation, subclass,
  `__all__`...; look-alikes counted, not listed; calls PRISM could not resolve by type listed apart). A pull agent
  needs to ask several right questions to assemble that picture; a PRISM agent needs to ask one.
- **"Done" checked against the whole change:** the harness's done gate (every arm) checks every place that has to
  change, not only the local tests, and says how many are still wrong (never where).
- **Not push in the full sense:** today the agent must still call PRISM's tools. `read_file` is not intercepted,
  `prism_read` adds only a one-line list of the definitions it shows, and `edit_file` warns only when an edit leaves a
  file unparseable. Pushing the dependents map on every read of a definition, or warning on an edit that breaks a
  downstream connection in the graph ("intent interception"), is not built. Phase 1-2's INJECTED arm pushed the change
  context once, before the first turn; Phase 3 does not have it.

## How Phase 3 tests it

**The arms** compare push against pull, each at its best:
- **BASELINE_LSP:** an IDE agent's tools: `grep`, `read_file`, a language server's `find_references` (jedi),
  `semantic_search`, `ast_search`, a read-only shell. It pulls.
- **PRISM_LSP:** the standard tools and PRISM's tools, `prism_find_references` among them. One call returns the map.
- **PRISM_ONLY:** PRISM's tools without `grep` and `read_file`.

**The tasks:** renaming a method, function or class with 15-30 places in 3+ files. Every answer key is built from
PRISM's resolved uses and agreed by jedi, place by place.

**The proof of concept:** 4 tasks plus 1 control, 3 seeds, 45 runs. Its pre-registered rule asks whether a PRISM arm
solves at least 3 more of its 12 hidden-task runs than BASELINE_LSP, while losing on at most 1 task.

**What would count against the hypothesis:** with a language server, a pull agent that asks the right question
already gets exact places, cheaply (166 tokens for `find_references`, 287 for `prism_find_references`, on the same
task). If BASELINE_LSP asks that question as reliably as the PRISM arms ask theirs, PRISM's richer map buys nothing on
these tasks. The proof of concept is built to tell the two apart.
