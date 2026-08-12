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
    # position of the name token itself (for LSP selection_range); defaults to (line, col)
    name_line: int = -1
    name_col: int = -1

    def name_pos(self) -> tuple[int, int]:
        return (self.name_line, self.name_col) if self.name_line >= 0 else (self.line, self.col)


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
            nl, nc = name_node.start_point if name_node else (sr, sc)
            out.append(
                Symbol(
                    name, "intrinsic", sr, sc, er, ec,
                    detail=_intrinsic_detail(node), name_line=nl, name_col=nc,
                )
            )
            continue  # don't descend into intrinsic bodies
        if t in ("function_definition", "procedure_definition"):
            # named `function NAME(...) ... end function;` — the dominant style in real code
            kind = "function" if t == "function_definition" else "procedure"
            name_node = named_child_after(node, kind)
            if name_node is not None and name_node.type == "identifier":
                sr, sc, er, ec = _pos(node)
                nl, nc = name_node.start_point
                out.append(
                    Symbol(node_text(name_node), kind, sr, sc, er, ec, name_line=nl, name_col=nc)
                )
                # descend anyway: nested named helpers are legal and useful in the outline
        if t == "assignment":
            sym = _assignment_symbol(node)
            if sym is not None:
                out.append(sym)
                continue
        if t == "declare_statement" or t == "type_declaration":
            nm = named_child_after(node, "type")
            if nm is not None:
                sr, sc, er, ec = _pos(node)
                nl, nc = nm.start_point
                out.append(Symbol(node_text(nm), "type", sr, sc, er, ec, name_line=nl, name_col=nc))
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
    # Only treat `name := function(...)` / `name := procedure(...)` / `name := func< ... >` /
    # `name := proc< ... >` as a symbol.
    ident = None
    rhs_kind = None
    seen_assign = False
    for c in node.children:
        if c.type == ":=":
            seen_assign = True
        elif c.type == "identifier" and ident is None and not seen_assign:
            ident = c
        elif seen_assign and c.type == "function_definition":
            rhs_kind = "function"
        elif seen_assign and c.type == "procedure_definition":
            rhs_kind = "procedure"
        elif seen_assign and c.type == "constructor":
            head = next((k for k in c.children if k.type == "identifier"), None)
            if head is not None and node_text(head) in ("func", "proc"):
                rhs_kind = "function" if node_text(head) == "func" else "procedure"
    if ident is None or rhs_kind is None:
        return None
    sr, sc, er, ec = _pos(node)
    nl, nc = ident.start_point
    return Symbol(node_text(ident), rhs_kind, sr, sc, er, ec, name_line=nl, name_col=nc)
