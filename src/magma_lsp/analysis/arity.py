"""Static arity check: a call to a *known* intrinsic with an argument count that matches none
of its overloads is reported before Magma ever runs (design.md §6 "wrong arity" diagnostics).

Conservative by construction:
- only calls whose target is in the signature DB are checked (unknown names are the
  ``undefined`` pass's job), and only when the name is not re-bound in a lexical scope the
  call can actually see (a local rebinding in an unrelated function does not suppress);
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

# arities(name) -> (fixed_overload_counts, min_variadic_base_arity_or_None) or None for
# unknown names — the signature of SignatureIndex.arities.
AritiesFn = Callable[[str], tuple[set[int], int | None] | None]


def arity_problems(source: bytes | str, arities: AritiesFn) -> list[Lint]:
    _available, calls = analyze(source)
    out: list[Lint] = []
    reported: set[tuple[int, int]] = set()
    for cs in calls:
        # bound_in_scope, not the document-wide set: a local rebinding in an unrelated
        # function must not shield this call's intrinsic-arity check
        if cs.n_args < 0 or cs.bound_in_scope:
            continue
        info = arities(cs.name)
        if info is None:
            continue
        counts, variadic_min = info
        if not counts and variadic_min is None:
            continue
        if cs.n_args in counts or (variadic_min is not None and cs.n_args >= variadic_min):
            continue
        key = (cs.line, cs.col)
        if key in reported:
            continue
        reported.add(key)
        accepted = ", ".join(str(n) for n in sorted(counts))
        if variadic_min is not None:
            accepted += (", " if accepted else "") + f"{variadic_min} or more"
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
