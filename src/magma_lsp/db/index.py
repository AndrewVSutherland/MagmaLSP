"""In-memory query layer over a built ``MagmaDB``: the read side used by the LSP server.

Loads the JSON artifact once at startup and answers hover / completion / signature-help /
definition lookups. 7k-10k names load in well under a second.
"""

from __future__ import annotations

from pathlib import Path

from .model import Intrinsic, MagmaDB, Signature, SourceLocation


class SignatureIndex:
    def __init__(self, db: MagmaDB) -> None:
        self.db = db
        # Case-sensitive exact map plus a sorted name list for prefix completion.
        self._names_sorted = sorted(db.intrinsics.keys())
        self._lower = {n.lower(): n for n in self._names_sorted}

    @classmethod
    def from_path(cls, path: str | Path) -> SignatureIndex:
        return cls(MagmaDB.load(path))

    @property
    def version(self) -> str:
        return self.db.version

    def lookup(self, name: str) -> Intrinsic | None:
        return self.db.get(name)

    def signatures(self, name: str) -> list[Signature]:
        intr = self.db.get(name)
        return list(intr.signatures) if intr else []

    def complete(self, prefix: str, *, limit: int = 200) -> list[str]:
        if not prefix:
            return self._names_sorted[:limit]
        # case-insensitive prefix match, but only over identifier-like names (skip operators)
        pl = prefix.lower()
        out = [n for n in self._names_sorted if n[:1].isalpha() and n.lower().startswith(pl)]
        return out[:limit]

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

    def hover_markdown(self, name: str) -> str | None:
        intr = self.db.get(name)
        if not intr:
            return None
        return render_hover(intr)


def render_hover(intr: Intrinsic) -> str:
    """Markdown hover: each overload's signature in a code block, with its doc beneath."""
    lines: list[str] = []
    # Group identical docs so ditto overloads don't repeat the same paragraph.
    shown_doc: str | None = None
    for sig in intr.signatures:
        kind_tag = "" if sig.kind == "package" else "  _(kernel)_"
        lines.append(f"```magma\n{sig.render()}\n```{kind_tag}")
        if sig.doc and sig.doc != shown_doc:
            lines.append(sig.doc)
            shown_doc = sig.doc
    return "\n\n".join(lines)
