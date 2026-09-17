"""Multi-language entrypoint recognition with segment-anchored test exclusion."""
import re
from dataclasses import dataclass

from prism.semantics.class_extractor import Role

INDEX_FILENAMES: tuple[str, ...] = ("index.ts", "index.tsx", "index.js", "index.jsx")

TEST_SEGMENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(^|/)tests?/"),
    re.compile(r"(^|/)__tests__/"),
    re.compile(r"(^|/)testing/"),
    re.compile(r"(^|/)tests?\.py$"),
    re.compile(r"(^|/)conftest\.py$"),
    re.compile(r"(^|/)test_[^/]+\.py$"),
    re.compile(r"(^|/)[^/]+_test\.py$"),
    re.compile(r"(^|/)[^/]+_test\.go$"),
    re.compile(r"\.(test|spec)\.(ts|tsx|js|jsx)$"),
)


@dataclass
class SymbolNode:
    name: str
    file_path: str
    role: Role | None
    is_exported: bool = False
    is_package_root_export: bool = False


def is_test_file(normalized_path: str) -> bool:
    """Segment-anchored test file detection. Rejects false positives like
    latest_values.py, contest/file.py, protest_utils.py."""
    p = normalized_path.lower().replace("\\", "/").lstrip("./")
    return any(pat.search(p) for pat in TEST_SEGMENT_PATTERNS)


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
