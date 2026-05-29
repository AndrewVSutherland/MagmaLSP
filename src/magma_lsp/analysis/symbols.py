"""documentSymbol support: top-level definitions in a Magma file.

Reports intrinsics, and top-level ``f := function(...)`` / ``p := procedure(...)`` bindings,
plus user type declarations. Positions are 0-based (LSP).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..parsing import named_child_after, new_parser, node_text


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str  # "function" | "procedure" | "intrinsic" | "type"
    line: int  # 0-based
    col: int  # 0-based
    end_line: int
    end_col: int
    detail: str = ""


def _pos(node) -> tuple[int, int, int, int]:
    sr, sc = node.start_point
    er, ec = node.end_point
    return sr, sc, er, ec


def document_symbols(source: bytes | str) -> list[Symbol]:
    data = source.encode("utf-8") if isinstance(source, str) else source
    tree = new_parser().parse(data)
    out: list[Symbol] = []

    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        t = node.type
        if t == "intrinsic_definition":
            name_node = named_child_after(node, "intrinsic")
            name = node_text(name_node) if name_node else "?"
            sr, sc, er, ec = _pos(node)
            out.append(Symbol(name, "intrinsic", sr, sc, er, ec, detail=_intrinsic_detail(node)))
            continue  # don't descend into intrinsic bodies
        if t == "assignment":
            sym = _assignment_symbol(node)
            if sym is not None:
                out.append(sym)
                continue
        if t == "declare_statement" or t == "type_declaration":
            nm = named_child_after(node, "type")
            if nm is not None:
                sr, sc, er, ec = _pos(node)
                out.append(Symbol(node_text(nm), "type", sr, sc, er, ec))
        stack.extend(reversed(node.children))

    out.sort(key=lambda s: (s.line, s.col))
    return out


def _intrinsic_detail(node) -> str:
    for c in node.children:
        if c.type == "doc_string":
            txt = node_text(c).strip("{} ").strip()
            return txt.split("\n", 1)[0][:80]
    return ""


def _assignment_symbol(node) -> Symbol | None:
    # Only treat `name := function(...)` / `name := procedure(...)` as a symbol.
    ident = None
    rhs_kind = None
    seen_assign = False
    for c in node.children:
        if c.type == ":=":
            seen_assign = True
        elif c.type == "identifier" and ident is None and not seen_assign:
            ident = c
        elif seen_assign and c.type in ("function_definition", "function_expression"):
            rhs_kind = "function"
        elif seen_assign and c.type in ("procedure_definition", "procedure_expression"):
            rhs_kind = "procedure"
    if ident is None or rhs_kind is None:
        return None
    sr, sc, er, ec = _pos(node)
    return Symbol(node_text(ident), rhs_kind, sr, sc, er, ec)
