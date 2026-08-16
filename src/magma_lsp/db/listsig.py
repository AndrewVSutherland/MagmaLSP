"""Full intrinsic enumeration via ``ListSignatures`` in a running Magma.

Two Magma runs (CLAUDE.md §4a, ~4 s total):
  1. ``ListCategories();`` prints the ~761 category names (it is a print-only procedure).
  2. loop over those names, ``eval`` each into a ``Cat`` constant, and ``ListSignatures(C)`` it.
     6 parametric formers (Aut, GSet, GaloisData, PowerGroup, Set, Thue) fail ``eval`` and are
     skipped by the try/catch; their signatures are reached via other categories anyway.

The deduped output is the authoritative full set including kernel-defined intrinsics that never
appear in any package ``.m`` file. Lines look like::

    NAME(arg::Type, ~ref::Type, ...) -> Ret1, Ret2
    NAME(arg::Type, ...)                         # procedure: no arrow

NAME is a bareword or a single-quoted operator. Untyped reference args render as ``<unknown>``.
"""

from __future__ import annotations

import re

from ..magma.runner import run_source
from .model import Param, Signature

CAT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")  # 2.29-9 added a _-prefixed category
# A signature line starts with an operator in quotes or a bareword identifier, then "(".
SIG_LINE_RE = re.compile(r"^\s*('[^']+'|[A-Za-z_][A-Za-z0-9_]*)\s*\(")

_OPENERS = "([<"
_CLOSERS = ")]>"


def parse_categories(stdout: str) -> list[str]:
    names: list[str] = []
    for line in stdout.splitlines():
        s = line.strip()
        if CAT_NAME_RE.match(s):
            names.append(s)
    # de-dup while preserving order
    seen: set[str] = set()
    out = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def build_enum_script(category_names: list[str]) -> str:
    cats = ",".join(f'"{n}"' for n in category_names)
    return (
        "SetColumns(0);\n"
        f"cats := [{cats}];\n"
        "for nm in cats do\n"
        '  try cc := eval("return " cat nm cat ";"); ListSignatures(cc); catch e ; end try;\n'
        "end for;\n"
    )


def _split_top_level(text: str, sep: str = ",") -> list[str]:
    out: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in text:
        if ch in _OPENERS:
            depth += 1
        elif ch in _CLOSERS:
            depth = max(0, depth - 1)
        if ch == sep and depth == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf))
    return [s.strip() for s in out if s.strip()]


def _parse_arg(item: str) -> Param:
    if "::" in item:
        name, typ = item.split("::", 1)
        return Param(name=name.strip(), type=typ.strip())
    return Param(name="", type=item.strip())


def parse_listsig_line(line: str) -> Signature | None:
    m = SIG_LINE_RE.match(line)
    if not m:
        return None
    name = m.group(1)
    rest = line[m.end() - 1 :]  # starts at "("
    # find matching close paren for the argument list
    depth = 0
    end = None
    for i, ch in enumerate(rest):
        if ch in _OPENERS:
            depth += 1
        elif ch in _CLOSERS:
            depth -= 1
            if depth == 0:
                end = i
                break
    if end is None:
        return None
    arg_str = rest[1:end]
    after = rest[end + 1 :].strip()
    args = [_parse_arg(a) for a in _split_top_level(arg_str)]
    is_proc = not after.startswith("->")
    returns: list[str] = []
    if after.startswith("->"):
        returns = _split_top_level(after[2:].strip())
    return Signature(name=name, args=args, returns=returns, is_procedure=is_proc, kind="kernel")


def parse_enum_output(stdout: str) -> list[Signature]:
    seen: set[str] = set()
    sigs: list[Signature] = []
    for line in stdout.splitlines():
        s = line.rstrip()
        if not s or s.startswith("Signatures relevant to"):
            continue
        if s in seen:
            continue
        seen.add(s)
        sig = parse_listsig_line(s)
        if sig is not None:
            sigs.append(sig)
    return sigs


def enumerate_signatures(
    *, magma_path: str | None = None, timeout: float = 120.0
) -> list[Signature]:
    """Run the two-phase enumeration and return parsed kernel-tagged signatures.

    Raises ``RuntimeError`` when either Magma run timed out or exited nonzero: this
    enumeration is *authoritative* for the kernel half of the DB, and accepting whatever
    partial output arrived before a timeout would save a silently truncated DB that looks
    complete. Callers degrade to a package-only DB instead.
    """
    cat_res = run_source("ListCategories();\n", magma_path=magma_path, timeout=60.0)
    if cat_res.timed_out or cat_res.returncode != 0:
        why = "timed out" if cat_res.timed_out else f"exited {cat_res.returncode}"
        raise RuntimeError(f"ListCategories() run {why}")
    cats = parse_categories(cat_res.stdout)
    if not cats:
        raise RuntimeError("ListCategories() produced no category names")
    enum_res = run_source(build_enum_script(cats), magma_path=magma_path, timeout=timeout)
    if enum_res.timed_out or enum_res.returncode != 0:
        why = "timed out" if enum_res.timed_out else f"exited {enum_res.returncode}"
        raise RuntimeError(
            f"ListSignatures enumeration {why}; refusing the partial output "
            "(raise --enum-timeout to allow it to finish)"
        )
    return parse_enum_output(enum_res.stdout)
