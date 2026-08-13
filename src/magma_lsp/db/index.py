"""In-memory query layer over a built ``MagmaDB``: the read side used by the LSP server.

Loads the JSON artifact once at startup and answers hover / completion / signature-help /
definition lookups, plus the agent-facing extras: name resolution (operators, case), near-miss
suggestions, and keyword search over names + doc strings. 7k-10k names load in ~200 ms.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from .model import Intrinsic, MagmaDB, Signature, SourceLocation
from .suggest import Suggester, camel_tokens

# Words too common in doc strings to be informative for keyword search.
_STOPWORD_TEXT = (
    "a an and are as at be by for from given has have if in into is it its of on or over "
    "return returns that the this to true false whether which with"
)
_STOPWORDS = frozenset(_STOPWORD_TEXT.split())

_WORD_RE = re.compile(r"[a-z0-9]+")


class SignatureIndex:
    def __init__(self, db: MagmaDB) -> None:
        self.db = db
        # Case-sensitive exact map plus a sorted name list for prefix completion.
        self._names_sorted = sorted(db.intrinsics.keys())
        self._lower: dict[str, str] = {}
        for n in self._names_sorted:  # alphabetically-first name wins a case-insensitive tie
            self._lower.setdefault(n.lower(), n)
        self._suggester: Suggester | None = None
        self._doc_index: list[tuple[str, dict[str, int]]] | None = None
        self._doc_freq: dict[str, int] | None = None
        self._ref_arg_names: frozenset[str] | None = None

    @classmethod
    def from_path(cls, path: str | Path) -> SignatureIndex:
        return cls(MagmaDB.load(path))

    @property
    def version(self) -> str:
        return self.db.version

    def lookup(self, name: str) -> Intrinsic | None:
        return self.db.get(name)

    def resolve(self, name: str) -> str | None:
        """Resolve a user-supplied name to a DB key: exact, operator (``#`` -> ``'#'``),
        or unique-case-insensitive. None if nothing matches."""
        if name in self.db.intrinsics:
            return name
        stripped = name.strip()
        if stripped != name and stripped in self.db.intrinsics:
            return stripped
        # operator spelled bare (``#``, ``+``, ``mod``) or already quoted (``'#'``)
        unquoted = stripped.strip("'")
        for cand in (f"'{stripped}'", f"'{unquoted}'" if unquoted else ""):
            if cand and cand in self.db.intrinsics:
                return cand
        return self._lower.get(stripped.lower())

    def signatures(self, name: str) -> list[Signature]:
        intr = self.db.get(name)
        return list(intr.signatures) if intr else []

    def arities(self, name: str) -> tuple[set[int], bool] | None:
        """Positional-argument counts accepted by ``name``'s overloads, plus a flag for
        variadic overloads (which accept their base arity or more). None if unknown name."""
        intr = self.db.get(name)
        if intr is None:
            return None
        counts: set[int] = set()
        variadic = False
        for s in intr.signatures:
            n_args = len(s.args)
            if any((p.type or "").strip() == "..." or p.name == "..." for p in s.args):
                variadic = True
                counts.add(max(0, n_args - 1))
            else:
                counts.add(n_args)
        return counts, variadic

    @property
    def ref_arg_names(self) -> frozenset[str]:
        """Names with a ``~``-first-argument (in-place procedure) overload, for the
        discarded-result pitfall lint."""
        if self._ref_arg_names is None:
            out: set[str] = set()
            for name, intr in self.db.intrinsics.items():
                for s in intr.signatures:
                    if s.args and s.args[0].name.startswith("~"):
                        out.add(name)
                        break
            self._ref_arg_names = frozenset(out)
        return self._ref_arg_names

    def complete(self, prefix: str, *, limit: int = 200) -> list[str]:
        if not prefix:
            # identifier-like names only: quoted operators are useless as completions
            return [n for n in self._names_sorted if n[:1].isalpha()][:limit]
        # case-insensitive prefix match, but only over identifier-like names (skip operators)
        pl = prefix.lower()
        out = [n for n in self._names_sorted if n[:1].isalpha() and n.lower().startswith(pl)]
        return out[:limit]

    def suggest(self, name: str, *, limit: int = 5) -> list[str]:
        """Near-miss suggestions for an unknown name (fuzzy + cross-system aliases)."""
        if self._suggester is None:
            self._suggester = Suggester(self._names_sorted)
        return self._suggester.suggest(name, limit=limit)

    # ----- keyword search ------------------------------------------------------------------
    def _ensure_doc_index(self) -> None:
        if self._doc_index is not None:
            return
        index: list[tuple[str, dict[str, int]]] = []
        dfreq: dict[str, int] = {}
        for name in self._names_sorted:
            intr = self.db.intrinsics[name]
            terms: dict[str, int] = {}
            for tok in camel_tokens(name):
                if tok not in _STOPWORDS:
                    # name tokens count heavily: they are the strongest relevance signal
                    terms[tok] = terms.get(tok, 0) + 5
            seen_docs: set[str] = set()
            for sig in intr.signatures:
                if sig.doc and sig.doc not in seen_docs:
                    seen_docs.add(sig.doc)
                    for tok in _WORD_RE.findall(sig.doc.lower()):
                        if tok not in _STOPWORDS and len(tok) > 1:
                            terms[tok] = terms.get(tok, 0) + 1
            for tok in terms:
                dfreq[tok] = dfreq.get(tok, 0) + 1
            index.append((name, terms))
        self._doc_index = index
        self._doc_freq = dfreq

    def search(self, query: str, *, limit: int = 10) -> list[tuple[str, float]]:
        """Rank intrinsics by keyword relevance of ``query`` against names + doc strings.

        Scoring is tf-idf-ish with a strong bonus for covering more distinct query terms, so
        multi-word queries behave like a soft AND. Returns (name, score), best first.
        """
        self._ensure_doc_index()
        assert self._doc_index is not None and self._doc_freq is not None
        n_docs = max(1, len(self._doc_index))
        # Tokenize the query with the SAME CamelCase splitting the index applies to names:
        # a query of "NumberOfPoints" must match the tokens [number, of, points], not the
        # single unsplit term "numberofpoints" (which matches nothing).
        qset: set[str] = set()
        for word in re.findall(r"[A-Za-z0-9]+", query):
            qset.add(word.lower())
            qset.update(camel_tokens(word))
        qterms = [t for t in qset if t not in _STOPWORDS and len(t) > 1]
        if not qterms:
            return []
        idf = {t: math.log(n_docs / (1 + self._doc_freq.get(t, 0))) for t in qterms}
        scored: list[tuple[float, str]] = []
        for name, terms in self._doc_index:
            score = 0.0
            covered = 0
            for t in qterms:
                tf = terms.get(t, 0)
                if tf:
                    covered += 1
                    score += (1.0 + math.log(tf)) * idf[t]
                else:  # prefix credit: query "factor" should hit "factorization"
                    for dt, dtf in terms.items():
                        if len(t) >= 4 and dt.startswith(t):
                            covered += 1
                            score += 0.6 * (1.0 + math.log(dtf)) * idf[t]
                            break
            if score <= 0:
                continue
            score *= covered / len(qterms)  # soft-AND: prefer covering all terms
            if covered == len(qterms):
                score *= 1.5
            scored.append((score, name))
        scored.sort(key=lambda t: (-t[0], len(t[1]), t[1]))
        return [(name, s) for s, name in scored[:limit]]

    def search_symbols(self, query: str, *, limit: int = 200) -> list[tuple[str, SourceLocation]]:
        """Package intrinsics whose name contains `query` (case-insensitive), with a location."""
        q = query.lower()
        out: list[tuple[str, SourceLocation]] = []
        for name in self._names_sorted:
            if q and q not in name.lower():
                continue
            loc = self.definition(name)
            if loc is not None:
                out.append((name, loc))
                if len(out) >= limit:
                    break
        return out

    def definition(self, name: str) -> SourceLocation | None:
        intr = self.db.get(name)
        if not intr:
            return None
        for s in intr.signatures:
            if s.source is not None:
                return s.source
        return None

    def hover_markdown(self, name: str, *, max_sigs: int = 20) -> str | None:
        intr = self.db.get(name)
        if not intr:
            return None
        return render_hover(intr, max_sigs=max_sigs)


def _doc_order(sigs: list[Signature]) -> list[Signature]:
    """Documented package signatures first (they carry the conventions), then the rest."""
    return sorted(
        sigs, key=lambda s: (0 if s.doc else 1, 0 if s.kind == "package" else 1)
    )


def render_hover(intr: Intrinsic, *, max_sigs: int = 20) -> str:
    """Markdown hover: each overload's signature in a code block, with its doc beneath.

    Overloads beyond ``max_sigs`` are elided with a count (operators like ``'*'`` have 500+;
    dumping them all helps nobody). Documented signatures are shown first.
    """
    lines: list[str] = []
    sigs = _doc_order(intr.signatures)
    shown = sigs[:max_sigs]
    # Group identical docs so ditto overloads don't repeat the same paragraph.
    shown_doc: str | None = None
    for sig in shown:
        kind_tag = "" if sig.kind == "package" else "  _(kernel)_"
        lines.append(f"```magma\n{sig.render()}\n```{kind_tag}")
        if sig.doc and sig.doc != shown_doc:
            lines.append(sig.doc)
            shown_doc = sig.doc
    hidden = len(sigs) - len(shown)
    if hidden > 0:
        lines.append(f"_… and {hidden} more overload{'s' if hidden != 1 else ''} not shown._")
    return "\n\n".join(lines)
