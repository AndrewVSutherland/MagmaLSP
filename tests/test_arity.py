"""Static arity check against a fake signature-DB arities function."""

from __future__ import annotations

from magma_lsp.analysis.arity import arity_problems


def fake_arities(name: str) -> tuple[set[int], int | None] | None:
    if name == "Foo":
        return {1, 2}, None
    if name == "Bar":
        return set(), 1  # variadic: 1 or more
    if name == "Mixed":
        return {1}, 3  # fixed 1-arg overload PLUS a variadic needing >= 3
    return None


def test_wrong_arity_flagged_with_accepted_counts():
    (lint,) = arity_problems("Foo(1,2,3);\n", fake_arities)
    assert "'Foo'" in lint.message
    assert "3 arguments" in lint.message
    assert "1, 2" in lint.message  # the accepted counts are spelled out
    assert (lint.line, lint.col) == (0, 0)


def test_accepted_arities_are_clean():
    assert arity_problems("Foo(1);\n", fake_arities) == []
    assert arity_problems("Foo(1,2);\n", fake_arities) == []


def test_optional_parameters_not_counted():
    # `: Opt := 3` is an optional parameter, not a third positional argument.
    assert arity_problems("Foo(1,2 : Opt := 3);\n", fake_arities) == []


def test_variadic_accepts_base_arity_or_more():
    assert arity_problems("Bar(1);\n", fake_arities) == []
    assert arity_problems("Bar(1,2,3,4);\n", fake_arities) == []


def test_variadic_still_flags_below_base_arity():
    (lint,) = arity_problems("Bar();\n", fake_arities)
    assert "0 arguments" in lint.message
    assert "1 or more" in lint.message


def test_locally_rebound_name_is_not_checked():
    # Foo is available in the document (assigned), so the DB arity data does not apply.
    src = "Foo := func< x | x >;\nFoo(1,2,3);\n"
    assert arity_problems(src, fake_arities) == []


def test_rebinding_in_disjoint_scope_does_not_suppress():
    """A local rebinding in one function must not shield an unrelated function's call to the
    intrinsic (codex #12 round 13)."""
    src = (
        "f := function(x)\n    Foo := func< y | y >;\n    return Foo(1,2,3);\nend function;\n"
        "g := function(x)\n    return Foo(1,2,3);\nend function;\n"
    )
    (lint,) = arity_problems(src, fake_arities)
    assert lint.line == 5  # g's call is flagged; f's call targets its local Foo


def test_rebinding_after_call_does_not_suppress():
    # Magma has no hoisting: the call runs before the local binding exists, so it targets
    # the intrinsic and is checked.
    src = "x := Foo(1,2,3);\nFoo := func< y | y >;\n"
    (lint,) = arity_problems(src, fake_arities)
    assert lint.line == 0


def test_unknown_names_never_flagged():
    assert arity_problems("Qux(1,2,3,4,5);\n", fake_arities) == []


def test_variadic_minimum_not_widened_by_fixed_overload():
    """A fixed 1-arg overload must not make the >=3 variadic overload accept 2 args
    (codex #12 round 15)."""
    assert arity_problems("Mixed(1);\n", fake_arities) == []
    assert arity_problems("Mixed(1,2,3);\n", fake_arities) == []
    assert arity_problems("Mixed(1,2,3,4);\n", fake_arities) == []
    (lint,) = arity_problems("Mixed(1,2);\n", fake_arities)
    assert "2 arguments" in lint.message and "3 or more" in lint.message


def test_self_assignment_rhs_still_targets_intrinsic():
    """`Foo := Foo(1,2,3);` evaluates the RHS before binding, so the call resolves to the
    intrinsic and its arity is checked (codex #12 round 16)."""
    (lint,) = arity_problems("Foo := Foo(1,2,3);\n", fake_arities)
    assert "'Foo'" in lint.message
    # after the binding, calls target the local value and are not checked
    assert arity_problems("Foo := 7;\nx := Foo(1,2,3);\n", fake_arities) == []


def test_constructor_formals_shield_body_calls():
    """`func< Foo | Foo(1,2,3) >` calls the formal, not the intrinsic (codex #12 round 16)."""
    assert arity_problems("f := func< Foo | Foo(1,2,3) >;\n", fake_arities) == []
    (lint,) = arity_problems("f := func< x | Foo(1,2,3) >;\n", fake_arities)
    assert "'Foo'" in lint.message


def test_comprehension_iterator_call_not_flagged():
    """`[Weight(1,2) : Weight in handlers]` calls the iterator value, not the intrinsic —
    the binder appears after the expression in the parse tree (codex #12 round 15)."""
    assert arity_problems("L := [Foo(1,2,3) : Foo in handlers];\n", fake_arities) == []
    # an unrelated iterator does not shield the intrinsic call
    (lint,) = arity_problems("L := [Foo(1,2,3) : x in S];\n", fake_arities)
    assert "'Foo'" in lint.message
