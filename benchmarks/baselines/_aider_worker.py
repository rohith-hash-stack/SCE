"""Runs Aider's official `RepoMap` (package `aider-chat`) and prints JSON.

Executed as a subprocess under the Python environment that has
`aider-chat` installed (see `aider_official.py`), so Aider's pinned
dependencies never mix with PRISM's. Imports nothing from this repository.

stdin JSON:  {"root", "files", "prompt", "map_tokens", "model"}
stdout JSON: {"map", "aider_tokens", "tags": [[rel_fname, line, name], ...],
              "mentioned_idents", "mentioned_fnames", "aider_version"}
"""
import json
import os
import sys
from importlib.metadata import version

from aider.coders.base_coder import Coder
from aider.io import InputOutput
from aider.models import Model
from aider.repomap import RepoMap


class _CoderView:
    """Just enough of a `Coder` for Aider's own mention helpers to run
    unmodified: `Coder.get_ident_mentions`, `Coder.get_file_mentions` and
    `Coder.get_ident_filename_matches` are called as plain functions with
    this object as `self`. A benchmark cell has no files in chat and no
    read-only files."""

    def __init__(self, rel_fnames):
        self._rel = list(rel_fnames)
        self.abs_read_only_fnames = set()

    def get_all_relative_files(self):
        return self._rel

    def get_addable_relative_files(self):
        return set(self._rel)

    def get_inchat_relative_files(self):
        return []

    def get_rel_fname(self, fname):
        return fname




class RecordingRepoMap(RepoMap):
    """Official RepoMap; additionally records which tags each candidate
    tree rendered, so the caller can tell exactly which definitions ended
    up in the final (binary-searched) map."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.renders = []

    def to_tree(self, tags, chat_rel_fnames):
        out = super().to_tree(tags, chat_rel_fnames)
        self.renders.append((out, [t for t in tags if len(t) > 1]))
        return out


def main():
    req = json.load(sys.stdin)
    root = req["root"]
    files = req["files"]
    rel = [os.path.relpath(f, root) for f in files]
    model = Model(req.get("model", "gpt-4o-mini"))
    io = InputOutput(yes=True, pretty=False, fancy_input=False)
    rm = RecordingRepoMap(map_tokens=req["map_tokens"], root=root, main_model=model, io=io)
    view = _CoderView(rel)
    idents = Coder.get_ident_mentions(view, req["prompt"])
    fnames = Coder.get_file_mentions(view, req["prompt"]) | Coder.get_ident_filename_matches(view, idents)
    repo_map = rm.get_repo_map(chat_files=[], other_files=files, mentioned_fnames=fnames, mentioned_idents=idents) or ""
    tags = []
    for out, rendered_tags in rm.renders:
        if out and out in repo_map:
            tags = [[t.rel_fname, t.line, t.name] for t in rendered_tags]
            break
    json.dump({
        "map": repo_map,
        "aider_tokens": model.token_count(repo_map),
        "tags": tags,
        "mentioned_idents": sorted(i for i in idents if i),
        "mentioned_fnames": sorted(fnames),
        "aider_version": version("aider-chat"),
    }, sys.stdout)


if __name__ == "__main__":
    main()
