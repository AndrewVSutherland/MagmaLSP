"""Parsing of ``ListSignatures`` output lines and ``ListCategories`` names."""

from __future__ import annotations

from magma_lsp.db.listsig import parse_categories, parse_enum_output, parse_listsig_line


def test_simple_line():
    s = parse_listsig_line("'#'(S::SetEnum) -> RngIntElt")
    assert s.name == "'#'"
    assert [(a.name, a.type) for a in s.args] == [("S", "SetEnum")]
    assert s.returns == ["RngIntElt"]
    assert not s.is_procedure
    assert s.kind == "kernel"


def test_ref_arg_procedure():
    s = parse_listsig_line("'*:='(~D::LieRepDec, c::RngIntElt)")
    assert s.is_procedure
    assert s.returns == []
    assert s.args[0].name == "~D"


def test_parametrized_type_not_split_on_inner_comma():
    s = parse_listsig_line("Foo(M::Mtrx[RngInt], v::SeqEnum) -> Mtrx[RngInt]")
    assert [a.type for a in s.args] == ["Mtrx[RngInt]", "SeqEnum"]
    assert s.returns == ["Mtrx[RngInt]"]


def test_multiple_returns():
    s = parse_listsig_line("Bar(A::ModAbVar, B::ModAbVar) -> ModAbVar, List, List")
    assert s.returns == ["ModAbVar", "List", "List"]


def test_unknown_angle_type():
    s = parse_listsig_line("LeftDiv(x::<unknown>, ~y::<unknown>) -> RngElt")
    assert s.args[0].type == "<unknown>"
    assert s.args[1].name == "~y"


def test_non_signature_lines_ignored():
    out = """Signatures relevant to RngIntElt:

    Abs(x::RngIntElt) -> RngIntElt
    Abs(x::RngIntElt) -> RngIntElt
'#'(S::SetEnum) -> RngIntElt
"""
    sigs = parse_enum_output(out)
    rendered = sorted({s.name for s in sigs})
    assert rendered == ["'#'", "Abs"]  # header/blank dropped, duplicate Abs deduped


def test_parse_categories():
    out = "AInfinity\nAff\n  RngInt  \nSignatures relevant to X:\n\n123notacat\n"
    assert parse_categories(out) == ["AInfinity", "Aff", "RngInt"]
