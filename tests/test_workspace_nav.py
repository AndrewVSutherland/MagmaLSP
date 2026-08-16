"""Issue #16: project-defined intrinsics/functions are first-class for navigation — the
workspace scan collects definition SITES (not just names), spec files extend the scanned set,
go-to-definition returns ALL overload locations, and workspace/symbol + completion surface
project symbols."""

from __future__ import annotations

from lsprotocol import types as t

from magma_lsp import server as srv
from magma_lsp.analysis.workspace import WorkspaceDef, scan_workspace
from magma_lsp.db.index import SignatureIndex
from magma_lsp.db.model import Intrinsic, MagmaDB, Param, Signature, SourceLocation

INTRINSIC_SRC = (
    "intrinsic MyHelper(x::RngIntElt) -> RngIntElt\n"
    "{Does helper things}\n"
    "  return x + 1;\n"
    "end intrinsic;\n"
)


def test_scan_collects_definition_sites(tmp_path):
    (tmp_path / "helper.m").write_text(INTRINSIC_SRC)
    (tmp_path / "util.m").write_text("Twice := function(n) return 2*n; end function;\n")
    scan = scan_workspace([str(tmp_path)])
    assert scan.defs is not None
    hd = scan.defs["MyHelper"]
    assert len(hd) == 1 and hd[0].kind == "intrinsic"
    assert hd[0].file.endswith("helper.m") and hd[0].line == 0
    assert scan.defs["Twice"][0].kind == "function"


def test_spec_files_extend_the_scan(tmp_path):
    # the spec lists a file OUTSIDE the walked root: it must still join the scan
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "helper.m").write_text(INTRINSIC_SRC)
    root = tmp_path / "proj"
    root.mkdir()
    (root / "main.m").write_text("y := MyHelper(1);\n")
    (root / "pkg.spec").write_text("../outside/helper.m\n")
    scan = scan_workspace([str(root)])
    assert "MyHelper" in scan.names
    assert scan.defs and scan.defs["MyHelper"][0].file.endswith("helper.m")


def _two_overload_index() -> SignatureIndex:
    a = Signature(
        name="Dimension",
        args=[Param("V", "ModTupFld")],
        returns=["RngIntElt"],
        doc="Dimension of a vector space.",
        source=SourceLocation("/opt/magma/package/vs.m", 10, 1),
    )
    b = Signature(
        name="Dimension",
        args=[Param("C", "SmpCpx")],
        returns=["RngIntElt"],
        source=SourceLocation("/opt/magma/package/simplicialhomology.m", 99, 1),
    )
    return SignatureIndex(
        MagmaDB(version="test", intrinsics={"Dimension": Intrinsic("Dimension", [b, a])})
    )


def test_index_definitions_returns_all_sites_documented_first():
    idx = _two_overload_index()
    locs = idx.definitions("Dimension")
    assert [loc.file for loc in locs] == [
        "/opt/magma/package/vs.m",  # documented overload ranks first
        "/opt/magma/package/simplicialhomology.m",
    ]
    assert idx.definition("Dimension").file == "/opt/magma/package/vs.m"
    assert idx.definitions("Nope") == []


def test_workspace_symbol_merges_project_and_db():
    ls = srv.MagmaLanguageServer()
    ls.index = _two_overload_index()
    ls.workspace_defs = {
        "MyHelper": (WorkspaceDef("MyHelper", "intrinsic", "/proj/helper.m", 0, 10),)
    }
    out = srv.workspace_symbol(ls, t.WorkspaceSymbolParams(query="my"))
    assert [s.name for s in out] == ["MyHelper"]
    assert out[0].location.uri.endswith("/proj/helper.m")
    both = srv.workspace_symbol(ls, t.WorkspaceSymbolParams(query=""))
    names = [s.name for s in both]
    assert names[0] == "MyHelper"  # project symbols first
    assert "Dimension" in names


def test_workspace_defs_power_definition_without_db(tmp_path):
    # rescan_workspace populates defs even with no signature DB, from a real tmp project
    ls = srv.MagmaLanguageServer()
    (tmp_path / "helper.m").write_text(INTRINSIC_SRC)
    ls.workspace_roots = [str(tmp_path)]
    ls.rescan_workspace()
    assert "MyHelper" in ls.workspace_defs
    assert ls.workspace_symbols >= {"MyHelper"}
