"""Hermetic, local verification that `uvx --from .` runs Prism's CLI and MCP
server exactly the way an end-user's `uvx --from git+https://github.com/
<OWNER>/<REPO>.git prism ...` would - proving the zero-install VS Code/Claude
Desktop setup in `docs/mcp_setup.md` actually works before anyone tries it.

`--from .` (a local path) exercises the identical code path `uv` uses for a
`--from git+...` URL: it builds an ephemeral wheel from the working tree via
the project's own PEP 517 build backend (`setuptools.build_meta`, per
`pyproject.toml`) into a throwaway venv, then runs the `prism` console script
`[project.scripts]` declares - so anything this script catches (a missing
submodule, a dependency that only exists as a `pip install [extra]` someone
forgot to make core, a broken entry point) would equally break the real
git-URL path. No network access to GitHub itself is needed for this local
form, which is what makes it fast enough to run as part of routine
verification rather than only ever by hand against the real remote.

Checks, in order (see the corresponding `run_check` calls in `main()`):
  1. `uvx --from . prism --help` exits 0.
  2. `uvx --from . prism mcp --help` exits 0 and documents `stdio`, `sse`,
     and `--repo`.
  3. A real MCP JSON-RPC stdio handshake (`initialize` ->
     `notifications/initialized` -> `tools/list`) against
     `uvx --from . prism mcp --transport stdio --repo .` returns exactly the
     5 tools `prism.mcp.server` registers: `get_symbol_context`,
     `get_architectural_invariants`, `find_symbols_by_tag`,
     `get_graph_status`, `reindex_repo`.

Usage:
    python scripts/verify_uvx_execution.py

Requires `uv`/`uvx` on PATH (https://docs.astral.sh/uv/getting-started/installation/).
Exits 0 iff every check passes; prints a PASS/FAIL summary either way.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MCP_PROTOCOL_VERSION = "2025-03-26"
EXPECTED_TOOL_NAMES = frozenset(
    {"get_symbol_context", "get_architectural_invariants", "find_symbols_by_tag", "get_graph_status", "reindex_repo"}
)
SUBPROCESS_TIMEOUT_SECONDS = 120


class VerificationError(Exception):
    """A check failed - message is printed as the FAIL reason, no traceback."""


def _run_uvx(args: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess:
    """Runs `uvx --from . prism <args>` from the project root. Deliberately
    does NOT sanitize the environment the way a packaged-binary smoke test
    would: `uv` itself needs the host's normal environment (HOME, PATH for
    finding/downloading a Python interpreter, its own cache dirs, ...) to
    do its job - what this script is verifying is the *dependency
    resolution and entry-point wiring*, not process isolation.
    """
    return subprocess.run(
        ["uvx", "--from", ".", "prism", *args],
        input=input_text,
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
        cwd=str(PROJECT_ROOT),
    )


def check_uv_installed() -> None:
    if shutil.which("uvx") is None:
        raise VerificationError(
            "'uvx' was not found on PATH - install uv first: "
            "https://docs.astral.sh/uv/getting-started/installation/"
        )


def check_cli_help() -> None:
    result = _run_uvx(["--help"])
    if result.returncode != 0:
        raise VerificationError(f"`uvx --from . prism --help` exited {result.returncode}\nstderr:\n{result.stderr}")
    if "Prism" not in result.stdout:
        raise VerificationError(f"`prism --help` output looks wrong (missing the CLI's own description):\n{result.stdout}")


def check_mcp_help() -> None:
    result = _run_uvx(["mcp", "--help"])
    if result.returncode != 0:
        raise VerificationError(f"`uvx --from . prism mcp --help` exited {result.returncode}\nstderr:\n{result.stderr}")
    missing = [token for token in ("stdio", "sse", "--repo") if token not in result.stdout]
    if missing:
        raise VerificationError(
            f"`prism mcp --help` output is missing expected token(s) {missing}:\n{result.stdout}"
        )


def _mcp_stdio_handshake_payload() -> str:
    """`initialize` -> `notifications/initialized` -> `tools/list`, each as
    one newline-delimited JSON-RPC message - the same three-message
    handshake any real MCP client (VS Code, Claude Desktop) performs before
    it can call a tool, piped in as a single stdin batch since this
    verification doesn't need an interactive session.
    """
    initialize_request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "verify_uvx_execution", "version": "0.1"},
        },
    }
    initialized_notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    tools_list_request = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    messages = [initialize_request, initialized_notification, tools_list_request]
    return "\n".join(json.dumps(m) for m in messages) + "\n"


def check_mcp_stdio_tool_discovery() -> None:
    result = _run_uvx(
        ["mcp", "--transport", "stdio", "--repo", "."],
        input_text=_mcp_stdio_handshake_payload(),
    )
    if result.returncode != 0:
        raise VerificationError(
            f"`uvx --from . prism mcp --transport stdio --repo .` exited {result.returncode}\nstderr:\n{result.stderr}"
        )

    response_lines = [line for line in result.stdout.splitlines() if line.strip()]
    if len(response_lines) < 2:
        raise VerificationError(
            f"expected 2 JSON-RPC responses (initialize + tools/list), got {len(response_lines)}:\n{result.stdout}"
        )

    try:
        responses = {json.loads(line)["id"]: json.loads(line) for line in response_lines if "id" in json.loads(line)}
    except (json.JSONDecodeError, KeyError) as exc:
        raise VerificationError(f"could not parse JSON-RPC responses:\n{result.stdout}") from exc

    if 1 not in responses or "result" not in responses[1]:
        raise VerificationError(f"no successful `initialize` response (id=1) found:\n{result.stdout}")
    if 2 not in responses or "result" not in responses[2]:
        raise VerificationError(f"no successful `tools/list` response (id=2) found:\n{result.stdout}")

    tools = responses[2]["result"].get("tools", [])
    tool_names = {t["name"] for t in tools}
    missing = EXPECTED_TOOL_NAMES - tool_names
    if missing:
        raise VerificationError(
            f"tools/list is missing expected tool(s) {sorted(missing)} - got {sorted(tool_names)}"
        )


def run_check(name: str, fn) -> bool:
    print(f"[ ] {name} ...", end=" ", flush=True)
    try:
        fn()
    except VerificationError as exc:
        print("FAIL")
        print(f"    {exc}")
        return False
    except subprocess.TimeoutExpired:
        print("FAIL")
        print(f"    timed out after {SUBPROCESS_TIMEOUT_SECONDS}s")
        return False
    print("PASS")
    return True


def main() -> int:
    print(f"Verifying `uvx --from .` execution against {PROJECT_ROOT} ...\n")

    if not run_check("uv/uvx is installed", check_uv_installed):
        return 1

    checks = [
        ("uvx --from . prism --help", check_cli_help),
        ("uvx --from . prism mcp --help (documents stdio/sse/--repo)", check_mcp_help),
        ("MCP stdio handshake returns all 5 tools", check_mcp_stdio_tool_discovery),
    ]
    results = [run_check(name, fn) for name, fn in checks]

    print()
    passed = sum(results)
    print(f"{passed}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
