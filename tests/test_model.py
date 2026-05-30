"""DB model serialization round-trip and the query index."""

from __future__ import annotations

from magma_lsp.db.index import SignatureIndex
from magma_lsp.db.model import Intrinsic, MagmaDB, Param, Signature, SourceLocation


def _sample_db() -> MagmaDB:
    sig = Signature(
        name="Factorial",
        args=[Param(name="n", type="RngIntElt")],
        returns=["RngIntElt"],
        doc="The factorial of n.",
        source=SourceLocation(file="/x/y.m", line=10, col=1),
    )
    kernel = Signature(
        name="Abs", args=[Param(name="x", type="FldReElt")], returns=["FldReElt"], kind="kernel"
    )
    return MagmaDB(
        version="2.29-7",
        intrinsics={
            "Factorial": Intrinsic("Factorial", [sig]),
            "Abs": Intrinsic("Abs", [kernel]),
        },
    )


def test_json_roundtrip():
    db = _sample_db()
    db2 = MagmaDB.from_json(db.to_json())
    assert db2.version == "2.29-7"
    f = db2.get("Factorial")
    assert f.signatures[0].render() == "Factorial(n::RngIntElt) -> RngIntElt"
    assert f.signatures[0].source.line == 10
    assert db2.get("Abs").signatures[0].kind == "kernel"


def test_index_queries():
    idx = SignatureIndex(_sample_db())
    assert idx.complete("Fac") == ["Factorial"]
    assert idx.definition("Factorial").line == 10
    assert idx.definition("Abs") is None  # kernel has no source
    hover = idx.hover_markdown("Factorial")
    assert "```magma" in hover and "The factorial of n." in hover
    assert idx.lookup("Nope") is None
