"""MCP front-end to the Magma LSP core, for an agent (in Claude Code or any MCP client) that is
writing and running Magma. Exposes five stdio tools — ``magma_guide``, ``magma_search``,
``magma_lookup``, ``magma_check``, ``magma_run`` — as a thin adapter over
:mod:`magma_lsp.frontend` (CLAUDE.md §10: one core, thin adapters; the shell ``magma-lsp-cli``
is the sibling adapter).

Why these four: our evals (eval/FINDINGS_3arm.md, eval/FINDINGS_trap.md) showed that *executing*
Magma and reading its real errors/output is the dominant lever for getting Magma right, and that
signature lookup is the efficiency layer that saves the agent from rediscovering conventions by
trial. ``run`` and ``check`` give the agent the execution loop; ``lookup`` gives it the DB;
``search`` gets it to the right name when it only knows the concept.

The signature DB and handbook are loaded once per process and reused across calls (DB load
~200 ms); a DB built after startup is picked up on the next call.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import frontend

mcp = FastMCP(
    "magma-lsp",
    instructions=(
        "Tools for writing correct Magma code. Recommended workflow: (1) magma_search when you "
        "only know the concept, to find the intrinsic name; (2) magma_lookup the intrinsics you "
        "plan to call — Magma's names, argument types, and RETURN CONVENTIONS are hard to guess; "
        "(3) magma_check the draft (it also executes it by default); (4) before presenting "
        "results, sanity-check the output — a clean run is NOT correctness. Never present Magma "
        "code to the user that has not at least been checked."
    ),
)

_index_cache = None
_handbook_cache = None


def _index():
    global _index_cache
    if _index_cache is None:  # retry each call until the DB exists, then keep it
        _index_cache = frontend.load_index()
    return _index_cache


def _handbook():
    global _handbook_cache
    if _handbook_cache is None:
        _handbook_cache = frontend.default_handbook()
    return _handbook_cache


@mcp.tool()
def magma_guide() -> str:
    """A one-page brief of Magma's conventions and pitfalls (verified against live Magma).

    Read this ONCE near the start of a session before writing Magma code — it covers the
    syntax essentials (`:=`, `eq`, `div`, `#`, `~`), the convention traps that produce
    silently wrong answers (Subgroups returns class representatives; Factorization returns
    <prime, exponent> pairs; Coefficients lists the constant term first; ...), and the
    idioms that make Magma code fast.
    """
    from .guide import guide

    return guide()


@mcp.tool()
def magma_search(query: str, limit: int = 10) -> str:
    """Find Magma intrinsics by keyword when you don't know the exact name.

    Searches intrinsic names and their documentation. Use this FIRST when you only know what
    you want to compute ("class group of a number field", "count points elliptic curve"), then
    magma_lookup the promising names for full signatures and docs. Guessing Magma names from
    other systems' conventions (Sage/Mathematica/PARI) usually fails — search instead.

    Args:
        query: keywords, e.g. "torsion subgroup elliptic curve" (names + doc text are searched).
        limit: max results (default 10).

    Returns:
        Ranked "Signature — one-line doc" rows, or a no-match note with advice.
    """
    return frontend.search(query, limit=min(int(limit), 25), index=_index()).text


@mcp.tool()
def magma_lookup(names: list[str]) -> str:
    """Look up Magma intrinsic signature(s) and handbook documentation by name.

    Use this BEFORE writing a call you are unsure of — to confirm the intrinsic exists, its exact
    name and capitalization, its argument and return types, optional parameters, and (crucially)
    what it actually returns. Many Magma intrinsics return tuples or representatives rather than
    the bare value you might expect (e.g. ConjugacyClasses returns <order, size, rep> triples;
    Subgroups returns conjugacy-class representatives), so reading the doc here avoids silent
    wrong answers. Operators work too (pass "#" or "'#'").

    Args:
        names: one or more intrinsic names, e.g. ["EllipticCurve", "ConjugacyClasses"].

    Returns:
        Markdown per name: overloaded signatures (documented/common ones first, capped) plus the
        handbook description. Unknown names get ranked "did you mean" suggestions — if you get
        suggestions, look one of them up instead of guessing again.
    """
    return frontend.lookup(names[:24], index=_index(), hb=_handbook()).text


@mcp.tool()
def magma_check(code: str, execute: bool = True, timeout: float = 30.0, filename: str = "") -> str:
    """Check Magma source for errors: static analysis + Magma's own syntax/binding pass, plus
    (by default) an execution pass that catches bad-argument-type and runtime errors.

    ALWAYS run this on code before presenting it to the user. The static pass reports unknown
    intrinsics (with spelling suggestions), wrong arity, and Magma pitfalls (`=` vs `:=`, `==`,
    method-call syntax, ...); the Magma pass is authoritative for syntax. Set execute=False for
    a parse/bind-only check that never runs the code (safe for code with side effects).

    Args:
        code: complete Magma source.
        execute: also run the code to catch runtime errors (default True; recommended).
        timeout: wall-clock limit in seconds for the execution pass (default 30).
        filename: the path this code lives in (or will be saved to), if any — lets
            `load "..."` directives resolve relative to it.

    Returns:
        "OK: ..." if clean, otherwise "FAIL:"/"INCONCLUSIVE:" with each diagnostic's severity,
        line/column, message, and the offending source line. Notes flag degraded modes (no DB,
        no Magma, stale DB) explicitly.
    """
    return frontend.check(
        code, execute=execute, timeout=timeout, index=_index(), filename=filename or None
    ).report


@mcp.tool()
def magma_run(code: str, timeout: float = 30.0, max_output: int = 24000) -> str:
    """Execute Magma source in a fresh sandboxed process and return its combined output.

    The process is hermetic (no startup file), wall-clock limited by `timeout`, and
    memory-limited. Error locations are reported in YOUR program's line numbers. A clean run is
    necessary but NOT sufficient for correctness: Magma will happily print a wrong answer from a
    misused intrinsic. Sanity-check the output (cross-check via an independent computation, or
    magma_lookup the intrinsic's documented return value) before trusting it.

    Args:
        code: complete Magma source. Have it `print` the value(s) you want to see.
        timeout: wall-clock limit in seconds (default 30).
        max_output: output budget in characters (default 24000); longer output is head+tail
            truncated (the tail, where errors and final results appear, is preserved).

    Returns:
        The program's stdout+stderr (Magma error blocks included), with notes for timeout,
        truncation, or a nonzero exit.
    """
    res = frontend.run(code, timeout=timeout, max_output=int(max_output))
    out = res.output
    notes: list[str] = []
    if res.timed_out:
        notes.append(f"(timed out after {timeout}s — raise timeout or reduce the computation)")
    elif res.returncode not in (0,):
        notes.append(f"(Magma exited with status {res.returncode} — see the error block above)")
    if notes:
        out = (out.rstrip("\n") + "\n" + "\n".join(notes)).lstrip("\n")
    return out if out.strip() else "(no output — add print statements for the values you need)"


def main() -> None:
    """Console-script entry point: run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
