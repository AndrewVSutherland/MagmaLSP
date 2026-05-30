"""Extract intrinsic signatures from Magma package ``.m`` files using tree-sitter-magma.

We parse each file once and pull every ``intrinsic_definition`` node, reading its name, typed
arguments, optional parameters, return types, doc string, and source location. The ``{"}`` ditto
doc-string form (680 occurrences across the corpus; CLAUDE.md §6) is resolved to the previous
overload of the same name within the file.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from tree_sitter import Node

from ..parsing import named_child_after, new_parser, node_text
from .model import Param, Signature, SourceLocation

DITTO = '"'  # inner text of the {"} ditto doc string


def _norm(text: str) -> str:
    """Collapse internal whitespace (types/defaults may wrap across lines)."""
    return " ".join(text.split())


def _doc_content(doc_node: Node) -> str | None:
    """Strip the outer braces of a ``doc_string`` node; return None for empty ``{}``."""
    raw = node_text(doc_node).strip()
    if raw.startswith("{") and raw.endswith("}"):
        raw = raw[1:-1]
    inner = raw.strip()
    return inner or None


def _iter_intrinsic_defs(node: Node) -> Iterator[Node]:
    # Iterative DFS: tree-sitter trees for large package files can nest deeper than
    # Python's recursion limit, so we use an explicit stack via the tree cursor's children.
    stack: list[Node] = [node]
    while stack:
        cur = stack.pop()
        if cur.type == "intrinsic_definition":
            yield cur
            # intrinsic bodies don't contain nested intrinsic definitions; skip descending.
            continue
        stack.extend(reversed(cur.children))


def _arg_from_typed(node: Node, *, is_ref: bool) -> Param | None:
    name = None
    typ = None
    for child in node.children:
        if child.type == "identifier" and name is None:
            name = node_text(child)
        elif child.type == "type":
            typ = _norm(node_text(child))
    if name is None:
        return None
    return Param(name=("~" + name) if is_ref else name, type=typ)


def _opt_param(node: Node) -> Param | None:
    text = node_text(node)
    if ":=" not in text:
        name = node_text(node.children[0]) if node.children else _norm(text)
        return Param(name=name)
    lhs, rhs = text.split(":=", 1)
    return Param(name=lhs.strip(), default=_norm(rhs))


def extract_signature(node: Node, *, file: str | None = None) -> Signature | None:
    name_node = named_child_after(node, "intrinsic")
    # The name must be an identifier (bareword or quoted operator). Anything else means a malformed
    # or commented-out declaration, e.g. `intrinsic // poles and residues ...` -> skip it.
    if name_node is None or name_node.type != "identifier":
        return None
    name = node_text(name_node)

    args: list[Param] = []
    opt_params: list[Param] = []
    returns: list[str] = []
    doc: str | None = None
    seen_arrow = False
    has_arrow = any(c.type == "->" for c in node.children)

    for child in node.children:
        t = child.type
        if t == "->":
            seen_arrow = True
        elif t == "typed_identifier":
            p = _arg_from_typed(child, is_ref=False)
            if p:
                args.append(p)
        elif t == "ref_typed_identifier":
            p = _arg_from_typed(child, is_ref=True)
            if p:
                args.append(p)
        elif t == "optional_parameter":
            p = _opt_param(child)
            if p:
                opt_params.append(p)
        elif t == "type" and seen_arrow:
            returns.append(_norm(node_text(child)))
        elif t == "doc_string":
            doc = _doc_content(child)

    row, col = node.start_point
    src = SourceLocation(file=file, line=row + 1, col=col + 1) if file else None
    return Signature(
        name=name,
        args=args,
        opt_params=opt_params,
        returns=returns,
        doc=doc,
        is_procedure=not has_arrow,
        kind="package",
        source=src,
    )


def extract_file(path: str | Path) -> list[Signature]:
    """Parse one ``.m`` file and return its intrinsic signatures (ditto docs resolved)."""
    path = str(path)
    data = Path(path).read_bytes()
    parser = new_parser()
    tree = parser.parse(data)

    sigs: list[Signature] = []
    last_doc: dict[str, str | None] = {}
    for node in _iter_intrinsic_defs(tree.root_node):
        sig = extract_signature(node, file=path)
        if sig is None:
            continue
        if sig.doc == DITTO:
            sig.doc = last_doc.get(sig.name)
        else:
            last_doc[sig.name] = sig.doc
        sigs.append(sig)
    return sigs


def iter_package_files(root: str | Path) -> Iterator[str]:
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if fn.endswith(".m"):
                yield os.path.join(dirpath, fn)
