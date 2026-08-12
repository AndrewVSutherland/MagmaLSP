"""Shared agent-facing front-end logic (CLAUDE.md §10: one core, thin adapters).

Both ``magma_lsp.cli`` (shell front-end) and ``magma_lsp.mcp_server`` (MCP front-end, for an
agent driving Magma) are thin wrappers over the three operations here, so the two never diverge:

- :func:`lookup` — intrinsic signature(s) + handbook description (the hover content).
- :func:`check`  — diagnostics: static unknown-intrinsic + Magma syntax/binding (+ optional run).
- :func:`run`    — execute the program in a sandboxed Magma and return its output.

Each returns plain text / markdown suitable for a terminal or an MCP tool result.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .analysis.undefined import undefined_intrinsics
from .db.index import SignatureIndex
from .db.store import newest_cached_db
from .handbook import HandbookIndex
from .magma.diagnostics import MagmaDiagnostic
from .magma.runner import find_magma, run_source
from .magma.validate import execution_check, syntax_check

# Memory ceiling for the execution path. The trusted-colleague sandbox is timeout + memory limit
# (verified reliable, CLAUDE.md §3); OS-level isolation is deferred until the audience widens.
RUN_MEMORY_BYTES = 2 * 1024 * 1024 * 1024

_RUN_PREAMBLE = (
    "SetColumns(0);\nSetAutoColumns(false);\nSetEchoInput(false);\nSetIgnorePrompt(true);\n"
)


def load_index() -> SignatureIndex | None:
    """Load the newest cached signature DB, or None if it has not been built."""
    p = newest_cached_db()
    return SignatureIndex.from_path(p) if p else None


def default_handbook() -> HandbookIndex | None:
    """Locate ``<magma install>/doc/html`` and load the handbook index, or None."""
    resolved = find_magma(None)
    base = os.path.dirname(os.path.realpath(resolved)) if resolved else "/opt/magma"
    hb = os.path.join(base, "doc", "html")
    return HandbookIndex.load(hb) if os.path.isdir(hb) else None


@dataclass(frozen=True)
class LookupResult:
    text: str
    all_found: bool


def lookup(
    names: list[str],
    *,
    handbook: bool = True,
    index: SignatureIndex | None = None,
    hb: HandbookIndex | None = None,
) -> LookupResult:
    """Render signature(s) + handbook doc for each name (the hover content).

    Pass a preloaded ``index`` / ``hb`` to avoid reloading them per call (the MCP server does).
    """
    idx = index if index is not None else load_index()
    if idx is None:
        return LookupResult("error: no signature DB; run magma-lsp-build-db", False)
    hbk = hb if hb is not None else (default_handbook() if handbook else None)
    blocks: list[str] = []
    all_found = True
    for name in names:
        if idx.lookup(name) is None:
            sugg = idx.complete(name, limit=8)
            line = f"# {name}: not a known intrinsic"
            blocks.append(line + (f" (did you mean: {', '.join(sugg)}?)" if sugg else ""))
            all_found = False
            continue
        block = [f"# {name}", idx.hover_markdown(name)]
        if hbk is not None:
            doc = hbk.doc_markdown(name)
            if doc:
                block.append(f"\n{doc}")
        blocks.append("\n".join(block))
    return LookupResult("\n\n".join(blocks), all_found)


def fmt_diags(diags: list[MagmaDiagnostic], source: str) -> list[str]:
    lines = source.splitlines()
    out: list[str] = []
    for d in diags:
        loc = "" if getattr(d, "positionless", False) else f" (line {d.line}, col {d.col})"
        src = lines[d.line - 1].strip() if 0 < d.line <= len(lines) else ""
        out.append(f"  [{d.severity}]{loc}: {d.message}" + (f"\n      >> {src}" if src else ""))
    return out


@dataclass(frozen=True)
class CheckOutcome:
    ok: bool
    report: str


def check(
    source: str, *, execute: bool = False, index: SignatureIndex | None = None
) -> CheckOutcome:
    """Static unknown-intrinsic + Magma syntax/binding diagnostics; optional execution pass."""
    idx = index if index is not None else load_index()
    problems: list[str] = []
    if idx is not None:
        names = frozenset(idx.db.intrinsics)
        for lint in undefined_intrinsics(source, names):
            problems.append(f"  [error] (line {lint.line + 1}, col {lint.col + 1}): {lint.message}")

    syn = syntax_check(source)
    problems += fmt_diags(syn.diagnostics, source)
    if not syn.diagnostics and not problems and execute:
        ex = execution_check(source)
        problems += fmt_diags(ex.diagnostics, source)

    if problems:
        return CheckOutcome(False, "FAIL: issues found:\n" + "\n".join(problems))
    return CheckOutcome(True, "OK: no static or Magma errors detected.")


@dataclass(frozen=True)
class RunOutcome:
    output: str
    returncode: int
    timed_out: bool


def run(source: str, *, timeout: float = 30.0, memory_bytes: int = RUN_MEMORY_BYTES) -> RunOutcome:
    """Execute ``source`` in a fresh sandboxed Magma (timeout + in-process memory limit)."""
    preamble = _RUN_PREAMBLE + f"SetMemoryLimit({memory_bytes});\n"
    res = run_source(source, timeout=timeout, preamble=preamble)
    return RunOutcome(res.stdout, res.returncode, res.timed_out)
