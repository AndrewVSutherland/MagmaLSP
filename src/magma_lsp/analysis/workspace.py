"""Project-wide symbol scan: names AND definition sites across the workspace's Magma files.

A multi-file Magma package calls helper functions and intrinsics defined in sibling files
(shared via ``load``, ``Attach``, or a package ``.spec``). The scan serves two consumers:

- the static unknown-intrinsic check (``analysis/undefined``): the *name set* keeps sibling
  helpers from being false-flagged (removes the ~0.07% residual);
- navigation (go-to-definition, workspace/symbol, completion): the *definition sites* let the
  server point at project-defined intrinsics/functions, not only the package DB.

File discovery walks the workspace roots for ``.m`` / ``.magma`` files and additionally parses
any ``*.spec`` files found there (same grammar as Magma's package specs, via ``db.spec``):
a spec names exactly the files a project attaches, so its entries join the scan even when they
sit outside the walked roots.

Bounded: a hard ``max_files`` cap (so opening, say, /opt/magma doesn't trigger a
multi-thousand-file parse) and skipped infrastructure directories. The scan is best-effort —
failures are ignored.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..db.spec import attached_files
from .scope import defined_symbols
from .symbols import document_symbols

_SKIP_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__", ".ruff_cache"}
_EXTS = (".m", ".magma")
_MAX_SPECS = 40  # spec files parsed per scan (a project has a handful; cap the pathological)


@dataclass(frozen=True)
class WorkspaceDef:
    """One top-level definition site in a workspace file. Positions are tree-sitter
    coordinates: 0-based line, 0-based BYTE column (callers converting to editor positions
    can treat the column as-is — ``intrinsic Name(`` / ``function Name(`` header prefixes
    are ASCII in practice)."""

    name: str
    kind: str  # "intrinsic" | "function" | "procedure" | "type"
    file: str
    line: int
    col: int
    detail: str = ""


@dataclass(frozen=True)
class WorkspaceScan:
    names: frozenset[str]
    files_scanned: int
    truncated: bool  # hit the file cap
    # name -> definition sites across the workspace (subset of `names`: only top-level
    # intrinsic/function/procedure/type definitions carry a useful jump target)
    defs: dict[str, tuple[WorkspaceDef, ...]] | None = None


def _iter_files(roots: list[str], max_files: int) -> tuple[list[str], bool]:
    files: list[str] = []
    seen: set[str] = set()  # overlapping roots (workspace folder + CLAUDE_PROJECT_DIR) dedup
    specs: list[str] = []

    def add(path: str) -> bool:
        """True while under the cap."""
        path = os.path.normpath(path)
        if path in seen:
            return True
        seen.add(path)
        files.append(path)
        return len(files) <= max_files

    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, dirs, filenames in os.walk(root):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for fn in filenames:
                if fn.endswith(_EXTS):
                    if not add(os.path.join(dirpath, fn)):
                        return files, True
                elif fn.endswith(".spec") and len(specs) < _MAX_SPECS:
                    specs.append(os.path.join(dirpath, fn))
    # Spec entries: the project's own attach list. Entries can point outside the walked
    # roots (e.g. `+../shared/shared.spec`); fold them in, still bounded by the cap.
    for spec in specs:
        try:
            listed = attached_files(spec)
        except Exception:  # a malformed spec must not break the scan
            continue
        for path in sorted(listed):
            if os.path.isfile(path) and not add(path):
                return files, True
    return files, False


# cache: path -> ((mtime_ns, size), that file's defined names, its definition sites)
ScanCache = dict[str, tuple[tuple[int, int], frozenset[str], tuple[WorkspaceDef, ...]]]


def _file_defs(path: str, data: bytes) -> tuple[WorkspaceDef, ...]:
    out = []
    for s in document_symbols(data):
        nl, nc = s.name_pos()
        out.append(WorkspaceDef(s.name, s.kind, path, nl, nc, detail=s.detail))
    return tuple(out)


def scan_workspace(
    roots: list[str], *, max_files: int = 2000, cache: ScanCache | None = None
) -> WorkspaceScan:
    """Union of ``defined_symbols`` (names) and ``document_symbols`` (definition sites) over
    the workspace's Magma files.

    With ``cache`` (a dict the caller keeps between scans), unchanged files (same mtime) are
    not re-read or re-parsed — a save rescans only the saved file instead of the whole tree.
    """
    files, truncated = _iter_files(roots, max_files)
    if truncated:
        # Workspace too large to scan eagerly; skip rather than block. The per-document scope
        # and the Magma binding pass still apply.
        return WorkspaceScan(names=frozenset(), files_scanned=0, truncated=True)

    names: set[str] = set()
    defs: dict[str, list[WorkspaceDef]] = {}
    scanned = 0
    for path in files:
        try:
            st = os.stat(path)
        except OSError:
            continue
        # (mtime_ns, size): a bare float mtime can collide on filesystems with coarse
        # timestamp resolution, silently serving stale symbols after a same-second edit
        stamp = (st.st_mtime_ns, st.st_size)
        file_names: frozenset[str]
        file_defs: tuple[WorkspaceDef, ...]
        hit = cache.get(path) if cache is not None else None
        if hit is not None and hit[0] == stamp:
            file_names, file_defs = hit[1], hit[2]
        else:
            try:
                with open(path, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            try:
                file_names = frozenset(defined_symbols(data))
                file_defs = _file_defs(path, data)
            except Exception:  # never let one bad file break the scan
                continue
            if cache is not None:
                cache[path] = (stamp, file_names, file_defs)
        names |= file_names
        for d in file_defs:
            defs.setdefault(d.name, []).append(d)
        scanned += 1
    if cache is not None:
        for stale in set(cache) - set(files):
            del cache[stale]
    return WorkspaceScan(
        names=frozenset(names),
        files_scanned=scanned,
        truncated=False,
        defs={n: tuple(v) for n, v in defs.items()},
    )
