"""Static "undefined intrinsic" check (design.md §3, lever 1).

Flags a call ``Foo(...)`` when ``Foo`` is neither a known intrinsic (signature DB) nor available
in the document (defined / imported / forward-declared / bound — see ``scope.analyze``) nor a
known call-position builtin. In Magma a name that is none of these genuinely errors at run time
("Identifier ... has not been declared or assigned"), so flagging it is correct — e.g.
``ChangeBaseRing`` is a package-local function, not an intrinsic, and is undefined unless imported.

This is the *fast, offline* complement to the authoritative Magma binding check
(``magma.validate.syntax_check``): it runs on every edit and without a Magma process. Measured
false-positive rate on the package corpus (the worst case, with cross-file package siblings):
~0.07%. Use the Magma pass as the source of truth when it is available.
"""

from __future__ import annotations

from collections.abc import Iterable

from .lints import Lint
from .scope import analyze

# Names that may appear in call position but are not signature-DB intrinsics. Empirically the
# package corpus surfaced none (the DB + variadic probe are comprehensive); kept as an explicit,
# extensible safety valve.
MAGMA_CALL_BUILTINS: frozenset[str] = frozenset()


def undefined_intrinsics(
    source: bytes | str,
    intrinsic_names: Iterable[str],
    *,
    builtins: frozenset[str] = MAGMA_CALL_BUILTINS,
) -> list[Lint]:
    known = (
        intrinsic_names if isinstance(intrinsic_names, (set, frozenset)) else set(intrinsic_names)
    )
    available, calls = analyze(source)

    out: list[Lint] = []
    reported_at: set[tuple[int, int]] = set()
    for cs in calls:
        if cs.name in known or cs.name in available or cs.name in builtins:
            continue
        key = (cs.line, cs.col)
        if key in reported_at:
            continue
        reported_at.add(key)
        out.append(
            Lint(
                line=cs.line,
                col=cs.col,
                end_line=cs.line,
                end_col=cs.end_col,
                message=(
                    f"'{cs.name}' is not a known intrinsic; define it locally "
                    f'or import it (import "<file>": {cs.name};)'
                ),
                severity="warning",
            )
        )
    out.sort(key=lambda d: (d.line, d.col))
    return out
