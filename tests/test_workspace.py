"""Project-wide symbol scan and its effect on the unknown-intrinsic check."""

from __future__ import annotations

from magma_lsp.analysis.scope import defined_symbols
from magma_lsp.analysis.undefined import undefined_intrinsics
from magma_lsp.analysis.workspace import scan_workspace


def test_defined_symbols_collects_defs_and_forwards():
    src = (
        "forward Pre;\n"
        "function NamedFn(x) return x; end function;\n"
        "procedure NamedProc(~y) y := 0; end procedure;\n"
        "Helper := func< n | n + 1 >;\n"
        "intrinsic Thing(a::RngIntElt) -> RngIntElt {d} return a; end intrinsic;\n"
    )
    syms = defined_symbols(src)
    assert {"Pre", "NamedFn", "NamedProc", "Helper", "Thing"} <= syms


def test_scan_collects_across_files(tmp_path):
    (tmp_path / "a.m").write_text("function SSCentralizer(G, x) return x; end function;\n")
    (tmp_path / "b.magma").write_text("forward Other;\n")
    (tmp_path / "skip.txt").write_text("function NotMagma() end;\n")
    scan = scan_workspace([str(tmp_path)])
    assert not scan.truncated
    assert scan.files_scanned == 2
    assert "SSCentralizer" in scan.names and "Other" in scan.names


def test_scan_is_bounded(tmp_path):
    for i in range(5):
        (tmp_path / f"f{i}.m").write_text(f"function F{i}() return 0; end function;\n")
    scan = scan_workspace([str(tmp_path)], max_files=2)
    assert scan.truncated
    assert scan.names == frozenset()  # skipped rather than partially scanned


def test_cross_file_sibling_call_not_flagged_with_scan(tmp_path):
    (tmp_path / "lib.m").write_text("function SSCentralizer(G, x) return x; end function;\n")
    caller = "C := SSCentralizer(G, g);\n"
    intrinsics = frozenset({"GF"})
    # Without project symbols -> flagged; with them -> clean.
    assert undefined_intrinsics(caller, intrinsics)
    scan = scan_workspace([str(tmp_path)])
    assert undefined_intrinsics(caller, intrinsics | scan.names) == []
    # A genuine typo is still flagged.
    assert undefined_intrinsics("x := SSCentraliser(G, g);\n", intrinsics | scan.names)
