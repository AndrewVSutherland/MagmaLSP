"""Magma-backed validation of user source (CLAUDE.md §5).

Two passes, both in a fresh sandboxed process:

- **syntax/binding pass** (default, safe): wrap the user code in a never-called function so it is
  *parsed and name-bound but never executed*. Catches syntax errors (one, fatal) and
  ``Identifier ... has not been declared`` (non-fatal, collects all). No I/O, no computation.
- **execution pass** (opt-in, heavier): run the code for real under a memory limit and
  ``SetQuitOnError`` to catch bad-argument-type / value / runtime errors. Aborts on the first one.

Diagnostics come back in the user document's 1-based coordinates (the wrapper/preamble line
offset is removed).
"""

from __future__ import annotations

from dataclasses import dataclass

from .diagnostics import MagmaDiagnostic, parse_diagnostics
from .runner import run_source

# Lines prepended before the user's first line in each pass. Keep in sync with the f-strings below.
_SYNTAX_PREAMBLE = (
    "SetColumns(0);\n"
    "SetAutoColumns(false);\n"
    "SetEchoInput(false);\n"
    "SetIgnorePrompt(true);\n"
    "__magma_lsp_chk := function()\n"
)
_SYNTAX_OFFSET = _SYNTAX_PREAMBLE.count("\n")  # user line 1 == magma line OFFSET+1


@dataclass(frozen=True)
class CheckResult:
    diagnostics: list[MagmaDiagnostic]
    timed_out: bool


def _shift(diags: list[MagmaDiagnostic], offset: int) -> list[MagmaDiagnostic]:
    out: list[MagmaDiagnostic] = []
    for d in diags:
        if d.positionless:
            out.append(d)
            continue
        new_line = d.line - offset
        if new_line < 1:
            # error inside our own preamble/wrapper — surface positionless so it isn't lost
            out.append(MagmaDiagnostic(1, 1, d.severity, d.message, d.file, positionless=True))
        else:
            out.append(
                MagmaDiagnostic(new_line, d.col, d.severity, d.message, d.file, d.positionless)
            )
    return out


def syntax_check(
    source: str, *, magma_path: str | None = None, timeout: float = 10.0
) -> CheckResult:
    wrapped = f"{_SYNTAX_PREAMBLE}{source}\nreturn 0; end function;\n"
    res = run_source(wrapped, magma_path=magma_path, timeout=timeout, preamble="")
    diags = _shift(parse_diagnostics(res.stdout), _SYNTAX_OFFSET)
    return CheckResult(diagnostics=diags, timed_out=res.timed_out)


def execution_check(
    source: str,
    *,
    magma_path: str | None = None,
    timeout: float = 15.0,
    memory_bytes: int = 2 * 1024 * 1024 * 1024,
) -> CheckResult:
    preamble = (
        "SetColumns(0);\n"
        "SetAutoColumns(false);\n"
        "SetEchoInput(false);\n"
        "SetIgnorePrompt(true);\n"
        f"SetMemoryLimit({memory_bytes});\n"
        "SetQuitOnError(true);\n"
    )
    offset = preamble.count("\n")
    res = run_source(preamble + source + "\n", magma_path=magma_path, timeout=timeout, preamble="")
    diags = _shift(parse_diagnostics(res.stdout), offset)
    if res.timed_out:
        diags = [
            *diags,
            MagmaDiagnostic(1, 1, "warning", "Magma check timed out", positionless=True),
        ]
    return CheckResult(diagnostics=diags, timed_out=res.timed_out)
