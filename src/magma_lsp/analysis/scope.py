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

from dataclasses import dataclass

from ..parsing import new_parser


@dataclass(frozen=True)
class CallSite:
    name: str
    line: int  # 0-based
    col: int
    end_col: int


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
            calls.append(CallSite(ident.text.decode("utf-8", "replace"), sr, sc, ec))
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
