"""Magma-backed validation of user source (CLAUDE.md §5).

The **syntax/binding pass** (default, safe) picks a strategy from the file's shape
(tree-sitter pre-scan):

- *plain script* — wrap the code in a never-called function so it is parsed and name-bound but
  never executed. Catches syntax errors and ``Identifier ... has not been declared``. ``load``
  directives (illegal inside a function) are blanked out first, and binding errors are then
  suppressed for such files (the loaded file could define anything).
- *package file* (contains ``intrinsic`` declarations, which are illegal inside the wrapper) —
  write it to a temp ``.m`` and ``Attach`` it: parse errors are reported with real positions;
  nothing user-level executes. (Attach binds lazily, so undefined names in bodies are left to
  the static check, which models ``import``/``forward`` correctly.)
- *does not parse* (tree-sitter ERROR) — report tree-sitter's syntax errors and skip Magma
  entirely: wrapping unbalanced code can close the wrapper early, yielding phantom errors and
  even *executing* the remainder.

The **execution pass** (opt-in, heavier) runs the code for real under a memory limit and
``SetQuitOnError``. Aborts on the first error.

Positioned diagnostics are filtered to *our* temp file (program output that merely looks like
an error block is ignored), shifted back to the user document's 1-based coordinates, clamped
to the document, and tab-expanded columns are mapped back to character offsets.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import uuid
from dataclasses import dataclass

from ..parsing import new_parser
from .diagnostics import MagmaDiagnostic, parse_diagnostics
from .runner import SERVER_PREAMBLE, run_source

# Lines prepended before the user's first line in the wrapped pass.
_SYNTAX_PREAMBLE = (
    "SetColumns(0);\n"
    "SetAutoColumns(false);\n"
    "SetEchoInput(false);\n"
    "SetIgnorePrompt(true);\n"
    "__magma_lsp_chk := function()\n"
)
_SYNTAX_OFFSET = _SYNTAX_PREAMBLE.count("\n")  # user line 1 == magma line OFFSET+1

_MAX_TS_DIAGS = 20


@dataclass(frozen=True)
class CheckResult:
    diagnostics: list[MagmaDiagnostic]
    timed_out: bool


# ----------------------------------------------------------------------------------------------
# coordinate mapping helpers
# ----------------------------------------------------------------------------------------------
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


def _vcol_to_char_col(line: str, vcol: int) -> int:
    """Invert Magma's tab-to-8-column-stop expansion: visual column -> 1-based char offset."""
    v = 0
    for i, ch in enumerate(line):
        if v >= vcol - 1:
            return i + 1
        v = (v // 8 + 1) * 8 if ch == "\t" else v + 1
    return len(line) + 1


def _fit_to_source(diags: list[MagmaDiagnostic], source: str) -> list[MagmaDiagnostic]:
    """Clamp lines to the document and convert tab-expanded columns to char offsets."""
    lines = source.splitlines()
    out: list[MagmaDiagnostic] = []
    for d in diags:
        if d.positionless:
            out.append(d)
            continue
        line, col, msg = d.line, d.col, d.message
        if lines and line > len(lines):
            # Error on our appended wrapper tail: an unterminated construct in the user code.
            line, col = len(lines), 1
            msg += " (at end of input — unterminated block?)"
        if 0 < line <= len(lines) and "\t" in lines[line - 1]:
            col = _vcol_to_char_col(lines[line - 1], col)
        out.append(MagmaDiagnostic(line, col, d.severity, msg, d.file, d.positionless))
    return out


# ----------------------------------------------------------------------------------------------
# tree-sitter pre-scan
# ----------------------------------------------------------------------------------------------
def _tree_sitter_diags(root) -> list[MagmaDiagnostic]:
    out: list[MagmaDiagnostic] = []
    stack = [root]
    while stack and len(out) < _MAX_TS_DIAGS:
        node = stack.pop()
        if node.type == "ERROR" or node.is_missing:
            row, col = node.start_point
            if node.is_missing:
                msg = f"Missing '{node.type}'"
            else:
                msg = "Syntax error (malformed or unbalanced construct)"
            out.append(MagmaDiagnostic(row + 1, col + 1, "error", msg))
            continue
        stack.extend(node.children)
    out.sort(key=lambda d: (d.line, d.col))
    return out


def _contains_intrinsic(root) -> bool:
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "intrinsic_definition":
            return True
        stack.extend(node.children)
    return False


def _blank_out_loads(source: str, root) -> tuple[str, bool]:
    """Replace ``load "..."`` directives with spaces (same byte length -> positions intact).

    Newlines inside the node are preserved: a ``load`` whose string sits on the next line
    parses as one multi-line ``load_directive``, and blanking its newline would shift every
    subsequent diagnostic up a line.
    """
    data = bytearray(source.encode("utf-8"))
    found = False
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "load_directive":
            for i in range(node.start_byte, node.end_byte):
                if data[i] not in (0x0A, 0x0D):  # keep \n / \r
                    data[i] = 0x20
            found = True
            continue
        stack.extend(node.children)
    return (data.decode("utf-8"), True) if found else (source, False)


# ----------------------------------------------------------------------------------------------
# the two Magma-backed strategies
# ----------------------------------------------------------------------------------------------
def _wrapped_check(
    source: str, root, *, magma_path: str | None, timeout: float
) -> CheckResult:
    src, has_loads = _blank_out_loads(source, root)
    wrapped = f"{_SYNTAX_PREAMBLE}{src}\nreturn 0; end function;\n"
    res = run_source(wrapped, magma_path=magma_path, timeout=timeout, preamble="")
    diags = _shift(parse_diagnostics(res.stdout, expect_file=res.source_path), _SYNTAX_OFFSET)
    if has_loads:
        # `load` can define anything; undefined-name reports would be false positives.
        diags = [d for d in diags if "has not been declared" not in d.message]
    return CheckResult(diagnostics=_fit_to_source(diags, source), timed_out=res.timed_out)


def _attach_check(source: str, *, magma_path: str | None, timeout: float) -> CheckResult:
    """Package files: Attach() parses (never executes) them, reporting real positions."""
    pkg_path = os.path.join(tempfile.gettempdir(), f"magma-lsp-pkg-{uuid.uuid4().hex}.m")
    with open(pkg_path, "w", encoding="utf-8") as fh:
        fh.write(source)
        if not source.endswith("\n"):
            fh.write("\n")
    try:
        driver = f'Attach("{pkg_path}");\n'
        res = run_source(driver, magma_path=magma_path, timeout=timeout, preamble=SERVER_PREAMBLE)
        # Positions are already in the package file's own coordinates: no shift. The driver's
        # own "Cannot attach" follow-up is filtered out by expect_file.
        diags = parse_diagnostics(res.stdout, expect_file=pkg_path)
        diags = [d for d in diags if "Cannot attach intrinsics" not in d.message]
        return CheckResult(diagnostics=_fit_to_source(diags, source), timed_out=res.timed_out)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(pkg_path)


# ----------------------------------------------------------------------------------------------
# public entry points
# ----------------------------------------------------------------------------------------------
def syntax_check(
    source: str, *, magma_path: str | None = None, timeout: float = 10.0
) -> CheckResult:
    tree = new_parser().parse(source.encode("utf-8"))
    root = tree.root_node
    if root.has_error:
        # Wrapping unbalanced code can escape the wrapper (phantom errors at wrong lines, and
        # the remainder would EXECUTE). Report tree-sitter's positions instead; the user gets
        # Magma-grade messages again as soon as the file parses.
        return CheckResult(diagnostics=_tree_sitter_diags(root), timed_out=False)
    if _contains_intrinsic(root):
        return _attach_check(source, magma_path=magma_path, timeout=timeout)
    return _wrapped_check(source, root, magma_path=magma_path, timeout=timeout)


def execution_check(
    source: str,
    *,
    magma_path: str | None = None,
    timeout: float = 15.0,
    memory_bytes: int = 2 * 1024 * 1024 * 1024,
    cwd: str | None = None,
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
    res = run_source(
        preamble + source + "\n", magma_path=magma_path, timeout=timeout, preamble="", cwd=cwd
    )
    diags = _shift(parse_diagnostics(res.stdout, expect_file=res.source_path), offset)
    diags = _fit_to_source(diags, source)
    if res.timed_out:
        diags = [
            *diags,
            MagmaDiagnostic(1, 1, "warning", "Magma check timed out", positionless=True),
        ]
    return CheckResult(diagnostics=diags, timed_out=res.timed_out)
