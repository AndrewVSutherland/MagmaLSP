"""Handbook index parsing and prose extraction (synthetic fixtures + optional real install)."""

from __future__ import annotations

import os

import pytest

from magma_lsp.handbook import HandbookIndex, _extract_name, _html_to_text


def test_extract_name_handles_alias_and_args():
    assert _extract_name("EllipticCurve(GR) ") == "EllipticCurve"
    assert _extract_name("IsUniqueFactorizationDomain :: IsUFD(R) ") == "IsUFD"
    assert _extract_name("'+'(x, y) ") is None  # operator -> no bareword name


def test_html_to_text_strips_tags_and_unescapes():
    txt = _html_to_text("Given <I>x</I>, return x<sup>2</sup> &amp; |n|.<P>Next para.")
    assert "Given x, return x^2 & |n|." in txt
    assert "Next para." in txt


def test_index_and_doc_from_synthetic_html(tmp_path):
    (tmp_path / "ind-all").write_text(
        "5<->Foo(x) <->text9.htm#42<->Foo(x) : RngIntElt -> RngIntElt\n"
        "3<->Some Section<->text9.htm#1<->Some Section\n"  # non-intrinsic level, ignored
    )
    (tmp_path / "text9.htm").write_text(
        '<H5><A NAME = "42">Foo(x) : RngIntElt -&gt; RngIntElt</A></H5>\n'
        "<BLOCKQUOTE>\nReturns the foo of <I>x</I>.\n</BLOCKQUOTE>\n"
    )
    idx = HandbookIndex.load(str(tmp_path))
    assert idx.entries == {"Foo": [("text9.htm", "42")]}
    assert idx.doc_markdown("Foo") == "Returns the foo of x."
    assert idx.doc_markdown("Missing") is None


@pytest.mark.skipif(
    not os.path.isdir("/opt/magma/doc/html"), reason="Magma handbook not present"
)
def test_real_handbook_lookup():
    idx = HandbookIndex.load("/opt/magma/doc/html")
    assert len(idx.entries) > 3000
    doc = idx.doc_markdown("Factorization")
    assert doc and "factorization" in doc.lower()
