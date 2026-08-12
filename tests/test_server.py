"""Unit tests for server helpers and the diagnostics pipeline (no live LSP transport)."""

from __future__ import annotations

from lsprotocol import types as t

from magma_lsp import server as srv
from magma_lsp.db.index import SignatureIndex
from magma_lsp.db.model import Intrinsic, MagmaDB, Param, Signature, SourceLocation


def _index() -> SignatureIndex:
    sig = Signature(
        name="Factorial",
        args=[Param("n", "RngIntElt")],
        returns=["RngIntElt"],
        doc="The factorial.",
        source=SourceLocation("/opt/magma/x.m", 3, 1),
    )
    return SignatureIndex(
        MagmaDB(version="2.29-7", intrinsics={"Factorial": Intrinsic("Factorial", [sig])})
    )


def test_word_and_prefix():
    text = "x := Factorial(5);"
    assert srv._word_at(text, t.Position(0, 8)) == "Factorial"
    assert srv._prefix_at(text, t.Position(0, 9)) == "Fact"  # chars strictly before col 9


def test_enclosing_call_name():
    text = "x := Factorial(5, 6);\n"
    call = srv._enclosing_call(text, t.Position(0, 16))
    assert call is not None and call.children[0].text == b"Factorial"


def test_compute_diagnostics_lints_only():
    ls = srv.MagmaLanguageServer()
    ls.enable_lints = True
    ls.magma_available = False  # force fast path (tree-sitter + lints, no Magma)
    text = "f := function(n)\n    dead := 1;\n    return n;\nend function;\n"
    diags = srv._compute_diagnostics(ls, text, run_magma=False)
    msgs = [d.message for d in diags]
    assert any("dead" in m and "never used" in m for m in msgs)
    lint = next(d for d in diags if "dead" in d.message)
    assert lint.severity == t.DiagnosticSeverity.Warning
    assert lint.tags == [t.DiagnosticTag.Unnecessary]


def test_unknown_intrinsic_on_fast_path():
    ls = srv.MagmaLanguageServer()
    ls.magma_available = False  # fast path -> static undefined check runs
    ls.enable_unknown_intrinsics = True
    ls.intrinsic_names = frozenset({"EllipticCurve"})
    diags = srv._compute_diagnostics(ls, "E := EllipitcCurve([0,1]);\n", run_magma=False)
    assert any("EllipitcCurve" in d.message and "not a known intrinsic" in d.message for d in diags)
    # a real intrinsic is clean
    clean = srv._compute_diagnostics(ls, "E := EllipticCurve([0,1]);\n", run_magma=False)
    assert not any("not a known intrinsic" in d.message for d in clean)


def test_workspace_scan_suppresses_cross_file_calls(tmp_path):
    (tmp_path / "lib.m").write_text("function Helper(x) return x; end function;\n")
    ls = srv.MagmaLanguageServer()
    ls.magma_available = False
    ls.enable_unknown_intrinsics = True
    ls.intrinsic_names = frozenset({"EllipticCurve"})
    caller = "z := Helper(3);\n"
    # before scanning the project, the sibling-defined Helper looks undefined
    before = srv._compute_diagnostics(ls, caller, run_magma=False)
    assert any("Helper" in d.message for d in before)
    # after scanning the workspace, it is known
    ls.workspace_roots = [str(tmp_path)]
    ls.rescan_workspace()
    assert "Helper" in ls.known_call_names()
    after = srv._compute_diagnostics(ls, caller, run_magma=False)
    assert not any("Helper" in d.message for d in after)


def test_tree_sitter_syntax_error_path():
    ls = srv.MagmaLanguageServer()
    ls.magma_available = False
    diags = srv._compute_diagnostics(ls, "x := 2 +\n", run_magma=False)
    assert any(d.severity == t.DiagnosticSeverity.Error for d in diags)


def test_index_powers_hover_completion_definition():
    idx = _index()
    assert "Factorial" in idx.complete("Fac")
    assert idx.definition("Factorial").line == 3
    assert "factorial" in idx.hover_markdown("Factorial").lower()
