"""Static analysis: unused-variable lint and document symbols."""

from __future__ import annotations

from magma_lsp.analysis.lints import unused_variables
from magma_lsp.analysis.symbols import document_symbols

INTRINSIC = """intrinsic Foo(x::RngIntElt) -> RngIntElt
{ Compute something. }
    local a, b;
    a := x + 1;
    unusedvar := 99;
    for i in [1..a] do
        s := i;
    end for;
    return a;
end intrinsic;"""


def test_unused_flags_dead_locals():
    msgs = {d.message for d in unused_variables(INTRINSIC)}
    assert msgs == {
        "'b' is assigned but never used",
        "'unusedvar' is assigned but never used",
        "'s' is assigned but never used",
    }


def test_unused_ignores_discard_and_indexed_use():
    src = """f := function(n)
    a, _ := Quotrem(n, 3);
    M := ZeroMatrix(Integers(), 2, 2);
    M[1] := n;
    return a + M[1];
end function;
print f(10);"""
    assert unused_variables(src) == []  # _, M, a, f all fine


def test_unused_does_not_flag_callable_bindings():
    src = "helper := function(x) return x; end function;\n"
    assert unused_variables(src) == []


def test_unused_reports_position_of_definition():
    (d,) = unused_variables("p := procedure() return; end procedure;\nq := 5;\n")
    # q on line 2 (0-based line 1) is the only dead store; p is a callable binding.
    assert d.message == "'q' is assigned but never used"
    assert d.line == 1


def test_document_symbols_intrinsic_and_function():
    src = INTRINSIC + "\ng := function(z) return z; end function;\n"
    syms = document_symbols(src)
    kinds = {(s.name, s.kind) for s in syms}
    assert ("Foo", "intrinsic") in kinds
    assert ("g", "function") in kinds
