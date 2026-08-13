"""Where the built signature DB lives on disk.

The DB is version-specific and regenerated per Magma install, so it is cached under the user
data dir (overridable via ``MAGMA_LSP_DB`` for an explicit path, or ``MAGMA_LSP_CACHE_DIR``).
"""

from __future__ import annotations

import os
from pathlib import Path


def cache_dir() -> Path:
    override = os.environ.get("MAGMA_LSP_CACHE_DIR")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(Path.home(), ".cache")
    return Path(base) / "magma-lsp"


def db_path_for_version(version: str) -> Path:
    explicit = os.environ.get("MAGMA_LSP_DB")
    if explicit:
        return Path(explicit)
    safe = version.replace("/", "_")
    return cache_dir() / f"{safe}.magmadb.json"


def find_existing_db(version: str) -> Path | None:
    p = db_path_for_version(version)
    return p if p.exists() else None


def newest_cached_db() -> Path | None:
    explicit = os.environ.get("MAGMA_LSP_DB")
    if explicit:
        # An explicit path is a contract: honor it or report nothing, never silently fall
        # back to an unrelated cache file.
        p = Path(explicit)
        return p if p.exists() else None
    d = cache_dir()
    if not d.is_dir():
        return None
    cands = sorted(d.glob("*.magmadb.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0] if cands else None


def best_cached_db(preferred_version: str | None = None) -> Path | None:
    """The DB to load: the explicit ``MAGMA_LSP_DB`` if set, else the exact artifact for
    ``preferred_version`` (the installed Magma), else the newest cached one."""
    if os.environ.get("MAGMA_LSP_DB"):
        return newest_cached_db()
    if preferred_version:
        p = db_path_for_version(preferred_version)
        if p.exists():
            return p
    return newest_cached_db()
