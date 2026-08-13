"""Agent-facing SignatureIndex extras: resolve, arities, search, suggest, hover elision."""

from __future__ import annotations

from magma_lsp.db.index import SignatureIndex
from magma_lsp.db.model import Intrinsic, MagmaDB, Param, Signature


def _sig(name: str, n_args: int, doc: str | None = None, **kw) -> Signature:
    args = [Param(name=f"a{i}", type="RngIntElt") for i in range(n_args)]
    return Signature(name=name, args=args, returns=["RngIntElt"], doc=doc, **kw)


def _db() -> MagmaDB:
    variadic = Signature(
        name="Sprintf",
        args=[Param(name="S", type="MonStgElt"), Param(name="", type="...")],
        returns=["MonStgElt"],
        kind="kernel",
    )
    append_ref = Signature(
        name="Append",
        args=[Param(name="~S", type="SeqEnum"), Param(name="x", type="Any")],
        is_procedure=True,
        kind="kernel",
    )
    intrinsics = {
        "EllipticCurve": Intrinsic(
            "EllipticCurve",
            [_sig("EllipticCurve", 1, "The elliptic curve defined by the coefficient sequence.")],
        ),
        "Conductor": Intrinsic("Conductor", [_sig("Conductor", 1, "The conductor of the curve.")]),
        "'#'": Intrinsic("'#'", [_sig("'#'", 1, kind="kernel")]),
        "Foo": Intrinsic("Foo", [_sig("Foo", 1), _sig("Foo", 2)]),
        "Sprintf": Intrinsic("Sprintf", [variadic]),
        "Quux": Intrinsic("Quux", [_sig("Quux", i) for i in range(1, 5)]),
        "Append": Intrinsic("Append", [append_ref, _sig("Append", 2)]),
    }
    return MagmaDB(version="test", intrinsics=intrinsics)


def test_resolve_exact_operator_and_case():
    idx = SignatureIndex(_db())
    assert idx.resolve("EllipticCurve") == "EllipticCurve"
    assert idx.resolve("#") == "'#'"  # bare operator resolves to its quoted DB key
    assert idx.resolve("'#'") == "'#'"
    assert idx.resolve("ellipticcurve") == "EllipticCurve"
    assert idx.resolve("Nonsense") is None


def test_arities_overloads_and_variadic():
    idx = SignatureIndex(_db())
    assert idx.arities("Foo") == ({1, 2}, False)
    counts, variadic = idx.arities("Sprintf")
    assert variadic
    assert counts == {1}  # base arity excludes the `...` slot
    assert idx.arities("Nope") is None


def test_search_doc_keyword_surfaces_intrinsic_first():
    idx = SignatureIndex(_db())
    hits = idx.search("conductor")
    assert hits and hits[0][0] == "Conductor"
    hits = idx.search("elliptic curve")
    assert hits and hits[0][0] == "EllipticCurve"


def test_search_empty_or_stopword_only_query():
    idx = SignatureIndex(_db())
    assert idx.search("") == []
    assert idx.search("the of a") == []


def test_search_ubiquitous_term_still_matches():
    """A term appearing in every doc must rank weakly, not zero out (raw idf goes
    nonpositive and the scores were discarded — codex #12 round 12). The one-entry
    index is the deterministic repro."""
    from magma_lsp.db.model import Intrinsic, MagmaDB, Signature

    db = MagmaDB(
        version="0",
        intrinsics={"FooBar": Intrinsic("FooBar", [Signature(name="FooBar", args=[])])},
    )
    idx = SignatureIndex(db)
    hits = idx.search("foo")
    assert hits and hits[0][0] == "FooBar"


def test_suggest_garbage_and_near_miss():
    idx = SignatureIndex(_db())
    assert idx.suggest("xyzzyq") == []
    assert "EllipticCurve" in idx.suggest("ElipticCurve")


def test_hover_elides_extra_overloads():
    idx = SignatureIndex(_db())
    hover = idx.hover_markdown("Quux", max_sigs=2)
    assert hover.count("```magma") == 2
    assert "2 more overload" in hover


def test_ref_arg_names_detects_tilde_first_arg():
    idx = SignatureIndex(_db())
    assert "Append" in idx.ref_arg_names
    assert "Foo" not in idx.ref_arg_names
    assert "Sprintf" not in idx.ref_arg_names
