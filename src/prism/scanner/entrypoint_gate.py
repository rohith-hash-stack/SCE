"""Multi-language entrypoint recognition with segment-anchored test exclusion."""
import os
import re
from dataclasses import dataclass

from prism.semantics.class_extractor import Role

INDEX_FILENAMES: tuple[str, ...] = ("index.ts", "index.tsx", "index.js", "index.jsx")

TEST_SEGMENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(^|/)tests?/"),
    re.compile(r"(^|/)__tests__/"),
    re.compile(r"(^|/)testing/"),
    # `spec/` holds tests by the RSpec and JS conventions only; in Python (litestar's `openapi/spec`) it is
    # production code (Phase 2 bug fix, `harness/experiments/r4/AMENDMENTS.md`).
    re.compile(r"(^|/)specs?/[^\n]*\.(ts|tsx|js|jsx|mjs|cjs|mts|cts|rb)$"),
    re.compile(r"(^|/)conftest\.py$"),
    re.compile(r"(^|/)test_[^/]+\.py$"),
    re.compile(r"(^|/)[^/]+_test\.py$"),
    re.compile(r"(^|/)[^/]+_test\.go$"),
    re.compile(r"\.(test|spec)\.(ts|tsx|js|jsx|mjs|cjs|mts|cts)$"),
)


@dataclass
class SymbolNode:
    name: str
    file_path: str
    role: Role | None
    is_exported: bool = False
    is_package_root_export: bool = False


#: A module named `test.py`/`tests.py` is a test module only when its code is test code: Django's `app/tests.py` is,
#: werkzeug's public `werkzeug/test.py` (its test client) and pydantic-ai's `models/test.py` (`TestModel`) are not.
_TEST_MODULE_NAME = re.compile(r"(^|/)tests?\.py$")
_TEST_CODE = re.compile(r"^\s*(?:async\s+)?def\s+test\w*\s*\(|^\s*class\s+\w+\s*\([^)]*TestCase|"
                        r"^\s*(?:import|from)\s+(?:pytest|unittest)\b", re.M)


def _holds_test_code(path: str, root: str | None) -> bool | None:
    """Whether the file's code is test code; None when it cannot be read."""
    full = path if os.path.isabs(path) or root is None else os.path.join(root, path)
    try:
        with open(full, "rb") as fh:
            text = fh.read(400_000).decode("utf8", "replace")
    except OSError:
        return None
    return bool(_TEST_CODE.search(text))


def is_test_file(normalized_path: str, root: str | None = None) -> bool:
    """Segment-anchored test file detection. Rejects false positives like
    latest_values.py, contest/file.py, protest_utils.py.

    A module named `test.py`/`tests.py` outside a test directory is read (`normalized_path` itself when absolute,
    else under `root`): it is a test file when it holds test code (a `test*` function, a `TestCase` subclass, a
    pytest/unittest import); a file that cannot be read keeps the name rule (a test file)."""
    p = normalized_path.lower().replace("\\", "/").lstrip("./")
    if any(pat.search(p) for pat in TEST_SEGMENT_PATTERNS):
        return True
    if _TEST_MODULE_NAME.search(p):
        held = _holds_test_code(normalized_path, root)
        return True if held is None else held
    return False


def is_public_symbol(node: SymbolNode, lang: str) -> bool:
    normalized_path = node.file_path.replace("\\", "/")
    if is_test_file(normalized_path):
        return False

    if lang == "python":
        parts = normalized_path.split("/")
        is_private_module = any(
            part.startswith("_") and not part.startswith("__")
            for part in parts
        )
        return not node.name.startswith("_") and not is_private_module

    if lang == "go":
        if normalized_path.endswith("_test.go"):
            return False
        return len(node.name) > 0 and node.name[0].isupper()

    if lang in ("typescript", "javascript"):
        if any(normalized_path.endswith(idx) for idx in INDEX_FILENAMES):
            return True
        return node.is_exported

    return False


def is_overview_entrypoint(node: SymbolNode, lang: str) -> bool:
    if not is_public_symbol(node, lang):
        return False

    if lang == "python":
        return (
            node.role in {Role.ENTRYPOINT, Role.FACADE}
            or node.is_package_root_export
        )

    if lang == "go":
        return node.role == Role.ENTRYPOINT

    if lang in ("typescript", "javascript"):
        return node.role in {Role.ENTRYPOINT, Role.FACADE}

    return False
