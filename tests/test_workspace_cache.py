"""Incremental scan_workspace cache: hits, invalidation on mtime change, stale-entry eviction."""

from __future__ import annotations

import os

from magma_lsp.analysis.workspace import scan_workspace


def test_scan_populates_cache(tmp_path):
    (tmp_path / "a.m").write_text("Alpha := function(n) return n; end function;\n")
    (tmp_path / "b.m").write_text("Beta := function(n) return n; end function;\n")
    cache: dict = {}
    scan = scan_workspace([str(tmp_path)], cache=cache)
    assert {"Alpha", "Beta"} <= set(scan.names)
    assert scan.files_scanned == 2
    assert set(cache) == {
        os.path.normpath(str(tmp_path / "a.m")),
        os.path.normpath(str(tmp_path / "b.m")),
    }


def test_unchanged_files_are_served_from_cache(tmp_path):
    (tmp_path / "a.m").write_text("Alpha := 1;\n")
    cache: dict = {}
    scan_workspace([str(tmp_path)], cache=cache)
    # Poison the cached names while keeping the mtime: a rescan must use the cache entry
    # (proving the file was not re-read or re-parsed).
    key = os.path.normpath(str(tmp_path / "a.m"))
    mtime, _names, _defs = cache[key]
    cache[key] = (mtime, frozenset({"Injected"}), ())
    scan = scan_workspace([str(tmp_path)], cache=cache)
    assert "Injected" in scan.names
    assert "Alpha" not in scan.names


def test_modified_file_is_rescanned(tmp_path):
    a = tmp_path / "a.m"
    a.write_text("Alpha := 1;\n")
    cache: dict = {}
    scan_workspace([str(tmp_path)], cache=cache)

    a.write_text("Gamma := 1;\n")
    st = os.stat(a)
    os.utime(a, (st.st_atime, st.st_mtime + 10))  # force a visible mtime bump
    scan = scan_workspace([str(tmp_path)], cache=cache)
    assert "Gamma" in scan.names
    assert "Alpha" not in scan.names
    key = os.path.normpath(str(a))
    assert cache[key][1] == frozenset({"Gamma"})  # cache entry refreshed


def test_same_mtime_different_size_edit_is_rescanned(tmp_path):
    """A same-second edit (coarse filesystem timestamps) must not serve stale symbols: the
    cache key is (mtime_ns, size), so a size-changing edit invalidates even when the mtime
    is byte-identical (codex #12 round 4)."""
    a = tmp_path / "a.m"
    a.write_text("Alpha := 1;\n")
    cache: dict = {}
    scan_workspace([str(tmp_path)], cache=cache)
    st = os.stat(a)
    a.write_text("Gamma := 12345;\n")  # different size
    os.utime(a, ns=(st.st_atime_ns, st.st_mtime_ns))  # restore the exact old mtime
    scan = scan_workspace([str(tmp_path)], cache=cache)
    assert "Gamma" in scan.names
    assert "Alpha" not in scan.names


def test_deleted_file_entry_is_evicted(tmp_path):
    a = tmp_path / "a.m"
    b = tmp_path / "b.m"
    a.write_text("Alpha := 1;\n")
    b.write_text("Beta := 1;\n")
    cache: dict = {}
    scan_workspace([str(tmp_path)], cache=cache)
    assert len(cache) == 2

    b.unlink()
    scan = scan_workspace([str(tmp_path)], cache=cache)
    assert "Beta" not in scan.names
    assert set(cache) == {os.path.normpath(str(a))}
