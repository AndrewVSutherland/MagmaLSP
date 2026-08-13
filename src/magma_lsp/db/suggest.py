"""Near-miss suggestions for intrinsic names ("did you mean ...?").

LLMs (especially smaller ones) guess plausible-but-wrong intrinsic names: other CASes' names
(``FactorInteger``, ``PrimeQ``), abbreviations (``NumPoints``), wrong case (``ellipticcurve``),
and typos. Prefix completion catches none of the interesting ones, so we rank candidates by the
better of:

- camelCase-token overlap (``NumPoints`` -> {num, points} matches ``NumberOfPoints``'s
  {number, of, points} by token prefix), weighted toward the first token;
- normalized Damerau-Levenshtein similarity over the full lowercase names (typos);

plus a small curated alias table for common cross-system names (Mathematica/Sage/PARI).

``Suggester`` precomputes per-candidate data once (10k names); a query is then a few ms.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_TOKEN_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+|\d+")

# Common wrong guesses imported from other computer algebra systems -> the Magma spelling.
# Values are real DB keys (operators in their quoted form). Kept deliberately small: only
# high-confidence, unambiguous mappings that fuzzy matching cannot recover.
CROSS_SYSTEM_ALIASES: dict[str, str] = {
    "factorinteger": "Factorization",  # Mathematica
    "primeq": "IsPrime",  # Mathematica
    "len": "'#'",  # Python; # is Magma's cardinality/length operator
    "length": "'#'",
    "cardinality": "'#'",
    "size": "'#'",
    "primerange": "PrimesUpTo",  # Sage
    "znprimroot": "PrimitiveRoot",  # PARI
    "totient": "EulerPhi",
    "sizeof": "'#'",
}

MIN_SCORE = 0.55


def camel_tokens(name: str) -> list[str]:
    """Split an identifier into lowercase word tokens: NumberOfPoints -> [number, of, points]."""
    return [t.lower() for t in _TOKEN_RE.findall(name)]


def _edit_distance(a: str, b: str, *, cap: int) -> int:
    """Damerau-Levenshtein (adjacent transposition counts as 1); returns cap+1 when > cap."""
    la, lb = len(a), len(b)
    if abs(la - lb) > cap:
        return cap + 1
    prev2: list[int] | None = None
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)  # type: ignore[index]
        if min(cur) > cap:
            return cap + 1
        prev2, prev = prev, cur
    return prev[lb]


def _token_score(query_toks: list[str], cand_toks: list[str], cand_set: set[str]) -> float:
    """Coverage of query tokens by candidate tokens (exact=1, prefix-of=0.75), first token 2x."""
    if not query_toks or not cand_toks:
        return 0.0
    total_weight = 0.0
    got = 0.0
    for k, qt in enumerate(query_toks):
        weight = 2.0 if k == 0 else 1.0
        total_weight += weight
        if qt in cand_set:
            got += weight
            continue
        if len(qt) >= 3:
            for ct in cand_toks:
                # prefix relation only between substantial tokens (>= 3 chars each side)
                if len(ct) >= 3 and (ct.startswith(qt) or qt.startswith(ct)):
                    got += weight * 0.75
                    break
    coverage = got / total_weight
    # Mild penalty for candidate tokens the query never asked for (prefer close names).
    extra = max(0, len(cand_toks) - len(query_toks))
    return coverage * (0.93**extra)


class Suggester:
    """Reusable near-miss ranker over a fixed name population."""

    def __init__(self, names: Iterable[str]) -> None:
        # (name, lowercase, tokens, token_set); operators excluded — never a plausible guess.
        self._cands: list[tuple[str, str, list[str], set[str]]] = []
        self._names: set[str] = set()  # full population incl. operators, for alias filtering
        for n in names:
            self._names.add(n)
            if not n or not n[0].isalpha():
                continue
            toks = camel_tokens(n)
            self._cands.append((n, n.lower(), toks, set(toks)))

    def suggest(self, query: str, *, limit: int = 5) -> list[str]:
        """Best near-miss suggestions for ``query``, best first; [] if nothing is close."""
        ql = query.lower()
        qtoks = camel_tokens(query)
        cap = max(2, min(6, len(ql) // 2))
        scored: list[tuple[float, str]] = []
        for name, low, toks, tok_set in self._cands:
            if ql == low:
                s = 1.0
            else:
                s = _token_score(qtoks, toks, tok_set)
                if s < 1.0 and abs(len(ql) - len(low)) <= cap:
                    dist = _edit_distance(ql, low, cap=cap)
                    if dist <= cap:
                        s = max(s, 1.0 - dist / max(len(ql), len(low)))
            if s >= MIN_SCORE:
                scored.append((s, name))
        scored.sort(key=lambda t: (-t[0], len(t[1]), t[1]))
        ranked = [name for _, name in scored[:limit]]
        alias = CROSS_SYSTEM_ALIASES.get(ql)
        # only suggest an alias target that actually exists in THIS index (a package-only or
        # custom DB may lack it, and a suggestion the subsequent lookup rejects is worse
        # than none)
        if alias and alias not in ranked and alias in self._names:
            ranked = [alias, *ranked][:limit]
        return ranked


def suggest(query: str, names: Iterable[str], *, limit: int = 5) -> list[str]:
    """One-shot convenience wrapper (builds a throwaway ``Suggester``)."""
    return Suggester(names).suggest(query, limit=limit)
