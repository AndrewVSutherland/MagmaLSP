"""Shared tree-sitter-magma parser access.

One Language is created per process and reused; Parser objects are cheap but not thread-safe,
so callers that parse concurrently should each hold their own via `new_parser()`.
"""

from __future__ import annotations

from functools import lru_cache

import tree_sitter_magma as tsm
from tree_sitter import Language, Node, Parser


@lru_cache(maxsize=1)
def get_language() -> Language:
    return Language(tsm.language())


def new_parser() -> Parser:
    return Parser(get_language())


def node_text(node: Node) -> str:
    return node.text.decode("utf-8", "replace")


def named_child_after(node: Node, kw_type: str) -> Node | None:
    """Return the child immediately following the first child of type `kw_type`."""
    prev_was_kw = False
    for child in node.children:
        if prev_was_kw:
            return child
        if child.type == kw_type:
            prev_was_kw = True
    return None
