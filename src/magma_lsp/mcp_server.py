"""MCP front-end to the Magma LSP core, for an agent (in Claude Code or any MCP client) that is
writing and running Magma. Exposes three stdio tools — ``magma_lookup``, ``magma_check``,
``magma_run`` — as a thin adapter over :mod:`magma_lsp.frontend` (CLAUDE.md §10: one core, thin
adapters; the shell ``magma-lsp-cli`` is the sibling adapter).

Why these three: our evals (eval/FINDINGS_3arm.md, eval/FINDINGS_trap.md) showed that *executing*
Magma and reading its real errors/output is the dominant lever for getting Magma right, and that
signature lookup is the efficiency layer that saves the agent from rediscovering conventions by
trial. ``run`` and ``check`` give the agent the execution loop; ``lookup`` gives it the DB.

The signature DB and handbook are loaded once per process and reused across calls (DB load ~200 ms).
"""

from __future__ import annotations

from functools import lru_cache

from mcp.server.fastmcp import FastMCP

from . import frontend

mcp = FastMCP("magma-lsp")


@lru_cache(maxsize=1)
def _index():
    return frontend.load_index()


@lru_cache(maxsize=1)
def _handbook():
    return frontend.default_handbook()


@mcp.tool()
def magma_lookup(names: list[str]) -> str:
    """Look up Magma intrinsic signature(s) and handbook documentation by name.

    Use this BEFORE writing a call you are unsure of — to confirm the intrinsic exists, its exact
    name and capitalization, its argument and return types, optional parameters, and (crucially)
    what it actually returns. Many Magma intrinsics return tuples or representatives rather than the
    bare value you might expect (e.g. ConjugacyClasses returns <order, size, rep> triples; Subgroups
    returns conjugacy-class representatives), so reading the doc here avoids silent wrong answers.

    Args:
        names: one or more intrinsic names, e.g. ["EllipticCurve", "ConjugacyClasses"].

    Returns:
        Markdown: each name's overloaded signatures plus its handbook description. Unknown names
        report "not a known intrinsic" with spelling suggestions.
    """
    return frontend.lookup(names, index=_index(), hb=_handbook()).text


@mcp.tool()
def magma_check(code: str, execute: bool = False) -> str:
    """Check Magma source for errors WITHOUT (by default) executing it.

    Runs a static unknown-intrinsic scan plus Magma's own syntax/binding pass (the code is parsed
    and name-bound but not run). Set execute=True to additionally run it and catch
    bad-argument-type / value / runtime errors. Use this to validate code before relying on it.

    Args:
        code: complete Magma source.
        execute: if True, also run an execution pass (heavier; catches runtime errors).

    Returns:
        "OK: ..." if clean, otherwise "FAIL:" with each diagnostic's severity, line/column,
        message, and the offending source line.
    """
    return frontend.check(code, execute=execute, index=_index()).report


@mcp.tool()
def magma_run(code: str, timeout: float = 30.0) -> str:
    """Execute Magma source in a fresh sandboxed process and return its combined output.

    The process is hermetic (no startup file), wall-clock limited by `timeout`, and memory-limited.
    A clean run is necessary but NOT sufficient for correctness: Magma will happily print a wrong
    answer from a misused intrinsic. Sanity-check the output (cross-check via an independent
    computation, or magma_lookup the intrinsic's documented return value) before trusting it.

    Args:
        code: complete Magma source. Have it `print` the value(s) you want to see.
        timeout: wall-clock limit in seconds (default 30).

    Returns:
        The program's stdout+stderr (Magma error blocks included), with a note if it timed out.
    """
    res = frontend.run(code, timeout=timeout)
    out = res.output
    if res.timed_out:
        out = (out + f"\n(timed out after {timeout}s)").lstrip("\n")
    return out if out.strip() else "(no output)"


def main() -> None:
    """Console-script entry point: run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
