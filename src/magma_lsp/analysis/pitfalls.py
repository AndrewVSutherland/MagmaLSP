"""Lints for the Magma mistakes LLMs (and newcomers) actually make.

Magma's own parser reports these as cryptic errors ("bad syntax", a silent no-op group
relation, "Identifier not declared") or not at all. Each lint here converts a known confusion
into a one-step fix, which is exactly what a small model iterating on diagnostics needs:

- ``x = 5;``       — a *group relation* statement, silently useless -> "did you mean ``:=``?"
- ``x == 5`` / ``2 ** 3`` — Python operators -> ``eq`` / ``^``.
- ``L.append(3)``  — method-call syntax does not exist -> call the intrinsic.
- ``True/False/None`` — Magma booleans are lowercase; None does not exist.
- ``a // b``       — ``//`` starts a comment; integer division is ``div``.
- ``Append(L, x);`` as a statement — the returned value is discarded; the in-place form is
  ``Append(~L, x)`` (needs the signature DB to know which intrinsics have a ``~`` form).
- assignment to an intrinsic name that the same file also calls — the call will break.

All positions are 0-based (LSP convention), like the other analysis passes.
"""

from __future__ import annotations

from ..parsing import new_parser, node_text
from .lints import Lint
from .scope import analyze

_PY_LITERALS = {
    "True": "Magma's booleans are lowercase: use `true`",
    "False": "Magma's booleans are lowercase: use `false`",
    "None": "`None` does not exist in Magma (use `false`, `0`, or an empty structure)",
}


def _lint(node, message: str, *, severity: str = "warning") -> Lint:
    sr, sc = node.start_point
    er, ec = node.end_point
    return Lint(line=sr, col=sc, end_line=er, end_col=ec, message=message, severity=severity)


def pitfall_lints(
    source: bytes | str,
    *,
    intrinsic_names: frozenset[str] = frozenset(),
    ref_arg_intrinsics: frozenset[str] = frozenset(),
) -> list[Lint]:
    """Scan ``source`` for the pitfalls above.

    ``intrinsic_names``: all known intrinsic names (for the shadowing lint).
    ``ref_arg_intrinsics``: intrinsics with a ``~``-first-argument overload (for the
    discarded-result lint); pass ``frozenset()`` to disable the DB-driven lints.
    """
    data = source.encode("utf-8") if isinstance(source, str) else source
    tree = new_parser().parse(data)
    _available, _calls = analyze(data)
    # per-call, position-sensitive boundness (a rebinding in an unrelated scope, or later in
    # this one, does not shield a call): keyed by the call identifier's start point
    bound_at = {(cs.line, cs.col): cs.bound_in_scope for cs in _calls}

    out: list[Lint] = []
    # call NODES by name (not just names): the shadowing lint must compare scopes — a local
    # variable in one function does not shadow an intrinsic another function calls
    call_nodes: dict[str, list] = {}
    lit_bindings: dict[str, list] = {}  # assignments to True/False/None (as variable names)
    _cstack = [tree.root_node]
    while _cstack:
        n = _cstack.pop()
        if n.type == "call" and n.children and n.children[0].type == "identifier":
            call_nodes.setdefault(node_text(n.children[0]), []).append(n)
        elif n.type == "assignment":
            for c in n.children:
                if c.type == ":=":
                    break
                if c.type == "identifier" and node_text(c) in _PY_LITERALS:
                    lit_bindings.setdefault(node_text(c), []).append(n)
        _cstack.extend(n.children)
    shadow_calls = set(call_nodes)

    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        t = node.type

        if t == "expression_statement" and node.children:
            child = node.children[0]
            # `x = 5;` parses as a group-relation statement: legal, silently useless.
            if child.type == "group_relation" and not child.has_error:
                kids = child.children
                if len(kids) >= 2 and kids[0].type == "identifier" and kids[1].type == "=":
                    out.append(
                        _lint(
                            kids[1],
                            "`=` is comparison/relation syntax in Magma — "
                            "assignment is `:=` (did you mean `"
                            f"{node_text(kids[0])} := ...;`?)",
                        )
                    )
            # `Append(L, x);` as a statement: result discarded; the in-place form takes ~L.
            if (
                child.type == "call"
                and child.children
                and child.children[0].type == "identifier"
            ):
                name = node_text(child.children[0])
                ident = child.children[0]
                locally_bound = bound_at.get(
                    (ident.start_point[0], ident.start_point[1]), False
                )
                if name in ref_arg_intrinsics and not locally_bound:
                    has_ref = any(
                        a.type == "unary_operator" and a.children and a.children[0].type == "~"
                        for c in child.children
                        if c.type == "argument_list"
                        for a in c.children
                    )
                    if not has_ref:
                        first_arg = _first_positional_arg_text(child)
                        hint = f"{name}(~{first_arg}, ...)" if first_arg else f"{name}(~...)"
                        out.append(
                            _lint(
                                child,
                                f"the result of `{name}(...)` is discarded — for in-place "
                                f"update use the procedure form `{hint}`",
                            )
                        )

        elif t == "identifier" and not node.is_missing:
            txt = node_text(node)
            if txt in _PY_LITERALS:
                binds = lit_bindings.get(txt, [])
                # suppressed only where a binding actually reaches this reference: inside
                # the binding statement itself, or lexically after it in its scope (same
                # reach rule as the shadowing lint — codex #12 round 15)
                inside = any(b.start_byte <= node.start_byte < b.end_byte for b in binds)
                reached = any(_shadowed_call_in_scope(b, [node]) for b in binds)
                if not inside and not reached:
                    out.append(_lint(node, _PY_LITERALS[txt]))

        elif t == "binary_operator":
            kids = node.children
            # `x.append(...)`: no method-call syntax in Magma.
            if (
                len(kids) >= 3
                and kids[1].type == "."
                and kids[2].type == "call"
                and kids[2].children
                and kids[2].children[0].type == "identifier"
            ):
                fn = node_text(kids[2].children[0])
                recv = node_text(kids[0]) if kids[0].type == "identifier" else "x"
                out.append(
                    _lint(
                        node,
                        f"Magma has no method-call syntax — write `{fn.capitalize()}({recv}, "
                        f"...)` (or the appropriate intrinsic) instead of `{recv}.{fn}(...)`",
                    )
                )

        elif t == "assignment":
            # assignment to an intrinsic name the same file also calls
            if intrinsic_names:
                for c in node.children:
                    if c.type == ":=":
                        break
                    if c.type == "identifier":
                        nm = node_text(c)
                        if (
                            nm in intrinsic_names
                            and nm in shadow_calls
                            and _shadowed_call_in_scope(node, call_nodes[nm])
                        ):
                            out.append(
                                _lint(
                                    c,
                                    f"assignment to `{nm}` shadows the Magma intrinsic of "
                                    "that name, which this file also calls — rename the "
                                    "variable",
                                )
                            )

        elif node.is_missing and t == "identifier":
            # `x == 5` / `2 ** 3` parse with a MISSING identifier squeezed between the two
            # operator characters. Look at the surrounding bytes.
            b = node.start_byte
            if 0 < b < len(data) and data[b - 1 : b] == data[b : b + 1]:
                ch = data[b : b + 1]
                if ch == b"=":
                    out.append(
                        _lint(node, "`==` is not a Magma operator — equality is `eq`")
                    )
                elif ch == b"*":
                    out.append(
                        _lint(node, "`**` is not a Magma operator — exponentiation is `^`")
                    )

        elif node.is_missing and t == ";":
            # `q := a // b;` — the `//` begins a comment, swallowing the `;`.
            prev = node.prev_sibling
            if prev is not None and prev.type == "comment" and node_text(prev).startswith("//"):
                out.append(
                    _lint(
                        prev,
                        "`//` starts a comment in Magma (the rest of this line is not code); "
                        "for integer division use `div`",
                    )
                )

        stack.extend(node.children)

    # de-dup identical (position, message) pairs and sort
    seen: set[tuple[int, int, str]] = set()
    uniq: list[Lint] = []
    for lint in sorted(out, key=lambda d: (d.line, d.col)):
        key = (lint.line, lint.col, lint.message)
        if key not in seen:
            seen.add(key)
            uniq.append(lint)
    return uniq


_CALLABLE_TYPES = frozenset(
    {"function_definition", "procedure_definition", "intrinsic_definition", "constructor"}
)
_LOOP_TYPES = frozenset({"while_statement", "for_statement", "repeat_statement"})


def _shadowed_call_in_scope(assignment_node, calls: list) -> bool:
    """True iff some call could actually execute AFTER the assignment and see it:
    - the call is in the assignment's scope (same callable, or anywhere for top level —
      a function defined earlier captures the intrinsic by value, so only textually-later
      code sees the rebinding), AND
    - the call is textually after the assignment, OR both sit inside a loop that re-runs
      the assignment (an earlier-in-body call executes again on the next iteration)."""
    scope = assignment_node.parent
    while scope is not None and scope.type not in _CALLABLE_TYPES:
        scope = scope.parent
    # loops enclosing the assignment (within its scope): an earlier call inside one of
    # these still breaks on the following iteration
    loop_ids: set[int] = set()
    p = assignment_node.parent
    while p is not None and (scope is None or p.id != scope.id):
        if p.type in _LOOP_TYPES:
            loop_ids.add(p.id)
        p = p.parent
    for cn in calls:
        if scope is not None:
            q = cn.parent
            while q is not None and q.id != scope.id:
                q = q.parent
            if q is None:
                continue  # call lives in a different callable
        if cn.start_byte >= assignment_node.end_byte:
            return True
        q = cn.parent
        while q is not None:
            if q.id in loop_ids:
                return True
            if scope is not None and q.id == scope.id:
                break
            q = q.parent
    return False


def _first_positional_arg_text(call_node) -> str | None:
    for c in call_node.children:
        if c.type == "argument_list":
            for a in c.children:
                if a.type in ("(", ")", ",", ":"):
                    continue
                return node_text(a) if a.type == "identifier" else None
    return None
