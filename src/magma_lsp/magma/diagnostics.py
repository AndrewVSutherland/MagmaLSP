"""Parse Magma's textual error blocks into structured diagnostics.

Magma's positioned error block (CLAUDE.md §5):

    <blank line>
    In file "<path>", line <N>, column <M>:
    >> <source echo>
            ^
    <SEVERITY>: <message>

Severities: ``User error`` (syntax errors; undefined-identifier; user ``error``),
``Runtime error`` / ``Runtime error in '<NAME>'`` / ``Runtime error in evaluation``,
``System Error`` (e.g. memory limit). A ``Bad argument types`` error is followed by an
``Argument types given: ...`` line with no header, which we fold into the message.

Line/column are 1-based as Magma reports them (tabs counted to 8-col stops); callers map them
to 0-based LSP positions and undo any wrapper line offset.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SEV = r"User error|Runtime error(?: in [^:]+)?|System [Ee]rror"

POSITIONED_RE = re.compile(
    r'(?:\[PC\]\s)?In file "(?P<file>[^"]+)", line (?P<line>\d+), column (?P<col>\d+):[ \t]*\n'
    r"(?:\[PC\]\s)?>> (?P<src>.*)\n"
    r"(?:\[PC\]\s)?[ \t]*\^[ \t]*\n"
    rf"(?:\[PC\]\s)?(?P<sev>{_SEV}):[ \t]?(?P<msg>.*)",
    re.MULTILINE,
)

# eval-expression form (`magma -e/-E`) reports no real path.
EVAL_RE = re.compile(
    r"(?:\[PC\]\s)?In eval expression, line (?P<line>\d+), column (?P<col>\d+):[ \t]*\n"
    r"(?:\[PC\]\s)?>> (?P<src>.*)\n"
    r"(?:\[PC\]\s)?[ \t]*\^[ \t]*\n"
    rf"(?:\[PC\]\s)?(?P<sev>{_SEV}):[ \t]?(?P<msg>.*)",
    re.MULTILINE,
)

ARG_TYPES_RE = re.compile(r"^(?:\[PC\]\s)?Argument types given: .*$", re.MULTILINE)
WARNING_RE = re.compile(r"^[ \t]*(?:\[PC\]\s)?WARNING\b.*$", re.MULTILINE | re.IGNORECASE)


@dataclass(frozen=True)
class MagmaDiagnostic:
    line: int  # 1-based as Magma reports; None-position errors use 1
    col: int  # 1-based
    severity: str  # "error" | "warning"
    message: str
    file: str | None = None
    positionless: bool = False


def _clean_msg(msg: str, text: str, match_end: int) -> str:
    # Fold a following "Argument types given: ..." line into the message.
    tail = text[match_end : match_end + 200]
    m = ARG_TYPES_RE.search(tail)
    if m and m.start() <= 1:
        extra = m.group(0).replace("[PC] ", "").strip()
        return f"{msg.strip()} ({extra})"
    return msg.strip()


def parse_diagnostics(text: str) -> list[MagmaDiagnostic]:
    diags: list[MagmaDiagnostic] = []
    consumed_spans: list[tuple[int, int]] = []

    for regex, has_file in ((POSITIONED_RE, True), (EVAL_RE, False)):
        for m in regex.finditer(text):
            consumed_spans.append(m.span())
            diags.append(
                MagmaDiagnostic(
                    line=int(m.group("line")),
                    col=int(m.group("col")),
                    severity="error",
                    message=_clean_msg(m.group("msg"), text, m.end()),
                    file=m.group("file") if has_file else None,
                )
            )

    # Positionless severities not already captured in a positioned block.
    loose = re.compile(rf"^(?:\[PC\]\s)?(?P<sev>{_SEV}):[ \t]?(?P<msg>.*)$", re.MULTILINE)
    for m in loose.finditer(text):
        if any(s <= m.start() < e for s, e in consumed_spans):
            continue
        diags.append(
            MagmaDiagnostic(
                line=1, col=1, severity="error", message=m.group("msg").strip(), positionless=True
            )
        )

    for m in WARNING_RE.finditer(text):
        msg = m.group(0).replace("[PC] ", "").strip()
        diags.append(
            MagmaDiagnostic(line=1, col=1, severity="warning", message=msg, positionless=True)
        )

    return diags
