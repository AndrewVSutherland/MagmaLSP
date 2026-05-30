"""Parse real captured Magma error blocks into structured diagnostics."""

from __future__ import annotations

from magma_lsp.magma.diagnostics import parse_diagnostics

SYNTAX = (
    '\nIn file "/tmp/synerr.m", line 1, column 9:\n'
    ">> x := 2 +;\n           ^\nUser error: bad syntax\n"
)

UNDEF = (
    '\nIn file "/tmp/rterr.m", line 1, column 6:\n'
    ">> y := NoSuchThing(3);\n        ^\n"
    "User error: Identifier 'NoSuchThing' has not been declared or assigned\n"
)

BADARGS = (
    '\nIn file "/tmp/p.m", line 2, column 15:\n'
    ">> ListSignatures(EllipticCurve);\n                 ^\n"
    "Runtime error in 'ListSignatures': Bad argument types\n"
    "Argument types given: Intrinsic\n"
)

ZERODIV = (
    '\nIn file "/tmp/v.m", line 2, column 8:\n'
    ">> x := 1/0;\n          ^\nUser error: Illegal zero denominator\n"
)


def test_syntax_error():
    (d,) = parse_diagnostics(SYNTAX)
    assert (d.line, d.col) == (1, 9)
    assert d.severity == "error"
    assert d.message == "bad syntax"
    assert d.file == "/tmp/synerr.m"


def test_undefined_identifier():
    (d,) = parse_diagnostics(UNDEF)
    assert (d.line, d.col) == (1, 6)
    assert "has not been declared" in d.message


def test_bad_argument_types_folds_continuation():
    (d,) = parse_diagnostics(BADARGS)
    assert d.line == 2
    assert "Bad argument types" in d.message
    assert "Argument types given: Intrinsic" in d.message


def test_zero_division():
    (d,) = parse_diagnostics(ZERODIV)
    assert d.message == "Illegal zero denominator"


def test_multiple_blocks_in_one_run():
    diags = parse_diagnostics(UNDEF + ZERODIV)
    assert len(diags) == 2


def test_warning_is_positionless_warning():
    diags = parse_diagnostics("WARNING: Coordinates is being called on an ideal\n")
    assert len(diags) == 1
    assert diags[0].severity == "warning"
    assert diags[0].positionless
