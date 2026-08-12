"""Near-miss "did you mean?" suggestions: token split, fuzzy ranking, cross-system aliases."""

from __future__ import annotations

from magma_lsp.db.suggest import Suggester, camel_tokens, suggest

NAMES = [
    "Factorization",
    "Factorisation",
    "EllipticCurve",
    "IsPrime",
    "NumberOfPoints",
    "Order",
    "PrimesUpTo",
    "Factor",
]


def test_camel_tokens_basic_split():
    assert camel_tokens("NumberOfPoints") == ["number", "of", "points"]


def test_camel_tokens_allcaps_run_and_digits():
    assert camel_tokens("LLLGram") == ["lll", "gram"]  # ALLCAPS run kept as one token
    assert camel_tokens("SL2Z") == ["sl", "2", "z"]  # digits are their own tokens
    assert camel_tokens("PrimesUpTo123") == ["primes", "up", "to", "123"]


def test_cross_system_guess_ranks_target_high():
    s = Suggester(NAMES)
    # Mathematica's FactorInteger should surface Magma's Factorization near the top.
    got = s.suggest("FactorInteger")
    assert "Factorization" in got[:3]
    assert got[0] == "Factorization"  # alias table pins it first


def test_wrong_case_resolves_exactly():
    s = Suggester(NAMES)
    assert s.suggest("ellipticcurve")[0] == "EllipticCurve"


def test_typo_recovered_by_edit_distance():
    s = Suggester(NAMES)
    got = s.suggest("Factorizaton")  # missing 'i'
    assert got[0] == "Factorization"


def test_garbage_yields_nothing():
    assert Suggester(NAMES).suggest("xyzzyq") == []


def test_cross_system_alias_primeq():
    assert "IsPrime" in Suggester(NAMES).suggest("primeq")


def test_length_alias_maps_to_hash_operator():
    # Operators never enter the fuzzy candidate pool, but the alias table still reaches '#'.
    assert "'#'" in Suggester(NAMES).suggest("length")


def test_abbreviation_matches_by_tokens():
    assert Suggester(NAMES).suggest("NumPoints")[0] == "NumberOfPoints"


def test_one_shot_wrapper_matches_class():
    assert suggest("ellipticcurve", NAMES) == Suggester(NAMES).suggest("ellipticcurve")
    assert suggest("xyzzyq", NAMES) == []
