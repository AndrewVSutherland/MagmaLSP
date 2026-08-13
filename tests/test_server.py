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


def test_workspace_scan_softens_cross_file_calls(tmp_path):
    """A workspace sibling's definition softens the unknown-name diagnostic to the
    reachability warning — on the STATIC (edit) path too, not silence (codex #12 round 11)."""
    (tmp_path / "lib.m").write_text("function Helper(x) return x; end function;\n")
    ls = srv.MagmaLanguageServer()
    ls.magma_available = False
    ls.enable_unknown_intrinsics = True
    ls.intrinsic_names = frozenset({"EllipticCurve"})
    caller = "z := Helper(3);\n"
    # before scanning the project, the sibling-defined Helper looks undefined
    before = srv._compute_diagnostics(ls, caller, run_magma=False)
    assert any("Helper" in d.message and "not a known intrinsic" in d.message for d in before)
    # after scanning the workspace: softened warning, not silence and not "unknown"
    ls.workspace_roots = [str(tmp_path)]
    ls.rescan_workspace()
    assert "Helper" in ls.known_call_names()
    after = srv._compute_diagnostics(ls, caller, run_magma=False)
    hits = [d for d in after if "Helper" in d.message]
    assert hits and all("load-ed or attached" in d.message for d in hits)
    assert all(d.severity == t.DiagnosticSeverity.Warning for d in hits)


def test_active_parameter_stops_at_optional_colon():
    """Commas separating `: P := v` optional arguments are not positional separators
    (codex #12 round 11)."""
    text = "x := Foo(a : Al := 1, Proof := true);\n"
    tree = srv.new_parser().parse(text.encode())
    call = None
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type == "call":
            call = n
        stack.extend(n.children)
    assert call is not None
    # cursor at the very end of the argument list: only positional commas (none) count
    assert srv._active_parameter(call, t.Position(0, 35)) == 0


def test_invalidate_scanned_file_beats_stat_blind_edit(tmp_path):
    """A same-size edit with an unchanged timestamp is invisible to the (mtime_ns, size)
    cache key; didSave eviction must force the re-read anyway (codex #12 round 7)."""
    import os

    a = tmp_path / "a.m"
    a.write_text("Old := 1;\n")
    ls = srv.MagmaLanguageServer()
    ls.workspace_roots = [str(tmp_path)]
    ls.rescan_workspace()
    assert "Old" in ls.workspace_symbols
    st = os.stat(a)
    a.write_text("New := 1;\n")  # same size...
    os.utime(a, ns=(st.st_atime_ns, st.st_mtime_ns))  # ...same timestamp: stat can't tell
    ls.rescan_workspace()
    assert "Old" in ls.workspace_symbols  # the residual staleness the eviction exists for
    ls.invalidate_scanned_file(str(a))
    ls.rescan_workspace()
    assert "New" in ls.workspace_symbols
    assert "Old" not in ls.workspace_symbols


def test_select_signature_picks_fitting_overload_and_clamps():
    """The active signature must fit the commas typed, and the active parameter must stay
    inside it (codex #12 round 5)."""

    def sig(n):
        return Signature(name="F", args=[Param(f"a{i}", "RngIntElt") for i in range(n)])

    # two commas typed -> the 1-arg overload can't be active; the 3-arg one is
    idx, param = srv._select_signature([sig(1), sig(3)], 2)
    assert (idx, param) == (1, 2)
    # more commas than ANY overload takes -> widest overload, param clamped to its last slot
    idx, param = srv._select_signature([sig(1), sig(3)], 7)
    assert (idx, param) == (1, 2)
    # zero-arg-only overloads never yield a negative index
    idx, param = srv._select_signature([sig(0)], 4)
    assert (idx, param) == (0, 0)


def test_server_arity_skipped_on_unresolved_load(tmp_path):
    """An unresolved load could redefine any intrinsic with any arity, so the editor arity
    pass must go quiet — same guard as the undefined-name pass (codex #12 round 4)."""
    ls = srv.MagmaLanguageServer()
    ls.magma_available = False
    ls.enable_lints = True
    ls.index = _index()
    ls.intrinsic_names = frozenset(ls.index.db.intrinsics)
    bad_call = "x := Factorial(1, 2);\n"  # DB Factorial takes 1 arg
    flagged = srv._compute_diagnostics(ls, bad_call, run_magma=False)
    assert any("no overload" in d.message for d in flagged)
    quiet = srv._compute_diagnostics(
        ls, 'load "missing.m";\n' + bad_call, run_magma=False, base_dir=str(tmp_path)
    )
    assert not any("no overload" in d.message for d in quiet)


def test_magma_undefined_workspace_name_downgraded_not_suppressed(monkeypatch, tmp_path):
    """Magma's authoritative undefined-identifier error must survive (as a warning) when the
    name is defined only in an unrelated workspace sibling — and stay an error when the name
    is defined nowhere (codex #12 round 1)."""
    from magma_lsp.magma.diagnostics import MagmaDiagnostic
    from magma_lsp.magma.validate import CheckResult

    def fake_check(ident):
        return CheckResult(
            diagnostics=[
                MagmaDiagnostic(
                    line=1,
                    col=6,
                    severity="error",
                    message=f"Identifier '{ident}' has not been declared or assigned",
                )
            ],
            timed_out=False,
        )

    (tmp_path / "lib.m").write_text("function Helper(x) return x; end function;\n")
    ls = srv.MagmaLanguageServer()
    ls.magma_available = True
    ls.enable_unknown_intrinsics = True
    ls.intrinsic_names = frozenset({"EllipticCurve"})
    ls.workspace_roots = [str(tmp_path)]
    ls.rescan_workspace()
    assert "Helper" in ls.workspace_symbols

    # sibling-defined but not proven reachable -> a Warning that explains why (emitted by
    # the static pass; the Magma duplicate is absorbed, never promoted to Error)
    monkeypatch.setattr(srv, "syntax_check", lambda *a, **k: fake_check("Helper"))
    diags = srv._compute_diagnostics(ls, "z := Helper(3);\n", run_magma=True)
    hits = [d for d in diags if "Helper" in d.message]
    assert hits, diags
    assert all(d.severity == t.DiagnosticSeverity.Warning for d in hits)
    assert any("load-ed or attached" in d.message for d in hits)

    # defined nowhere -> an Error survives (the static pass flags it first, with suggestions,
    # and the Magma duplicate is deduped; the point is it is NOT silently suppressed)
    monkeypatch.setattr(srv, "syntax_check", lambda *a, **k: fake_check("Nowhere"))
    diags = srv._compute_diagnostics(ls, "z := Nowhere(3);\n", run_magma=True)
    hits = [d for d in diags if "Nowhere" in d.message]
    assert hits and any(d.severity == t.DiagnosticSeverity.Error for d in hits)


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
