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
    bound_in_scope: bool = False  # name (re)bound in a scope enclosing the call, BEFORE it


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


_SCOPE_TYPES = frozenset(
    {"function_definition", "procedure_definition", "intrinsic_definition", "constructor"}
)


def analyze(source: bytes | str) -> tuple[set[str], list[CallSite]]:
    """``available`` is the document-wide union of bound names (the undefined pass's view).
    Each ``CallSite`` additionally records ``bound_in_scope``: whether the name was bound in
    a lexical scope enclosing the call at the point of the call — a later same-scope binding
    or a binding local to a *different* callable does not count (Magma has no hoisting)."""
    data = source.encode("utf-8") if isinstance(source, str) else source
    tree = new_parser().parse(data)

    available: set[str] = set()
    calls: list[CallSite] = []

    def bind(name: str, scopes: tuple[set[str], ...]) -> None:
        scopes[-1].add(name)
        available.add(name)

    class _DeferredBind:
        """Sentinel popped AFTER an assignment's subtree: `W := W(1,2);` evaluates its RHS
        before the name is bound, so the RHS call must not see the new binding."""

        __slots__ = ("names",)

        def __init__(self, names: list[str]) -> None:
            self.names = names

    # pre-order walk (children pushed reversed) with a chain of shared scope sets: bindings
    # made in an earlier sibling's subtree at the same level are visible to later siblings
    stack: list[tuple[object, tuple[set[str], ...]]] = [(tree.root_node, (set(),))]
    while stack:
        node, scopes = stack.pop()
        if isinstance(node, _DeferredBind):
            for nm in node.names:
                bind(nm, scopes)
            continue
        t = node.type

        if t == "call" and node.children and node.children[0].type == "identifier":
            ident = node.children[0]
            name = ident.text.decode("utf-8", "replace")
            sr, sc = ident.start_point
            _er, ec = ident.end_point
            n_args, has_ref = _call_args(node)
            bound = any(name in s for s in scopes)
            calls.append(CallSite(name, sr, sc, ec, n_args, has_ref, bound))
        elif t in ("function_definition", "procedure_definition", "intrinsic_definition"):
            name = _named_def(node)
            if name:
                bind(name, scopes)
        elif t == "assignment":
            # bind targets AFTER the RHS subtree (deferred sentinel below): Magma evaluates
            # the RHS first, so its calls still resolve to the old meaning of the name
            targets = []
            for c in node.children:
                if c.type == ":=":
                    break
                if c.type == "identifier":
                    targets.append(c.text.decode("utf-8", "replace"))
            if targets:
                stack.append((_DeferredBind(targets), scopes))
        elif t == "for_quantifier":
            for c in node.children:
                if c.type == "identifier":
                    bind(c.text.decode("utf-8", "replace"), scopes)
                    break
        elif t == "in":
            # Binder of a for-loop (`for phi in A`), comprehension (`[ e : c in C ]`), or
            # quantifier (`forall x in S`): the identifier immediately before `in` is bound.
            # (A membership test `x in S` harmlessly re-adds an already-defined name.)
            prev = node.prev_sibling
            if prev is not None and prev.type == "identifier":
                bind(prev.text.decode("utf-8", "replace"), scopes)
        elif t in _BIND_LISTS:
            for c in node.children:
                if c.type == "identifier":
                    bind(c.text.decode("utf-8", "replace"), scopes)
        elif t == "where_expression":
            # `expr where x is ...` / `where x := ...`: identifiers before `is`/`:=` are bound
            for c in node.children:
                if c.type in ("is", ":="):
                    break
                if c.type == "identifier":
                    bind(c.text.decode("utf-8", "replace"), scopes)

        if t in _SCOPE_TYPES:
            new_scope: set[str] = set()
            if t == "constructor":
                # func< x, y | body >: the formals are bare identifiers between `<` and the
                # body — seed them so body calls through a formal aren't taken for intrinsics
                seen_lt = False
                for c in node.children:
                    if c.type == "<":
                        seen_lt = True
                    elif c.type == "constructor_elements":
                        break
                    elif seen_lt and c.type == "identifier":
                        nm = c.text.decode("utf-8", "replace")
                        new_scope.add(nm)
                        available.add(nm)
            child_scopes: tuple[set[str], ...] = (*scopes, new_scope)
        else:
            # Comprehension/quantifier binders (`[expr : x in S]`) and where-bindings
            # (`expr where x is v`) appear AFTER the expression they govern in the parse
            # tree; prebind them in a scope local to this node so the expression's calls
            # see them (codex #12 round 15).
            pre: set[str] | None = None
            for c in node.children:
                if c.type == "iter_vars":
                    for iv in c.children:
                        if iv.type != "iter_var":
                            continue
                        for k in iv.children:
                            if k.type == "in":
                                break
                            if k.type == "identifier":
                                pre = pre if pre is not None else set()
                                nm = k.text.decode("utf-8", "replace")
                                pre.add(nm)
                                available.add(nm)
            if t == "where_expression":
                for c in node.children:
                    if c.type in ("is", ":="):
                        break
                    if c.type == "identifier":
                        pre = pre if pre is not None else set()
                        nm = c.text.decode("utf-8", "replace")
                        pre.add(nm)
                        available.add(nm)
            child_scopes = (*scopes, pre) if pre is not None else scopes
        for c in reversed(node.children):
            stack.append((c, child_scopes))

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


_MAX_LOAD_FILES = 100  # runaway/pathological load-chain backstop


def load_analysis(source: bytes | str, base_dir: str | None) -> tuple[set[str], int, set[str]]:
    """Names defined by the files the document ``load``s, followed transitively.

    Magma's ``load`` is textual inclusion, so a loaded file's own ``load`` directives run too
    and their definitions become available to the top-level document. Every relative target —
    including nested ones — is resolved against the *same* ``base_dir``: Magma resolves load
    paths against the process cwd, not the loading file's directory (verified on 2.29-9).
    Cycles are tolerated (each file is read once).

    Returns ``(names, n_unresolved, resolved_paths)``; an unresolved load means undefined-name
    checking for the document is unreliable (the loaded file could define anything), and
    ``resolved_paths`` holds the realpaths of every successfully read file — the set of files
    whose error blocks an execution pass should trust. Chains longer than ``_MAX_LOAD_FILES``
    are cut off and counted as unresolved rather than silently ignored.
    """
    names: set[str] = set()
    unresolved = 0
    resolved_paths: set[str] = set()
    queue = list(load_targets(source))
    seen: set[str] = set()
    while queue:
        target = queue.pop()
        path = target if os.path.isabs(target) else os.path.join(base_dir or ".", target)
        real = os.path.realpath(path)
        if real in seen:
            continue
        if len(seen) >= _MAX_LOAD_FILES:
            unresolved += 1
            break
        seen.add(real)
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            unresolved += 1
            continue
        try:
            names |= defined_symbols(data)
            queue.extend(load_targets(data))
            resolved_paths.add(real)
        except Exception:
            unresolved += 1
    return names, unresolved, resolved_paths


def load_defined_symbols(source: bytes | str, base_dir: str | None) -> tuple[set[str], int]:
    """``load_analysis`` without the path set (see there for semantics)."""
    names, unresolved, _paths = load_analysis(source, base_dir)
    return names, unresolved


def defined_symbols(source: bytes | str) -> set[str]:
    """Names a file makes available to *other* files in the same project: named
    function/procedure/intrinsic definitions, ``forward`` declarations, and assignment targets
    (which cover ``F := function ...`` / ``F := func< ... >`` helpers). Deliberately generous —
    used to suppress cross-file "undefined" false positives in multi-file packages.

    Callable bodies are NOT descended into: a name assigned inside a function/procedure/
    intrinsic is local and does not escape the file (verified on 2.29-9 — a top-level call to
    it fails with "has not been declared"). Assignments inside top-level control flow
    (``if``/``for``/...) DO bind at load time and are kept.
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
            continue  # body is local scope: nothing inside escapes to the file level
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
