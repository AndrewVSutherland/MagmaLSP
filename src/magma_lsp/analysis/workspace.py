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
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, dirs, filenames in os.walk(root):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for fn in filenames:
                if fn.endswith(_EXTS):
                    files.append(os.path.join(dirpath, fn))
                    if len(files) > max_files:
                        return files, True
    return files, False


def scan_workspace(roots: list[str], *, max_files: int = 2000) -> WorkspaceScan:
    files, truncated = _iter_files(roots, max_files)
    if truncated:
        # Workspace too large to scan eagerly; skip rather than block. The per-document scope
        # and the Magma binding pass still apply.
        return WorkspaceScan(names=frozenset(), files_scanned=0, truncated=True)

    names: set[str] = set()
    scanned = 0
    for path in files:
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        try:
            names |= defined_symbols(data)
        except Exception:  # never let one bad file break the scan
            continue
        scanned += 1
    return WorkspaceScan(names=frozenset(names), files_scanned=scanned, truncated=False)
