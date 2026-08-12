"""Probe-output parsing of optional-parameter blocks and per-group doc paragraphs."""

from __future__ import annotations

from magma_lsp.db.model import Param
from magma_lsp.db.probe import parse_probe_output

# Realistic `name;` output: the first signature carries a bracketed optional-parameter block,
# and each signature group is followed by its own doc paragraph.
SAMPLE = """@@@Points
Intrinsic 'Points'

Signatures:

(C::CrvCon) -> SetIndx
[
Bound: RngIntElt
]

The set of rational points of the conic, up to the bound.

(E::CrvEll[FldFin]) -> SetIndx

The set of rational points of the elliptic curve.
"""


def test_two_signatures_parsed():
    res = parse_probe_output(SAMPLE)
    assert set(res) == {"Points"}
    assert len(res["Points"]) == 2
    assert all(s.kind == "kernel" for s in res["Points"])


def test_first_signature_gets_opt_params_and_its_own_doc():
    first = parse_probe_output(SAMPLE)["Points"][0]
    assert [(a.name, a.type) for a in first.args] == [("C", "CrvCon")]
    assert first.opt_params == [Param(name="Bound", type="RngIntElt")]
    # The doc is the prose paragraph, never the bracket-block content.
    assert first.doc == "The set of rational points of the conic, up to the bound."
    assert first.render() == "Points(C::CrvCon : Bound) -> SetIndx"


def test_second_signature_has_own_doc_and_no_opt_params():
    second = parse_probe_output(SAMPLE)["Points"][1]
    assert [(a.name, a.type) for a in second.args] == [("E", "CrvEll[FldFin]")]
    assert second.opt_params == []
    assert second.doc == "The set of rational points of the elliptic curve."
