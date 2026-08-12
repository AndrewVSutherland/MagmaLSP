"""Type normalization, dedup keys, and package/kernel merge in db/build.py + package.py fixes."""

from __future__ import annotations

from magma_lsp.db.build import _norm_type, _sig_type_key, merge
from magma_lsp.db.model import Param, Signature
from magma_lsp.db.package import extract_file


def test_norm_type_package_shorthands():
    assert _norm_type("[RngIntElt]") == "SeqEnum[RngIntElt]"
    assert _norm_type("[RngIntElt]") == _norm_type("SeqEnum[RngIntElt]")
    assert _norm_type("{T}") == "SetEnum[T]"
    assert _norm_type(".") == "Any"
    assert _norm_type(None) == "Any"
    assert _norm_type("[]") == "SeqEnum"


def test_norm_type_whitespace_variants():
    assert _norm_type("[ RngElt ]") == "SeqEnum[RngElt]"
    assert _norm_type(" SeqEnum[ RngIntElt ] ") == "SeqEnum[RngIntElt]"


def _seqint(argtype: str, *, ref: bool = False, kind: str = "package") -> Signature:
    return Signature(
        name="Seqint",
        args=[Param(name=("~Q" if ref else "Q"), type=argtype), Param(name="b", type="RngIntElt")],
        returns=["RngIntElt"],
        kind=kind,
    )


def test_sig_type_key_equates_shorthand_and_kernel_spelling():
    pkg = _seqint("[RngIntElt]")
    ker = _seqint("SeqEnum[RngIntElt]", kind="kernel")
    assert _sig_type_key(pkg) == _sig_type_key(ker)


def test_sig_type_key_distinguishes_ref_args():
    plain = _seqint("SeqEnum[RngIntElt]")
    ref = _seqint("SeqEnum[RngIntElt]", ref=True)
    assert _sig_type_key(plain) != _sig_type_key(ref)


def test_extract_untyped_args_are_any(tmp_path):
    f = tmp_path / "untyped.m"
    f.write_text(
        "intrinsic F(R::RngIntElt, w1, w2) -> RngIntElt\n"
        "{Doc for F.}\n"
        "return R;\n"
        "end intrinsic;\n"
    )
    (sig,) = extract_file(f)
    assert [a.type for a in sig.args] == ["RngIntElt", "Any", "Any"]
    assert [a.name for a in sig.args] == ["R", "w1", "w2"]


def test_extract_ditto_docs_chain_across_names(tmp_path):
    f = tmp_path / "ditto.m"
    f.write_text(
        "intrinsic A(x::RngIntElt) -> RngIntElt\n{X}\nreturn x;\nend intrinsic;\n\n"
        'intrinsic B(y::RngIntElt) -> RngIntElt\n{"}\nreturn y;\nend intrinsic;\n\n'
        'intrinsic C(z::RngIntElt) -> RngIntElt\n{"}\nreturn z;\nend intrinsic;\n'
    )
    sigs = {s.name: s for s in extract_file(f)}
    assert sigs["A"].doc == "X"
    # The ditto {"} resolves to the previous intrinsic's doc in file order, regardless of name,
    # and chains through consecutive dittoes.
    assert sigs["B"].doc == "X"
    assert sigs["C"].doc == "X"


def test_merge_package_wins_over_kernel_duplicate():
    pkg = _seqint("[RngIntElt]")
    ker = _seqint("SeqEnum[RngIntElt]", kind="kernel")
    db = merge([pkg], [ker], "test")
    (sig,) = db.get("Seqint").signatures  # one survivor for the shared normalized key
    assert sig.kind == "package"


def test_merge_adds_distinct_ref_kernel_variant():
    pkg = _seqint("[RngIntElt]")
    ker = _seqint("SeqEnum[RngIntElt]", kind="kernel")
    kref = _seqint("SeqEnum[RngIntElt]", ref=True, kind="kernel")
    db = merge([pkg], [ker, kref], "test")
    sigs = db.get("Seqint").signatures
    assert len(sigs) == 2
    assert {s.kind for s in sigs} == {"package", "kernel"}
    assert any(s.args[0].name == "~Q" for s in sigs)
