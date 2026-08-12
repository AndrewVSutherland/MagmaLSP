"""Project-wide symbol scan: names defined across the workspace's ``.m`` / ``.magma`` files.

A multi-file Magma package calls helper functions defined in sibling files (and shared via the
package spec). Those calls would otherwise be false-flagged by the static unknown-intrinsic check
(``analysis/undefined``). Scanning the workspace's own files for their ``defined_symbols`` and
treating those names as known removes that ~0.07% residual.

Bounded: a hard ``max_files`` cap (so opening, say, /opt/magma doesn't trigger a multi-thousand-file
parse) and skipped infrastructure directories. The scan is best-effort — failures are ignored.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .scope import defined_symbols

_SKIP_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__", ".ruff_cache"}
_EXTS = (".m", ".magma")


@dataclass(frozen=True)
class WorkspaceScan:
    names: frozenset[str]
    files_scanned: int
    truncated: bool  # hit the file cap


def _iter_files(roots: list[str], max_files: int) -> tuple[list[str], bool]:
    files: list[str] = []
    seen: set[str] = set()  # overlapping roots (workspace folder + CLAUDE_PROJECT_DIR) dedup
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, dirs, filenames in os.walk(root):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for fn in filenames:
                if fn.endswith(_EXTS):
                    path = os.path.normpath(os.path.join(dirpath, fn))
                    if path in seen:
                        continue
                    seen.add(path)
                    files.append(path)
                    if len(files) > max_files:
                        return files, True
    return files, False


# cache type: path -> (mtime, that file's defined names)
ScanCache = dict[str, tuple[float, frozenset[str]]]


def scan_workspace(
    roots: list[str], *, max_files: int = 2000, cache: ScanCache | None = None
) -> WorkspaceScan:
    """Union of ``defined_symbols`` over the workspace's Magma files.

    With ``cache`` (a dict the caller keeps between scans), unchanged files (same mtime) are
    not re-read or re-parsed — a save rescans only the saved file instead of the whole tree.
    """
    files, truncated = _iter_files(roots, max_files)
    if truncated:
        # Workspace too large to scan eagerly; skip rather than block. The per-document scope
        # and the Magma binding pass still apply.
        return WorkspaceScan(names=frozenset(), files_scanned=0, truncated=True)

    names: set[str] = set()
    scanned = 0
    for path in files:
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            continue
        if cache is not None:
            hit = cache.get(path)
            if hit is not None and hit[0] == mtime:
                names |= hit[1]
                scanned += 1
                continue
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        try:
            file_names = frozenset(defined_symbols(data))
        except Exception:  # never let one bad file break the scan
            continue
        if cache is not None:
            cache[path] = (mtime, file_names)
        names |= file_names
        scanned += 1
    if cache is not None:
        for stale in set(cache) - set(files):
            del cache[stale]
    return WorkspaceScan(names=frozenset(names), files_scanned=scanned, truncated=False)
