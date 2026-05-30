"""Recover intrinsics that ``ListSignatures(Cat)`` omits (e.g. variadic ones like ``Sprintf``,
``Explode``) by probing candidate names with the REPL ``name;`` form, which Magma honours even in
batch mode and which *does* show variadic ``...`` signatures.

Candidates are call-targets harvested from the package corpus that aren't already in the DB: real
package code calls real intrinsics, so the missing-from-DB call-targets are exactly the intrinsics
the category enumeration dropped (plus package-local helpers, which probe as "not an intrinsic"
and are skipped).

The ``name;`` output (per name, between our ``@@@<name>`` markers):

    Intrinsic 'NAME'

    Signatures:

    (arg::Type, ...) -> Ret, ...
    (arg::Type) -> Ret

    Doc paragraph shared by the preceding signature group.

Undeclared names instead emit a non-fatal ``User error: Identifier ... not declared`` and are
skipped. ``</dev/null`` keeps the non-fatal errors from blocking.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from ..magma.runner import run_source
from ..parsing import new_parser
from .listsig import parse_listsig_line
from .model import Signature

MARKER = "@@@"
_VALID_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def harvest_call_targets(package_root: str) -> set[str]:
    """All identifier call-targets used anywhere in the package ``.m`` corpus."""
    parser = new_parser()
    names: set[str] = set()
    for dirpath, _dirs, files in os.walk(package_root):
        for fn in files:
            if not fn.endswith(".m"):
                continue
            try:
                data = Path(dirpath, fn).read_bytes()
            except OSError:
                continue
            tree = parser.parse(data)
            stack = [tree.root_node]
            while stack:
                node = stack.pop()
                if node.type == "call" and node.children and node.children[0].type == "identifier":
                    names.add(node.children[0].text.decode("utf-8", "replace"))
                stack.extend(node.children)
    return {n for n in names if _VALID_NAME.match(n)}


def build_probe_script(names: list[str]) -> str:
    # `eval("name;")` (not a bare `name;`): if `name` is a reserved word the statement is a parse
    # error, and only eval turns that into a *runtime* error the try/catch can absorb — a bare form
    # would abort the whole batch script at the first such candidate.
    lines = ["SetColumns(0);"]
    for n in names:
        lines.append(f'print "{MARKER}{n}";')
        lines.append(f'try eval("{n};"); catch e ; end try;')
    return "\n".join(lines) + "\n"


def _parse_signatures_section(name: str, body: list[str]) -> list[Signature]:
    sigs: list[Signature] = []
    pending: list[Signature] = []
    doc_buf: list[str] = []
    started = False

    def flush_doc() -> None:
        if doc_buf and pending:
            doc = " ".join(doc_buf)
            for sg in pending:
                sg.doc = doc
        pending.clear()
        doc_buf.clear()

    for raw in body:
        s = raw.strip()
        if not started:
            if s == "Signatures:":
                started = True
            continue
        if s == "":
            if doc_buf:
                flush_doc()
            continue
        if s.startswith("("):
            if doc_buf:
                flush_doc()
            sig = parse_listsig_line(name + s)
            if sig is not None:
                sig.kind = "kernel"
                sigs.append(sig)
                pending.append(sig)
        else:
            doc_buf.append(s)
    flush_doc()
    return sigs


def parse_probe_output(text: str) -> dict[str, list[Signature]]:
    results: dict[str, list[Signature]] = {}
    for block in text.split(MARKER)[1:]:
        lines = block.splitlines()
        if not lines:
            continue
        name = lines[0].strip()
        body = lines[1:]
        if not any(line.strip().startswith("Intrinsic '") for line in body):
            continue  # undeclared / not an intrinsic
        sigs = _parse_signatures_section(name, body)
        if sigs:
            results[name] = sigs
    return results


def probe_names(
    names: list[str], *, magma_path: str | None = None, timeout: float = 180.0, batch: int = 4000
) -> dict[str, list[Signature]]:
    """Probe ``names`` in batches; return {name: signatures} for those that are intrinsics."""
    out: dict[str, list[Signature]] = {}
    for i in range(0, len(names), batch):
        chunk = names[i : i + batch]
        res = run_source(build_probe_script(chunk), magma_path=magma_path, timeout=timeout)
        out.update(parse_probe_output(res.stdout))
    return out
