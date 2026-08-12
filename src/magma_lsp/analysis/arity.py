"""Static arity check: a call to a *known* intrinsic with an argument count that matches none
of its overloads is reported before Magma ever runs (design.md §6 "wrong arity" diagnostics).

Conservative by construction:
- only calls whose target is in the signature DB are checked (unknown names are the
  ``undefined`` pass's job), and only when the name is not locally re-bound in the document;
- variadic overloads (``...``) accept their base arity or more;
- calls whose argument count could not be determined are skipped.

The DB's arity data is trustworthy after the extraction fixes (untyped args are ``Any``, the
``~``-ref/kernel merge is normalized); measured false-positive rate on the package corpus and
handbook programs is ~0 (see validation/).
"""

from __future__ import annotations

from collections.abc import Callable

from .lints import Lint
from .scope import analyze

# arities(name) -> (accepted_counts, has_variadic_overload) or None for unknown names —
# the signature of SignatureIndex.arities.
AritiesFn = Callable[[str], tuple[set[int], bool] | None]


def arity_problems(source: bytes | str, arities: AritiesFn) -> list[Lint]:
    available, calls = analyze(source)
    out: list[Lint] = []
    reported: set[tuple[int, int]] = set()
    for cs in calls:
        if cs.n_args < 0 or cs.name in available:
            continue
        info = arities(cs.name)
        if info is None:
            continue
        counts, variadic = info
        if not counts:
            continue
        if cs.n_args in counts or (variadic and cs.n_args >= min(counts)):
            continue
        key = (cs.line, cs.col)
        if key in reported:
            continue
        reported.add(key)
        accepted = ", ".join(str(n) for n in sorted(counts)) + (" or more" if variadic else "")
        out.append(
            Lint(
                line=cs.line,
                col=cs.col,
                end_line=cs.line,
                end_col=cs.end_col,
                message=(
                    f"no overload of '{cs.name}' takes {cs.n_args} argument"
                    f"{'s' if cs.n_args != 1 else ''} (accepted: {accepted}) — "
                    f"check the signature with magma_lookup"
                ),
                severity="warning",
            )
        )
    out.sort(key=lambda d: (d.line, d.col))
    return out
