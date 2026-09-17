"""Canonical path normalization for cross-platform repository navigation."""
import posixpath
import re
import sys

SOURCE_EXTENSIONS: tuple[str, ...] = (
    ".py", ".pyi", ".pyw",
    ".ts", ".tsx", ".js", ".jsx",
    ".go",
)

DRIVE_LETTER_PATTERN = re.compile(r"^[a-zA-Z]:")


def get_module_parts(file_path: str, repo_root: str = "") -> list[str]:
    """Converts a repository file path into clean, repo-relative
    module segments.

    Guarantees:
      - Normalizes Windows drive letters and path separators.
      - Safe handling of empty or unprovided repo_root.
      - Rejects path traversal escapes.
      - Suffix slicing avoids character-set truncation
        (removesuffix semantics).
    """
    if not file_path:
        return []

    p = file_path.replace("\\", "/").strip()
    root = repo_root.replace("\\", "/").strip() if repo_root else ""

    is_windows_abs = bool(DRIVE_LETTER_PATTERN.match(p))
    is_posix_abs = posixpath.isabs(p)
    is_abs = is_windows_abs or is_posix_abs

    if is_abs:
        if root:
            # Length-preserving assumption: str.lower() preserves
            # length for ASCII. Non-ASCII paths on Windows are not
            # supported in MVP1.
            p_cmp = p.lower() if sys.platform == "win32" else p
            root_cmp = root.lower() if sys.platform == "win32" else root
            try:
                rel = posixpath.relpath(p_cmp, root_cmp)
                if rel == ".":
                    p = ""
                elif rel.startswith("../") or rel == "..":
                    raise ValueError(
                        f"Path traversal detected: '{file_path}' "
                        f"escapes repo root '{repo_root}'"
                    )
                else:
                    p = p[-len(rel):]
            except ValueError:
                p = DRIVE_LETTER_PATTERN.sub("", p).lstrip("/")
        else:
            p = DRIVE_LETTER_PATTERN.sub("", p).lstrip("/")

    p = posixpath.normpath(p)

    if p == ".." or p.startswith("../"):
        raise ValueError(
            f"Path traversal detected: '{file_path}' escapes "
            f"repository boundary"
        )

    for ext in SOURCE_EXTENSIONS:
        if p.endswith(ext):
            p = p[:-len(ext)]
            break

    return [seg for seg in p.split("/") if seg and seg != "."]
