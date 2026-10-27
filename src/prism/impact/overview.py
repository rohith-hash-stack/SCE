"""The map and the second ring around a change: the wide view that the change context's close-up lacks.

    map          one or two sentences for the repository, one for each main folder, one for each file near
                 the change (the anchor's, its callers', the second ring's)
    second ring  one line each for the functions one step beyond the close-up: the callers of the anchor's
                 callers, and functions doing similar work elsewhere (they share callees or name words with the
                 anchor) - the summary cards of `prism.summaries`

`plan_overview` decides deterministically WHAT is shown and gathers the model inputs (file outlines, folder
contents, README, v2 cards). `write_overview` asks a model for the sentences (bottom-up: files, then folders,
then the repository) and `render_overview` turns them into two sections of the change context. A plan is
plain JSON, so it can be computed where the code is and written where the model is.
"""
from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field

from prism.summaries.pipeline import _clean, summarize_batch

MAX_FOLDERS = 8
MAX_FILES = 10
MAX_RING_UP = 12
MAX_RING_SIMILAR = 8
OUTLINE_CAP = 40      # outline lines per file
FILES_PER_FOLDER = 6  # a folder is described from its largest files
_NOT_MAIN = re.compile(r"(^|/)(examples?|docs?|tests?|benchmarks?|scripts?|tools?)(/|$)")
README_CAP = 1200     # characters of the README's opening

FILE_PROMPT = (
    "Below is the outline of the source file `{path}` (its classes and functions, with the first line of their "
    "docstrings).\n\n{outline}\n\n"
    "Write ONE plain-English sentence of 10 to 20 words saying what this file is responsible for in its project. "
    "Respond with a JSON object {{\"summary\": \"<the sentence>\"}}.")
FOLDER_PROMPT = (
    "The folder `{path}` of a software project contains these files, each described in one sentence:\n\n{files}\n\n"
    "Write ONE plain-English sentence of 10 to 20 words saying what this folder is responsible for. "
    "Respond with a JSON object {{\"summary\": \"<the sentence>\"}}.")
REPO_PROMPT = (
    "A software project `{name}`. The opening of its README:\n\n{readme}\n\nIts main folders:\n\n{folders}\n\n"
    "Write TWO plain-English sentences: what the project is, and how its main parts fit together. "
    "Respond with a JSON object {{\"summary\": \"<the two sentences>\"}}.")


@dataclass
class RingItem:
    qname: str
    relation: str          # "caller of a caller" | "similar"
    via: str               # the direct caller it calls, or the reason it is similar
    file: str
    line: int
    card: str              # the v2 summary card (model input)


@dataclass
class OverviewPlan:
    repo: str
    readme: str
    folders: list[tuple[str, int]] = field(default_factory=list)            # (folder, functions)
    folder_files: dict[str, list[str]] = field(default_factory=dict)         # folder -> files inside it (in scope)
    files: list[str] = field(default_factory=list)                           # files near the change
    outlines: dict[str, str] = field(default_factory=dict)                   # file -> outline
    ring: list[RingItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "OverviewPlan":
        return OverviewPlan(d["repo"], d["readme"], [tuple(x) for x in d["folders"]], d["folder_files"], d["files"],
                            d["outlines"], [RingItem(**r) for r in d["ring"]])


def _rel(path: str, root: str) -> str:
    try:
        return os.path.relpath(path, root)
    except ValueError:
        return path


def readme_excerpt(root: str) -> str:
    for name in ("README.md", "README.rst", "README.txt", "README", "readme.md"):
        path = os.path.join(root, name)
        if os.path.isfile(path):
            text = open(path, encoding="utf8", errors="replace").read()
            text = re.sub(r"<[^>]+>|!\[[^\]]*\]\([^)]*\)|\[!\[.*?\]\(.*?\)\]\(.*?\)", "", text)  # badges, html
            return re.sub(r"\n{3,}", "\n\n", text).strip()[:README_CAP]
    return ""


def file_outline(builder, path: str, root: str) -> str:
    """The file's classes and functions in order, with the first line of each docstring."""
    from prism.summaries.cards import docstring
    rows = []
    for q in builder._def_nodes:
        sym = builder.symbol_table.get(q)
        if sym is None or sym.file != path or sym.kind not in ("class", "interface", "function", "method"):
            continue
        node = builder.def_node(q)
        parsed = builder.parsed_file(sym.file)
        doc = docstring(node, parsed.source, parsed.language_id) if node is not None and parsed is not None else ""
        depth = q.count(".") - sym.module.count(".") - 1
        rows.append((sym.line_range[0], "  " * max(depth, 0) + f"{sym.kind} {q.rsplit('.', 1)[-1]}"
                     + (f" - {doc[:100]}" if doc else "")))
    rows.sort()
    lines = [r for _l, r in rows]
    return "\n".join(lines[:OUTLINE_CAP] + ([f"... (+{len(lines) - OUTLINE_CAP} more)"] if len(lines) > OUTLINE_CAP else []))


def plan_overview(cb, anchor: str, ctx) -> OverviewPlan:
    """What the map and the second ring show for `anchor`, given its change context `ctx`
    (`prism.impact.context.ChangeContextBuilder` `cb`)."""
    from prism.impact.style import siblings
    from prism.packer.candidate_index import upstream_callers_by_hop
    from prism.summaries.cards import build_card
    b, root = cb.b, cb.root
    in_package = {ctx.anchor} | {r.caller for r in ctx.callers} | {s.qname for s in ctx.siblings} | \
        {d.on for d in ctx.callees}
    # second ring: callers of the direct callers, then similar functions
    walked = upstream_callers_by_hop(b, anchor, include_tests=False)
    ring: list[RingItem] = []
    direct = {r.caller for r in ctx.callers}
    for q in sorted((q for q, hop in walked.items() if hop == 2), key=lambda q: q):
        if q in in_package or len([x for x in ring if x.relation == "caller of a caller"]) >= MAX_RING_UP:
            continue
        via = sorted(v for _u, v in b.graph.out_edges(q) if v in direct)
        sym = b.symbol_table.get(q)
        card = build_card(b, {}, cb.entries, q, axes=False)
        if sym is None or card is None:
            continue
        ring.append(RingItem(q, "caller of a caller", via[0] if via else "", _rel(sym.file, root), sym.line_range[0],
                             card["card"]))
    for s in siblings(b, anchor, cb.groups.of, k=MAX_RING_SIMILAR * 4, exclude=in_package):
        if len([x for x in ring if x.relation == "similar"]) >= MAX_RING_SIMILAR or s.qname in {x.qname for x in ring}:
            continue
        if "shares" not in s.reason and "name words" not in s.reason:
            continue  # nearness alone (same class or file) is not similarity
        card = build_card(b, {}, cb.entries, s.qname, axes=False)
        if card is not None:
            ring.append(RingItem(s.qname, "similar", s.reason, _rel(s.file, root), s.lines[0], card["card"]))
    # map: main folders, and the files near the change
    near = [ctx.file] + [r.file for r in ctx.callers] + [x.file for x in ring]
    files = list(dict.fromkeys(f for f in near if f))[:MAX_FILES]
    cbase = ctx.codebase
    wanted = {os.path.dirname(f) or "." for f in files}
    folders = [(g, n) for g, n, _e in cbase["largest"] if not _NOT_MAIN.search(g) or g in wanted][:MAX_FOLDERS]
    for g in sorted(wanted - {g for g, _n in folders}):
        folders.append((g, len(cb.groups.members.get(g, []))))
    folder_files: dict[str, list[str]] = {}
    for g, _n in folders:
        sizes = {}
        for q in cb.groups.members.get(g, []):
            f = _rel(b.symbol_table.get(q).file, root)
            sizes[f] = sizes.get(f, 0) + 1
        largest = sorted(sizes, key=lambda f: (-sizes[f], f))[:FILES_PER_FOLDER]
        folder_files[g] = sorted(set(largest) | {f for f in files if (os.path.dirname(f) or ".") == g})
    outline_files = list(dict.fromkeys(files + [p for ps in folder_files.values() for p in ps]))
    outlines = {f: file_outline(b, os.path.join(root, f), root) for f in outline_files}
    return OverviewPlan(os.path.basename(root.rstrip(os.sep)), readme_excerpt(root), folders, folder_files, files,
                        outlines, ring)


def write_overview(plan: OverviewPlan, llm) -> dict:
    """The sentences for a plan: `{"files": {..}, "folders": {..}, "repo": str, "ring": {qname: str}}`.
    `llm.json(prompt, max_out)` returns parsed JSON (the `prism.summaries.pipeline.Ollama` client)."""
    def one(prompt: str) -> str:
        got = llm.json(prompt, max_out=200) or {}
        return _clean(got.get("summary", "")) if isinstance(got, dict) else ""

    files = {f: one(FILE_PROMPT.format(path=f, outline=o or "(no functions)")) for f, o in plan.outlines.items()}
    folders = {}
    for g, paths in plan.folder_files.items():
        listed = "\n".join(f"- {p}: {files.get(p, '')}" for p in paths)
        folders[g] = one(FOLDER_PROMPT.format(path=g, files=listed or "(no files)"))
    repo = one(REPO_PROMPT.format(name=plan.repo, readme=plan.readme or "(none)",
                                  folders="\n".join(f"- {g}: {folders.get(g, '')}" for g, _n in plan.folders)))
    cards = {r.qname: r.card for r in plan.ring}
    ring: dict[str, str] = {}
    names = list(cards)
    for i in range(0, len(names), 12):
        ring.update(summarize_batch(llm, cards, names[i:i + 12]))
    return {"files": files, "folders": folders, "repo": repo, "ring": ring}


def render_overview(plan: OverviewPlan, text: dict) -> tuple[str, str]:
    """`(map section, second-ring section)` as markdown."""
    m = ["## 0. Map", f"Repository `{plan.repo}`: {text.get('repo') or '(no description)'}", "", "Main folders:"]
    m += [f"- `{g}` ({n} functions): {text.get('folders', {}).get(g, '')}" for g, n in plan.folders]
    m += ["", "Files near the change:"]
    m += [f"- `{f}`: {text.get('files', {}).get(f, '')}" for f in plan.files]
    r = ["## 9. Second ring (one line each)"]
    up = [x for x in plan.ring if x.relation == "caller of a caller"]
    similar = [x for x in plan.ring if x.relation == "similar"]
    if up:
        r.append("Callers of the callers:")
        r += [f"- `{x.qname}` ({x.file}:{x.line}), calls `{x.via}`: {text.get('ring', {}).get(x.qname, '')}" for x in up]
    if similar:
        r.append("Similar functions elsewhere:")
        r += [f"- `{x.qname}` ({x.file}:{x.line}; {x.via}): {text.get('ring', {}).get(x.qname, '')}" for x in similar]
    if not up and not similar:
        r.append("Nothing beyond the close-up.")
    return "\n".join(m) + "\n", "\n".join(r) + "\n"
