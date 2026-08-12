"""Collect the names *available* in a Magma document and the call sites in it.

A name is available if it is introduced by any of: an ``import "file": Name;`` directive, a
``forward Name;`` declaration, a named ``function``/``procedure`` definition, an ``intrinsic``
definition, an assignment target, a ``local`` declaration, a parameter, a loop variable, or a
``where`` binding. Availability is computed document-wide (any scope) — deliberately
over-approximating so legitimate forward references and scope nuances never produce a false
"undefined" report.

Call sites are ``call`` nodes whose target is a bare identifier (``Foo(...)``). Indexed or
attribute call targets (``A[i](...)``, ``x`f(...)``) are not bare identifiers and are ignored.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..parsing import new_parser


@dataclass(frozen=True)
class CallSite:
    name: str
    line: int  # 0-based
    col: int
    end_col: int
    n_args: int = -1  # positional args (optional `: P := v` args excluded); -1 = unknown
    has_ref_arg: bool = False  # any `~x` argument (procedure-style call)


_ARG_TOKEN_TYPES = frozenset({"(", ")", ",", ":", "comment"})


def _call_args(call_node) -> tuple[int, bool]:
    """Count positional arguments of a ``call`` node and detect ``~x`` reference args."""
    for c in call_node.children:
        if c.type == "argument_list":
            n = 0
            has_ref = False
            for a in c.children:
                if a.type == ":":
                    break  # optional `: P := v` parameters follow
                if a.type in _ARG_TOKEN_TYPES:
                    continue
                n += 1
                if a.type == "unary_operator" and a.children and a.children[0].type == "~":
                    has_ref = True
            return n, has_ref
    return -1, False


_BIND_LISTS = frozenset(
    {
        "import_directive",
        "forward",
        "local_statement",
        "parameters",
        "typed_identifier",
        "ref_typed_identifier",
        "ref_identifier",
    }
)


def _named_def(node) -> str | None:
    """Name of a `function NAME(...)` / `procedure NAME(...)` / `intrinsic NAME(...)` form."""
    kids = node.children
    for i, c in enumerate(kids):
        if c.type in ("function", "procedure", "intrinsic"):
            if i + 1 < len(kids) and kids[i + 1].type == "identifier":
                return kids[i + 1].text.decode("utf-8", "replace")
            return None
    return None


def analyze(source: bytes | str) -> tuple[set[str], list[CallSite]]:
    data = source.encode("utf-8") if isinstance(source, str) else source
    tree = new_parser().parse(data)

    available: set[str] = set()
    calls: list[CallSite] = []

    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        t = node.type

        if t == "call" and node.children and node.children[0].type == "identifier":
            ident = node.children[0]
            sr, sc = ident.start_point
            _er, ec = ident.end_point
            n_args, has_ref = _call_args(node)
            calls.append(
                CallSite(ident.text.decode("utf-8", "replace"), sr, sc, ec, n_args, has_ref)
            )
        elif t in ("function_definition", "procedure_definition", "intrinsic_definition"):
            name = _named_def(node)
            if name:
                available.add(name)
        elif t == "assignment":
            for c in node.children:
                if c.type == ":=":
                    break
                if c.type == "identifier":
                    available.add(c.text.decode("utf-8", "replace"))
        elif t == "for_quantifier":
            for c in node.children:
                if c.type == "identifier":
                    available.add(c.text.decode("utf-8", "replace"))
                    break
        elif t == "in":
            # Binder of a for-loop (`for phi in A`), comprehension (`[ e : c in C ]`), or
            # quantifier (`forall x in S`): the identifier immediately before `in` is bound.
            # (A membership test `x in S` harmlessly re-adds an already-defined name.)
            prev = node.prev_sibling
            if prev is not None and prev.type == "identifier":
                available.add(prev.text.decode("utf-8", "replace"))
        elif t in _BIND_LISTS:
            for c in node.children:
                if c.type == "identifier":
                    available.add(c.text.decode("utf-8", "replace"))
        elif t == "where_expression":
            # `expr where x is ...` / `where x := ...`: identifiers before `is`/`:=` are bound
            for c in node.children:
                if c.type in ("is", ":="):
                    break
                if c.type == "identifier":
                    available.add(c.text.decode("utf-8", "replace"))

        stack.extend(node.children)

    return available, calls


def load_targets(source: bytes | str) -> list[str]:
    """The quoted paths of ``load "file";`` directives in the document (unordered)."""
    data = source.encode("utf-8") if isinstance(source, str) else source
    if b"load" not in data:
        return []
    tree = new_parser().parse(data)
    out: list[str] = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "load_directive":
            for c in node.children:
                if c.type == "string":
                    out.append(c.text.decode("utf-8", "replace").strip('"'))
            continue
        stack.extend(node.children)
    return out


def load_defined_symbols(source: bytes | str, base_dir: str | None) -> tuple[set[str], int]:
    """Names defined by the files the document ``load``s (resolved against ``base_dir``).

    Returns ``(names, n_unresolved)``; an unresolved load means undefined-name checking for
    the document is unreliable (the loaded file could define anything).
    """
    names: set[str] = set()
    unresolved = 0
    for target in load_targets(source):
        path = target if os.path.isabs(target) else os.path.join(base_dir or ".", target)
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            unresolved += 1
            continue
        try:
            names |= defined_symbols(data)
        except Exception:
            unresolved += 1
    return names, unresolved


def defined_symbols(source: bytes | str) -> set[str]:
    """Names a file makes available to *other* files in the same project: named
    function/procedure/intrinsic definitions, ``forward`` declarations, and assignment targets
    (which cover ``F := function ...`` / ``F := func< ... >`` helpers). Deliberately generous —
    used to suppress cross-file "undefined" false positives in multi-file packages.
    """
    data = source.encode("utf-8") if isinstance(source, str) else source
    tree = new_parser().parse(data)

    out: set[str] = set()
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        t = node.type
        if t in ("function_definition", "procedure_definition", "intrinsic_definition"):
            name = _named_def(node)
            if name:
                out.add(name)
        elif t == "forward":
            for c in node.children:
                if c.type == "identifier":
                    out.add(c.text.decode("utf-8", "replace"))
        elif t == "assignment":
            for c in node.children:
                if c.type == ":=":
                    break
                if c.type == "identifier":
                    out.add(c.text.decode("utf-8", "replace"))
        stack.extend(node.children)
    return out
