"""Data model for the Magma signature database.

An intrinsic name maps to one or more overloaded signatures. Each signature records its
positional arguments (name + type), optional parameters (name + default expression), return
types, doc string, and — for package intrinsics — its source location (for go-to-definition).

Two provenance kinds:
- ``package``: declared in a ``.m`` file; has arg names, optional params, doc, and location.
- ``kernel``:  present only in ``ListSignatures`` output; types only, no doc/location.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Param:
    name: str
    type: str | None = None
    default: str | None = None  # optional-parameter default expression, verbatim


def _render_param(p: Param) -> str:
    if p.name and p.type:
        return f"{p.name}::{p.type}"
    return p.name or p.type or ""


@dataclass(frozen=True)
class SourceLocation:
    file: str
    line: int  # 1-based
    col: int  # 1-based


@dataclass
class Signature:
    name: str
    args: list[Param] = field(default_factory=list)
    opt_params: list[Param] = field(default_factory=list)
    returns: list[str] = field(default_factory=list)
    doc: str | None = None
    is_procedure: bool = False
    kind: str = "package"  # "package" | "kernel"
    source: SourceLocation | None = None

    def render(self) -> str:
        """Render the signature the way ``ListSignatures`` / the handbook would display it."""
        inner = ", ".join(_render_param(a) for a in self.args)
        if self.opt_params:
            opts = ", ".join(
                f"{p.name} := {p.default}" if p.default is not None else p.name
                for p in self.opt_params
            )
            inner = f"{inner} : {opts}" if inner else f": {opts}"
        head = f"{self.name}({inner})"
        if self.is_procedure or not self.returns:
            return head
        return f"{head} -> {', '.join(self.returns)}"


@dataclass
class Intrinsic:
    name: str
    signatures: list[Signature] = field(default_factory=list)

    @property
    def has_doc(self) -> bool:
        return any(s.doc for s in self.signatures)

    @property
    def first_doc(self) -> str | None:
        for s in self.signatures:
            if s.doc:
                return s.doc
        return None


@dataclass
class MagmaDB:
    version: str
    intrinsics: dict[str, Intrinsic] = field(default_factory=dict)
    built_at: str | None = None
    stats: dict[str, int] = field(default_factory=dict)

    def get(self, name: str) -> Intrinsic | None:
        return self.intrinsics.get(name)

    def names(self) -> list[str]:
        return list(self.intrinsics.keys())

    # ----- serialization -----------------------------------------------------------------
    def to_json(self) -> str:
        return json.dumps(
            {
                "version": self.version,
                "built_at": self.built_at,
                "stats": self.stats,
                "intrinsics": {
                    name: [_sig_to_dict(s) for s in intr.signatures]
                    for name, intr in self.intrinsics.items()
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def from_json(cls, text: str) -> MagmaDB:
        raw = json.loads(text)
        intrinsics: dict[str, Intrinsic] = {}
        for name, sigs in raw["intrinsics"].items():
            intrinsics[name] = Intrinsic(name=name, signatures=[_sig_from_dict(d) for d in sigs])
        return cls(
            version=raw["version"],
            intrinsics=intrinsics,
            built_at=raw.get("built_at"),
            stats=raw.get("stats", {}),
        )

    @classmethod
    def load(cls, path: str | Path) -> MagmaDB:
        return cls.from_json(Path(path).read_text(encoding="utf-8"))


def _sig_to_dict(s: Signature) -> dict:
    d = asdict(s)
    # drop empties to keep the artifact small
    return {k: v for k, v in d.items() if v not in (None, [], False)}


def _sig_from_dict(d: dict) -> Signature:
    src = d.get("source")
    return Signature(
        name=d["name"],
        args=[Param(**p) for p in d.get("args", [])],
        opt_params=[Param(**p) for p in d.get("opt_params", [])],
        returns=d.get("returns", []),
        doc=d.get("doc"),
        is_procedure=d.get("is_procedure", False),
        kind=d.get("kind", "package"),
        source=SourceLocation(**src) if src else None,
    )
