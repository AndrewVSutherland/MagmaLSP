"""Static lints Magma's interpreter does not provide (CLAUDE.md §13).

v1 ships the highest-signal, lowest-false-positive check: **assigned-but-never-used local
variables**, scoped per function / procedure / intrinsic body and at the top level.

Conservative by design:
- A scope is treated as a single visibility region (nested ``for``/``while`` bodies share it),
  so a variable used *anywhere* in its function is never flagged -> no false positives from
  loop/branch nesting (at the cost of missing some nested-only dead stores).
- Only *bare* assignment targets and ``local`` declarations are treated as definitions. Indexed
  (``x[i] := ...``) and attribute (``x`attr := ...``) assignments leave ``x`` as a use, since the
  object must already exist.
- The discard placeholder ``_`` is never reported. Parameters are not reported (an intrinsic
  legitimately may not use every argument).
"""

from __future__ import annotations

from dataclasses import dataclass

from tree_sitter import Node

from ..parsing import new_parser, node_text

SCOPE_BOUNDARY = frozenset(
    {
        "function_definition",
        "procedure_definition",
        "intrinsic_definition",
    }
)


def is_callable_ctor(node: Node) -> bool:
    """A ``func< ... >`` / ``proc< ... >`` literal (they parse as ``constructor`` nodes)."""
    if node.type != "constructor":
        return False
    for c in node.children:
        if c.type == "identifier":
            return node_text(c) in ("func", "proc")
    return False


@dataclass(frozen=True)
class Lint:
    line: int  # 0-based
    col: int
    end_line: int
    end_col: int
    message: str
    severity: str  # "warning" | "hint"
    unnecessary: bool = False


def _unit_nodes(unit: Node) -> list[Node]:
    """All nodes in `unit` without descending into nested scope boundaries."""
    nodes: list[Node] = []
    stack = [(unit, True)]
    while stack:
        node, is_root = stack.pop()
        nodes.append(node)
        for child in node.children:
            if child.type in SCOPE_BOUNDARY and not is_root:
                continue
            stack.append((child, False))
    return nodes


_CALLABLE_RHS = frozenset({"function_definition", "procedure_definition"})


def _assignment_targets(node: Node) -> list[Node]:
    # A binding whose value is a function/procedure literal (including `func< ... >` /
    # `proc< ... >`) is a callable definition, not a dead store: don't treat its target as an
    # unused-variable candidate.
    seen_assign = False
    for child in node.children:
        if child.type == ":=":
            seen_assign = True
        elif seen_assign and (child.type in _CALLABLE_RHS or is_callable_ctor(child)):
            return []
    targets: list[Node] = []
    for child in node.children:
        if child.type == ":=":
            break
        if child.type == "identifier":
            targets.append(child)
    return targets


def _analyze_unit(unit: Node, out: list[Lint]) -> None:
    nodes = _unit_nodes(unit)
    def_ids: set[int] = set()
    type_ids: set[int] = set()
    # name -> first defining identifier node (for reporting position)
    first_def: dict[str, Node] = {}

    for node in nodes:
        if node.type == "type":
            stack = [node]
            while stack:
                n = stack.pop()
                if n.type == "identifier":
                    type_ids.add(id(n))
                stack.extend(n.children)
        elif node.type == "assignment":
            for t in _assignment_targets(node):
                def_ids.add(id(t))
                first_def.setdefault(node_text(t), t)
        elif node.type == "local_statement":
            for c in node.children:
                if c.type == "identifier":
                    def_ids.add(id(c))
                    first_def.setdefault(node_text(c), c)
        elif node.type == "for_quantifier":
            # Loop variables are registered as definitions but never reported as unused:
            # `for i := 1 to n do` (repeat-n-times) legitimately ignores i, and the `for x in S`
            # form parses its binder inside a binary_operator anyway.
            for c in node.children:
                if c.type == "identifier":
                    def_ids.add(id(c))
                    break

    used: set[str] = set()
    for node in nodes:
        if node.type == "identifier" and id(node) not in def_ids and id(node) not in type_ids:
            used.add(node_text(node))

    for name, defnode in first_def.items():
        if name == "_" or name in used:
            continue
        sr, sc = defnode.start_point
        er, ec = defnode.end_point
        out.append(
            Lint(
                line=sr,
                col=sc,
                end_line=er,
                end_col=ec,
                message=f"'{name}' is assigned but never used",
                severity="warning",
                unnecessary=True,
            )
        )


def unused_variables(source: bytes | str) -> list[Lint]:
    data = source.encode("utf-8") if isinstance(source, str) else source
    tree = new_parser().parse(data)

    units = [tree.root_node]
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        for child in node.children:
            if child.type in SCOPE_BOUNDARY:
                units.append(child)
            stack.append(child)

    out: list[Lint] = []
    for unit in units:
        _analyze_unit(unit, out)
    out.sort(key=lambda d: (d.line, d.col))
    return out
