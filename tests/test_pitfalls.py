"""Pitfall lints: the Magma mistakes LLMs actually make (=, ==, **, method calls, True, //)."""

from __future__ import annotations

from magma_lsp.analysis.pitfalls import pitfall_lints


def test_single_equals_statement_suggests_assignment():
    lints = pitfall_lints("x = 5;\n")
    assert len(lints) == 1
    assert ":=" in lints[0].message


def test_eq_comparison_is_not_flagged():
    assert pitfall_lints("if x eq 5 then y := 1; end if;\n") == []


def test_double_equals_suggests_eq():
    (lint,) = pitfall_lints("if x == 5 then y := 1; end if;\n")
    assert "eq" in lint.message


def test_double_star_suggests_caret():
    (lint,) = pitfall_lints("z := 2 ** 3;\n")
    assert "^" in lint.message


def test_method_call_syntax_flagged():
    (lint,) = pitfall_lints("L.append(3);\n")
    assert "method-call" in lint.message
    assert "Append(L, " in lint.message


def test_python_true_literal_flagged_when_undefined():
    (lint,) = pitfall_lints("x := True;\n")
    assert "`true`" in lint.message


def test_lowercase_true_never_flagged():
    assert pitfall_lints("x := true;\nflag := false;\n") == []


def test_true_bound_in_document_is_skipped():
    # A file that assigns True itself has made the name available: no lint.
    assert pitfall_lints("True := 1;\nx := True;\n") == []


def test_discarded_result_of_ref_arg_intrinsic():
    (lint,) = pitfall_lints("Append(L, 3);\n", ref_arg_intrinsics=frozenset({"Append"}))
    assert "discarded" in lint.message
    assert "Append(~L, ...)" in lint.message


def test_ref_arg_call_form_not_flagged():
    assert pitfall_lints("Append(~L, 3);\n", ref_arg_intrinsics=frozenset({"Append"})) == []


def test_used_result_not_flagged():
    # Not a bare statement: the returned value is kept, so nothing is discarded.
    assert pitfall_lints("y := Append(L, 3);\n", ref_arg_intrinsics=frozenset({"Append"})) == []


def test_shadowing_intrinsic_that_file_calls():
    src = "Order := 5;\nprint Order(G);\n"
    (lint,) = pitfall_lints(src, intrinsic_names=frozenset({"Order"}))
    assert "shadows" in lint.message and "Order" in lint.message


def test_shadowing_without_a_call_is_allowed():
    assert pitfall_lints("Order := 5;\n", intrinsic_names=frozenset({"Order"})) == []


def test_double_slash_comment_suggests_div():
    (lint,) = pitfall_lints("q := a // b;\nprint q;\n")
    assert "div" in lint.message
    assert "comment" in lint.message


def test_clean_idiomatic_code_has_no_lints():
    src = """E := EllipticCurve([0, -1, 1, 0, 0]);
for p in PrimesUpTo(20) do
    if p ge 5 then
        printf "%o: %o\\n", p, p - TraceOfFrobenius(E, p);
    end if;
end for;
"""
    lints = pitfall_lints(
        src,
        intrinsic_names=frozenset({"EllipticCurve", "PrimesUpTo", "TraceOfFrobenius"}),
        ref_arg_intrinsics=frozenset({"Append"}),
    )
    assert lints == []
