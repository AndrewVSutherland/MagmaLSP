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
    [
    OptParam: Type,
    OtherParam
    ]

    Doc paragraph shared by the preceding signature group.

i.e. each signature may be followed by a bracketed optional-parameter block (names, sometimes
with types — information ``ListSignatures`` never shows), and a doc paragraph applies to the
group of signatures since the previous doc. Undeclared names instead emit a non-fatal
``User error: Identifier ... not declared`` and are skipped. ``</dev/null`` keeps the non-fatal
errors from blocking.

Besides recovering missing variadic intrinsics, this is the machinery for the **kernel doc
harvest** (``probe_names`` over undocumented DB names): ``ListSignatures`` provides no doc
strings, but ``name;`` does — for ~90% of the otherwise-undocumented names.
"""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..magma.runner import run_source
from ..parsing import new_parser
from .listsig import parse_listsig_line
from .model import Param, Signature

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


def _parse_opt_param(line: str) -> Param:
    """One optional-parameter block entry: ``Name`` / ``Name,`` / ``Name: Type,``."""
    entry = line.strip().rstrip(",").strip()
    if ":" in entry:
        pname, ptype = entry.split(":", 1)
        return Param(name=pname.strip(), type=ptype.strip() or None)
    return Param(name=entry)


def _parse_signatures_section(name: str, body: list[str]) -> list[Signature]:
    sigs: list[Signature] = []
    pending: list[Signature] = []
    doc_buf: list[str] = []
    started = False
    in_opt_block = False

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
        if in_opt_block:
            if s == "]":
                in_opt_block = False
            elif s and pending:
                pending[-1].opt_params.append(_parse_opt_param(s))
            continue
        if s == "":
            if doc_buf:
                flush_doc()
            continue
        if s == "[":
            # optional-parameter block for the signature just parsed
            in_opt_block = True
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
    names: list[str],
    *,
    magma_path: str | None = None,
    timeout: float = 180.0,
    batch: int = 500,
    workers: int | None = None,
) -> dict[str, list[Signature]]:
    """Probe ``names`` in parallel batches; return {name: signatures} for the intrinsics."""
    names = [n for n in names if _VALID_NAME.match(n)]  # never interpolate junk into Magma source
    chunks = [names[i : i + batch] for i in range(0, len(names), batch)]
    workers = workers or min(8, (os.cpu_count() or 4), max(1, len(chunks)))
    out: dict[str, list[Signature]] = {}

    def probe_chunk(chunk: list[str]) -> dict[str, list[Signature]]:
        res = run_source(build_probe_script(chunk), magma_path=magma_path, timeout=timeout)
        return parse_probe_output(res.stdout)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(probe_chunk, chunks):
            out.update(result)
    return out
