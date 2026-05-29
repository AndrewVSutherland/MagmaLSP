"""Lock in the tree-sitter package extractor against the grammar corpus."""

from __future__ import annotations

from pathlib import Path

import pytest

from magma_lsp.db.package import extract_file

CORPUS = Path(__file__).parent / "fixtures" / "grammar_corpus.m"


@pytest.fixture(scope="module")
def by_name():
    sigs = extract_file(CORPUS)
    out: dict[str, list] = {}
    for s in sigs:
        out.setdefault(s.name, []).append(s)
    return out


def test_simple(by_name):
    (s,) = by_name["Simple"]
    assert [(a.name, a.type) for a in s.args] == [("x", "RngIntElt")]
    assert s.returns == ["RngIntElt"]
    assert s.doc == "The simplest case."
    assert not s.is_procedure
    assert s.source is not None and s.source.line == 4


def test_multiline_header_optionals_and_multi_return(by_name):
    (s,) = by_name["WithOptionals"]
    assert [a.name for a in s.args] == ["x", "S"]
    assert s.args[1].type == "SeqEnum[RngIntElt]"
    assert {p.name: p.default for p in s.opt_params} == {"Al": '"Default"', "Bound": "0"}
    assert s.returns == ["RngIntElt", "BoolElt"]


def test_expression_default(by_name):
    (s,) = by_name["ExprDefault"]
    assert s.opt_params[0].name == "C3"
    assert s.opt_params[0].default == "Curve(model3)"


def test_operator_intrinsic(by_name):
    (s,) = by_name["'+'"]
    assert s.name == "'+'"
    assert s.returns == ["AlgMatLie"]


def test_procedure_and_ref_arg(by_name):
    (s,) = by_name["AssignNames"]
    assert s.is_procedure
    assert s.returns == []
    assert s.args[0].name == "~C"  # reference arg keeps the tilde


def test_wildcard_and_any(by_name):
    (s,) = by_name["Wildcard"]
    assert s.args[0].type == "."
    assert s.args[1].type == "Any"


def test_empty_doc(by_name):
    (s,) = by_name["Empty"]
    assert s.doc is None


def test_ditto_doc_resolves_to_previous_overload(by_name):
    first, second = by_name["Dittoed"]
    assert first.doc == "This doc is shared by the next overload."
    assert second.doc == first.doc  # {"} resolved


def test_render_roundtrips_optionals(by_name):
    (s,) = by_name["WithOptionals"]
    assert s.render() == (
        "WithOptionals(x::RngIntElt, S::SeqEnum[RngIntElt] : "
        'Al := "Default", Bound := 0) -> RngIntElt, BoolElt'
    )
