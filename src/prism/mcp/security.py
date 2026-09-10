"""Item 13 (second post-implementation audit): MCP Server Security Audit
& Path Sandboxing.

`prism mcp` is a long-lived process an LLM agent drives by calling tools
with arguments the agent itself generated - which makes every tool
argument untrusted input in the classic sense (prompt injection from a
fetched file/webpage/tool result can end up shaping what the agent
"decides" to call a tool with, same threat model as any other
LLM-in-the-loop system). Three concrete things this module closes:

  1. **Path sandboxing** (`assert_path_in_repo`): every tool accepts an
     optional `repo_path` override. Before this module, that string went
     straight into `os.path.abspath` and then `os.walk` with no boundary
     check at all - a `repo_path` pointed at, say, `/etc` or a `../../`
     sequence walking out of the intended tree would have Prism happily
     parse and return the *contents* of whatever source-looking files it
     finds there back to the calling agent. `prism.mcp.cache.GraphCache`
     now enforces a **sandbox root** whenever an operator has actually
     pinned the server to one repository (`PRISM_MCP_DEFAULT_REPO`, i.e.
     `prism mcp --repo PATH`) - every `repo_path` override must then
     resolve (through `os.path.realpath`, so a symlink can't be used to
     step outside either) to that root itself or a real subdirectory of
     it, `SecurityError` otherwise. A server started *without* `--repo`
     keeps its original, documented "index whatever `repo_path` an agent
     names, on demand" design - there is no operator-configured boundary
     to enforce in that shape, and pretending otherwise would silently
     break real, intended flexibility for a security property nobody
     asked for.
  2. **Pydantic validation with hard bounds**: `token_budget` is now
     bounds-checked (`1..MAX_TOKEN_BUDGET`) via a real `pydantic.Field`
     constraint, not just "whatever int the caller sent" - an
     unreasonably large budget is a resource-exhaustion vector against
     `ContextKnapsackPacker`'s own packing loop, not merely a UX
     nuisance. (The MCP SDK this server is built on already generates a
     JSON schema from each tool's plain type-hinted signature and does
     its own basic type coercion via Pydantic internally - what this
     module adds on top is the numeric *bound* and the symbol/tag
     *character allowlist* below, which plain type hints can't express.)
  3. **Symbol/tag name hygiene**: `target_symbol`/`tag` are restricted to
     a conservative character allowlist and a length cap. Concretely,
     neither string is ever used to construct a filesystem path or reach
     a shell/subprocess in this codebase today (both are plain dict-key
     lookups into an already-built `GlobalSymbolTable`/tag matrix) - so
     this is deliberately framed as *input hygiene / defense-in-depth*,
     not "closes an RCE", and this module says so rather than overstating
     the finding. It still catches a null byte, a control character, or
     an unbounded-length string reaching downstream string formatting/
     logging before that ever becomes a real problem, and keeps the door
     shut if a future change ever does turn one of these into a path or
     query component.
"""
from __future__ import annotations

import os
import re

from pydantic import BaseModel, Field, ValidationError


class SecurityError(Exception):
    """Raised by every validator in this module - a single, greppable
    exception type the MCP tool layer (`prism.mcp.server`) catches
    uniformly and turns into a `ToolError`, the same way
    `prism.mcp.cache.RepoNotFoundError` already is. Never a bare
    `ValueError`/`AssertionError`, so a caller can distinguish "this input
    was rejected for a security reason" from an ordinary validation bug.
    """


#: Item 13's own literal cap - a `token_budget` this large would have
#: `ContextKnapsackPacker` attempt to pack and render an unreasonably
#: large context; every real caller of this server budgets in the
#: hundreds-to-low-thousands (see `DEFAULT_TOKEN_BUDGET` in
#: `prism.mcp.server`), so this is generous headroom, not a practical
#: ceiling anyone should ever hit legitimately.
MAX_TOKEN_BUDGET = 128_000

#: Real qualified names in this codebase (`GlobalSymbolTable`) are built
#: from `path_to_module` (dot-joined path segments) plus identifier
#: segments a supported language's own grammar allows - Unicode word
#: characters, `.`, and `-` (a hyphenated file/directory name is valid on
#: disk even if unusual as an import path) cover every real one; a `/`,
#: `\`, null byte, or other control/whitespace character never appears in
#: one, so rejecting anything outside this set is a correctness-neutral
#: hardening (never rejects a real symbol name) that also stops a
#: malformed/hostile string before it reaches string formatting or a log
#: line.
_SYMBOL_NAME_PATTERN = re.compile(r"^[\w.\-]+$", re.UNICODE)
_SYMBOL_NAME_MAX_LENGTH = 512

#: Every tag this codebase's own `TaggingEngine`/metamodel ever registers
#: is `#` followed by lowercase letters/underscores (see
#: `prism.tagger.rules`/`prism.graph.metamodel` - `#auth_guard`,
#: `#db_write`, `#entrypoint`, ...) - `find_symbols_by_tag` already
#: rejects an unregistered tag with a `ToolError` listing the real ones,
#: so this is one more layer catching a malformed value before that
#: lookup even runs.
_TAG_PATTERN = re.compile(r"^#[a-z][a-z0-9_]*$")


class _TokenBudgetModel(BaseModel):
    token_budget: int = Field(gt=0, le=MAX_TOKEN_BUDGET)


def validate_token_budget(token_budget: int) -> int:
    try:
        return _TokenBudgetModel(token_budget=token_budget).token_budget
    except ValidationError as exc:
        raise SecurityError(
            f"token_budget must be a positive integer <= {MAX_TOKEN_BUDGET} (got {token_budget!r})"
        ) from exc


def validate_symbol_name(name: str) -> str:
    if not isinstance(name, str) or not name or len(name) > _SYMBOL_NAME_MAX_LENGTH or not _SYMBOL_NAME_PATTERN.match(name):
        raise SecurityError(f"invalid symbol name: {name!r}")
    return name


def validate_tag(tag: str) -> str:
    if not isinstance(tag, str) or not tag or len(tag) > _SYMBOL_NAME_MAX_LENGTH or not _TAG_PATTERN.match(tag):
        raise SecurityError(f"invalid tag: {tag!r}")
    return tag


def assert_path_in_repo(candidate: str, sandbox_root: str) -> str:
    """Resolves `candidate` and `sandbox_root` (`os.path.realpath`, so a
    symlink hop can't be used to step outside the sandbox either) and
    requires the former to be the latter or a real subdirectory of it.
    Returns the resolved `candidate` path on success; raises
    `SecurityError` otherwise - on a Windows-style "different drive, no
    common root at all" `ValueError` from `os.path.commonpath`, that's
    still definitionally "escapes the sandbox", not a crash.
    """
    resolved_candidate = os.path.realpath(os.path.abspath(candidate))
    resolved_root = os.path.realpath(os.path.abspath(sandbox_root))
    try:
        common = os.path.commonpath([resolved_candidate, resolved_root])
    except ValueError:
        common = None
    if common != resolved_root:
        raise SecurityError(
            f"repo_path '{candidate}' escapes the sandboxed repository root '{sandbox_root}'"
        )
    return resolved_candidate
