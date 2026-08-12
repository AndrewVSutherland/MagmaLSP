"""Shared agent-facing front-end logic (CLAUDE.md §10: one core, thin adapters).

Both ``magma_lsp.cli`` (shell front-end) and ``magma_lsp.mcp_server`` (MCP front-end, for an
agent driving Magma) are thin wrappers over the four operations here, so the two never diverge:

- :func:`lookup` — intrinsic signature(s) + handbook description (the hover content).
- :func:`search` — keyword search over intrinsic names + doc strings (when the name is unknown).
- :func:`check`  — diagnostics: static unknown-intrinsic + Magma syntax/binding (+ optional run).
- :func:`run`    — execute the program in a sandboxed Magma and return its output.

Each returns plain text / markdown suitable for a terminal or an MCP tool result. All degrade
honestly: no DB / no Magma / timeout produce an explicit note, never a silent gap or a crash.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .analysis.arity import arity_problems
from .analysis.pitfalls import pitfall_lints
from .analysis.scope import load_defined_symbols
from .analysis.undefined import undefined_intrinsics
from .db.index import SignatureIndex
from .db.store import best_cached_db
from .handbook import HandbookIndex
from .magma.diagnostics import MagmaDiagnostic
from .magma.runner import find_magma, run_source
from .magma.validate import execution_check, syntax_check

# Memory ceiling for the execution path. The trusted-colleague sandbox is timeout + memory limit
# (verified reliable, CLAUDE.md §3); OS-level isolation is deferred until the audience widens.
RUN_MEMORY_BYTES = 2 * 1024 * 1024 * 1024

# Output budget for `run`: enough for real results, small enough not to blow an agent's context.
RUN_MAX_OUTPUT_CHARS = 24_000

# Overload display budget for `lookup` (operators like '*' have 500+ overloads).
LOOKUP_MAX_SIGS = 10

_RUN_PREAMBLE = (
    "SetColumns(0);\nSetAutoColumns(false);\nSetEchoInput(false);\nSetIgnorePrompt(true);\n"
)

_BUILD_HINT = (
    "build it with `magma-lsp-build-db` "
    "(from the plugin/repo dir: `uv run magma-lsp-build-db`; requires Magma)"
)


def load_index() -> SignatureIndex | None:
    """Load the signature DB matching the installed Magma if available, else the newest
    cached one; None if none has been built."""
    p = best_cached_db(installed_magma_version())
    return SignatureIndex.from_path(p) if p else None


def _install_dirs() -> list[str]:
    """Candidate Magma install roots: derived from the resolved binary, then the known default."""
    dirs: list[str] = []
    resolved = find_magma(None)
    if resolved:
        dirs.append(os.path.dirname(os.path.realpath(resolved)))
    dirs.append("/opt/magma")
    return dirs


def default_handbook() -> HandbookIndex | None:
    """Locate ``<magma install>/doc/html`` and load the handbook index, or None."""
    for base in _install_dirs():
        hb = os.path.join(base, "doc", "html")
        if os.path.isdir(hb):
            return HandbookIndex.load(hb)
    return None


@lru_cache(maxsize=1)
def installed_magma_version() -> str | None:
    """Version of the *installed* Magma, from the package VERSION file (no Magma process)."""
    roots = [os.environ.get("MAGMA_PACKAGE_ROOT")] if os.environ.get("MAGMA_PACKAGE_ROOT") else []
    roots += [os.path.join(d, "package") for d in _install_dirs()]
    for root in roots:
        vf = Path(root) / "VERSION"
        try:
            if vf.is_file():
                txt = vf.read_text(encoding="utf-8").strip()
                if txt:
                    return txt.splitlines()[0].strip()
        except OSError:
            continue
    return None


def staleness_note(index: SignatureIndex | None) -> str | None:
    """A warning when the loaded DB was built for a different Magma than the installed one."""
    if index is None:
        return None
    installed = installed_magma_version()
    if installed and index.version not in ("unknown", installed):
        return (
            f"warning: signature DB was built for Magma {index.version} but the installed "
            f"Magma is {installed}; rebuild with `magma-lsp-build-db`"
        )
    return None


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
    max_sigs: int = LOOKUP_MAX_SIGS,
) -> LookupResult:
    """Render signature(s) + handbook doc for each name (the hover content).

    Names are resolved forgivingly (operators like ``#`` -> ``'#'``, case-insensitive fallback);
    unknown names get ranked near-miss suggestions. Pass a preloaded ``index`` / ``hb`` to avoid
    reloading them per call (the MCP server does).
    """
    idx = index if index is not None else load_index()
    if idx is None:
        return LookupResult(f"error: no signature DB; {_BUILD_HINT}", False)
    hbk = hb if hb is not None else (default_handbook() if handbook else None)
    blocks: list[str] = []
    all_found = True
    for name in names:
        key = idx.resolve(name)
        if key is None:
            sugg = idx.suggest(name, limit=5)
            line = f"# {name}: not a known intrinsic"
            if sugg:
                line += f" — did you mean: {', '.join(sugg)}? (magma_lookup any of these)"
            blocks.append(line)
            all_found = False
            continue
        header = f"# {key}" + (f"  _(resolved from `{name}`)_" if key != name else "")
        block = [header, idx.hover_markdown(key, max_sigs=max_sigs) or ""]
        if hbk is not None:
            doc = hbk.doc_markdown(key)
            if doc:
                block.append(f"\n{doc}")
        blocks.append("\n".join(b for b in block if b))
    note = staleness_note(idx)
    if note:
        blocks.append(note)
    return LookupResult("\n\n".join(blocks), all_found)


@dataclass(frozen=True)
class SearchResult:
    text: str
    n_hits: int


def search(
    query: str,
    *,
    limit: int = 10,
    index: SignatureIndex | None = None,
) -> SearchResult:
    """Keyword search over intrinsic names + doc strings; the entry point when the exact
    intrinsic name is unknown. Returns ranked ``Name(sig) — doc`` rows."""
    idx = index if index is not None else load_index()
    if idx is None:
        return SearchResult(f"error: no signature DB; {_BUILD_HINT}", 0)
    hits = idx.search(query, limit=limit)
    if not hits:
        return SearchResult(
            f'no matches for "{query}". Try fewer/different keywords (the search covers '
            "intrinsic names and their doc strings), or magma_lookup an exact name.",
            0,
        )
    rows: list[str] = []
    for name, _score in hits:
        sigs = idx.signatures(name)
        first = _preferred_signature(sigs)
        sig_str = first.render() if first else name
        doc = next((s.doc for s in sigs if s.doc), None)
        doc_line = f" — {_first_line(doc, 140)}" if doc else ""
        rows.append(f"{sig_str}{doc_line}")
    text = "\n".join(rows)
    note = staleness_note(idx)
    if note:
        text += f"\n\n{note}"
    return SearchResult(text, len(hits))


def _preferred_signature(sigs: list):
    if not sigs:
        return None
    from .db.index import _doc_order

    return _doc_order(sigs)[0]


def _first_line(text: str, cap: int) -> str:
    line = " ".join(text.split())
    return line if len(line) <= cap else line[: cap - 1].rsplit(" ", 1)[0] + " …"


def fmt_diags(diags: list[MagmaDiagnostic], source: str) -> list[str]:
    lines = source.splitlines()
    out: list[str] = []
    for d in diags:
        loc = "" if getattr(d, "positionless", False) else f" (line {d.line}, col {d.col})"
        src = lines[d.line - 1].strip() if 0 < d.line <= len(lines) else ""
        out.append(f"  [{d.severity}]{loc}: {d.message}" + (f"\n      >> {src}" if src else ""))
    return out


_IDENT_IN_MSG_RE = re.compile(r"Identifier '([A-Za-z_][A-Za-z0-9_]*)'")


@dataclass(frozen=True)
class CheckOutcome:
    ok: bool
    report: str


def check(
    source: str,
    *,
    execute: bool = False,
    timeout: float = 30.0,
    index: SignatureIndex | None = None,
    magma_path: str | None = None,
    filename: str | None = None,
) -> CheckOutcome:
    """Static unknown-intrinsic + Magma syntax/binding diagnostics; optional execution pass.

    ``filename`` (when the code lives in / is destined for a file) lets ``load "..."``
    directives resolve relative to it, so names defined by loaded files aren't false-flagged.
    Degrades honestly: without Magma it reports static findings with an explicit note; a
    timed-out Magma pass is INCONCLUSIVE, never OK.
    """
    idx = index if index is not None else load_index()
    notes: list[str] = []
    # (line0, col0, rendered) triples so findings from all passes interleave in source order
    problems: list[tuple[int, int, str]] = []

    def render_lint(lint) -> tuple[int, int, str]:
        return (
            lint.line,
            lint.col,
            f"  [{lint.severity}] (line {lint.line + 1}, col {lint.col + 1}): {lint.message}",
        )

    # names defined by load-ed files count as known; unresolved loads disable name checking
    loaded_names: set[str] = set()
    loads_unresolved = 0
    if "load" in source:
        base = os.path.dirname(os.path.abspath(filename)) if filename else os.getcwd()
        loaded_names, loads_unresolved = load_defined_symbols(source, base)
        if loads_unresolved:
            notes.append(
                "note: unresolved `load` target(s) — undefined-name checking skipped "
                "(the loaded file could define anything)"
            )

    static_by_name: dict[str, tuple[int, int, str]] = {}  # unknown-name -> problem triple
    if idx is not None:
        names = frozenset(idx.db.intrinsics) | loaded_names
        if not loads_unresolved:
            for lint in undefined_intrinsics(source, names, suggest=idx.suggest):
                m = re.match(r"'([^']+)'", lint.message)
                if m:
                    static_by_name.setdefault(m.group(1), render_lint(lint))
                else:
                    problems.append(render_lint(lint))
        for lint in arity_problems(source, idx.arities):
            problems.append(render_lint(lint))
        note = staleness_note(idx)
        if note:
            notes.append(note)
    else:
        notes.append(f"note: no signature DB (static name check skipped); {_BUILD_HINT}")

    # Pitfall lints (`=` vs `:=`, `==`, method-call syntax, True/False, discarded ~-results):
    # these run with or without a DB and catch exactly the mistakes LLMs make.
    problems.extend(
        render_lint(lint)
        for lint in pitfall_lints(
            source,
            intrinsic_names=frozenset(idx.db.intrinsics) if idx else frozenset(),
            ref_arg_intrinsics=idx.ref_arg_names if idx else frozenset(),
        )
    )

    magma_ran = False
    inconclusive = False
    magma_diags: list[MagmaDiagnostic] = []
    try:
        syn = syntax_check(source, magma_path=magma_path, timeout=min(timeout, 10.0))
        magma_ran = True
        if syn.timed_out:
            inconclusive = True
            notes.append(
                f"INCONCLUSIVE: Magma syntax check timed out after {min(timeout, 10.0):.0f}s; "
                "the code was NOT validated (raise timeout or simplify)"
            )
        else:
            magma_diags = syn.diagnostics
    except FileNotFoundError:
        notes.append(
            "note: Magma not found — static checks only; syntax/binding/runtime errors "
            "were NOT checked (set MAGMA_PATH or put `magma` on PATH)"
        )

    # Merge: Magma's binding errors are authoritative; drop the static duplicate for the same
    # name and enrich Magma's terse message with our suggestions instead.
    for d in magma_diags:
        msg = d.message
        m = _IDENT_IN_MSG_RE.search(msg)
        if m:
            ident = m.group(1)
            static_by_name.pop(ident, None)
            if idx is not None and ident[:1].isupper():
                sugg = idx.suggest(ident, limit=3)
                if sugg:
                    msg = f"{msg} — did you mean: {', '.join(sugg)}?"
        remapped = MagmaDiagnostic(d.line, d.col, d.severity, msg, d.file, d.positionless)
        for line in fmt_diags([remapped], source):
            problems.append((max(0, d.line - 1), max(0, d.col - 1), line))
    problems.extend(static_by_name.values())

    if not problems and not inconclusive and execute and magma_ran:
        ex = execution_check(source, magma_path=magma_path, timeout=timeout)
        for d in ex.diagnostics:
            for line in fmt_diags([d], source):
                problems.append((max(0, d.line - 1), max(0, d.col - 1), line))

    rendered = [text for _l, _c, text in sorted(problems, key=lambda p: (p[0], p[1]))]
    tail = ("\n" + "\n".join(notes)) if notes else ""
    if rendered:
        return CheckOutcome(False, "FAIL: issues found:\n" + "\n".join(rendered) + tail)
    if inconclusive:
        return CheckOutcome(False, "INCONCLUSIVE: Magma check timed out." + tail)
    if not magma_ran:
        return CheckOutcome(True, "OK: static checks passed (Magma pass skipped)." + tail)
    return CheckOutcome(True, "OK: no static or Magma errors detected." + tail)


@dataclass(frozen=True)
class RunOutcome:
    output: str
    returncode: int
    timed_out: bool
    truncated: bool = False


_TMPFILE_LOC_RE = re.compile(r'In file "[^"]*magma-lsp-[0-9a-f]+\.m", line (\d+)')


def _remap_run_output(out: str, offset: int) -> str:
    """Rewrite error-block locations from our temp file back to the user's program."""

    def fix(m: re.Match) -> str:
        line = int(m.group(1)) - offset
        if line < 1:
            return "In the run preamble, line " + m.group(1)
        return f"In your program, line {line}"

    return _TMPFILE_LOC_RE.sub(fix, out)


def _truncate_output(out: str, cap: int) -> tuple[str, bool]:
    """Head+tail truncation at line boundaries; the tail is kept because Magma's error blocks
    and final results are at the end."""
    if len(out) <= cap:
        return out, False
    head_budget, tail_budget = (cap * 2) // 5, (cap * 3) // 5
    head = out[:head_budget]
    cut = head.rfind("\n")
    if cut > 0:
        head = head[: cut + 1]
    tail = out[-tail_budget:]
    cut = tail.find("\n")
    if 0 <= cut < len(tail) - 1:
        tail = tail[cut + 1 :]
    elided = len(out) - len(head) - len(tail)
    marker = f"[... {elided} characters of output elided; print less, or raise max_output ...]\n"
    return head + marker + tail, True


def run(
    source: str,
    *,
    timeout: float = 30.0,
    memory_bytes: int = RUN_MEMORY_BYTES,
    max_output: int = RUN_MAX_OUTPUT_CHARS,
    magma_path: str | None = None,
) -> RunOutcome:
    """Execute ``source`` in a fresh sandboxed Magma (timeout + in-process memory limit).

    Error locations are remapped to the user program's own line numbers, and output beyond
    ``max_output`` chars is head+tail truncated (tail preserved: errors/results live there).
    ``SetQuitOnError`` makes the exit code meaningful (nonzero on the first runtime error).
    """
    preamble = _RUN_PREAMBLE + f"SetMemoryLimit({memory_bytes});\nSetQuitOnError(true);\n"
    offset = preamble.count("\n")
    res = run_source(source, timeout=timeout, preamble=preamble, magma_path=magma_path)
    out = _remap_run_output(res.stdout, offset)
    out, truncated = _truncate_output(out, max_output)
    return RunOutcome(out, res.returncode, res.timed_out, truncated)
