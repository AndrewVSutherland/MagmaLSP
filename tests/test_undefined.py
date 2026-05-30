"""Static undefined-intrinsic detection with scope modeling (imports/forwards/defs/bindings)."""

from __future__ import annotations

from magma_lsp.analysis.scope import analyze
from magma_lsp.analysis.undefined import undefined_intrinsics

# A small stand-in intrinsic set (the real check is fed the signature DB's names).
INTRINSICS = frozenset({"EllipticCurve", "Factorial", "Matrix", "GF"})


def names(src: str):
    return {lint.message.split("'")[1] for lint in undefined_intrinsics(src, INTRINSICS)}


def test_real_intrinsics_are_clean():
    assert names("E := EllipticCurve([0, 1]);\np := Factorial(5);\n") == set()


def test_typo_is_flagged():
    assert names("E := EllipitcCurve([0, 1]);\n") == {"EllipitcCurve"}


def test_unimported_package_local_is_flagged():
    # ChangeBaseRing is a package-local function, not an intrinsic.
    assert names("r := ChangeBaseRing(M, K);\n") == {"ChangeBaseRing"}


def test_import_makes_name_known():
    src = (
        'import "matrix.m": ChangeBaseRing;\n'
        "r := ChangeBaseRing(Matrix(2, 2, [1, 2, 3, 4]), GF(5));\n"
    )
    assert names(src) == set()


def test_local_and_forward_definitions_are_known():
    assert names("h := function(n) return n + 1; end function;\nx := h(3);\n") == set()
    assert names("forward F;\nx := F(2);\nF := function(n) return n; end function;\n") == set()


def test_named_function_definition_is_known():
    assert names("function g(y) return y; end function;\nz := g(1);\n") == set()


def test_scope_collects_imports_and_defs():
    src = (
        'import "f.m": A, B;\n'
        "forward C;\n"
        "h := function(x) return x; end function;\n"
        "q := A(B(C(h(1))));\n"
    )
    avail, calls = analyze(src)
    assert {"A", "B", "C", "h"} <= avail
    assert {c.name for c in calls} == {"A", "B", "C", "h"}


def test_position_points_at_call_target():
    (lint,) = undefined_intrinsics("  z := Bogus(1);\n", INTRINSICS)
    assert lint.line == 0
    assert lint.col == 7  # 0-based column of 'Bogus'
