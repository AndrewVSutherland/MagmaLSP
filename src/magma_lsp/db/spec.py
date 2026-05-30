"""Resolve the set of package ``.m`` files attached by a Magma ``.spec`` tree.

Only intrinsics in spec-attached files are available in a default Magma session; extracting from
*every* ``.m`` file over-includes intrinsics from non-attached packages (CompTree, test files, …),
which Magma does not register. Filtering the package extraction to this set makes the DB match
reality (validated: ~100% of names then confirmed by Magma).

Spec grammar (declarative; CLAUDE.md §7):
- ``NAME { ... }``     — subdirectory NAME; its block lists contents.
- ``{ ... }``          — a bare block in the current directory (an included spec's body).
- ``file.m``           — attach ``<curdir>/file.m``.
- ``+other.spec``      — include ``<curdir>/other.spec`` (parsed with the same current directory).
- ``# ...``            — line comment.
"""

from __future__ import annotations

import os

DEFAULT_SPEC = "/opt/magma/package/spec"


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for line in text.splitlines():
        line = line.split("#", 1)[0]
        for chunk in line.split():
            # separate attached braces, e.g. "Foo{" or "}"
            buf = ""
            for ch in chunk:
                if ch in "{}":
                    if buf:
                        tokens.append(buf)
                        buf = ""
                    tokens.append(ch)
                else:
                    buf += ch
            if buf:
                tokens.append(buf)
    return tokens


def _parse(tokens: list[str], i: int, curdir: str, out: set[str], seen: set[str]) -> int:
    """Parse entries from index i until a matching '}' (or end); return next index."""
    while i < len(tokens):
        tok = tokens[i]
        if tok == "}":
            return i + 1
        if tok == "{":
            i = _parse(tokens, i + 1, curdir, out, seen)
            continue
        # a word: directory (if followed by '{'), include, or file
        if i + 1 < len(tokens) and tokens[i + 1] == "{":
            i = _parse(tokens, i + 2, os.path.join(curdir, tok), out, seen)
            continue
        if tok.startswith("+"):
            # Include path may contain subdirectories (e.g. `+FldNum/FldNum.spec`); its entries
            # are resolved relative to the included spec file's *own* directory (set in
            # _parse_spec_file), not the current directory.
            _parse_spec_file(os.path.join(curdir, tok[1:]), out, seen)
        elif tok.endswith(".m"):
            out.add(os.path.normpath(os.path.join(curdir, tok)))
        i += 1
    return i


def _parse_spec_file(spec_path: str, out: set[str], seen: set[str]) -> None:
    real = os.path.normpath(spec_path)
    if real in seen or not os.path.isfile(real):
        return
    seen.add(real)
    with open(real, encoding="utf-8", errors="replace") as fh:
        tokens = _tokenize(fh.read())
    _parse(tokens, 0, os.path.dirname(real), out, seen)


def attached_files(spec_path: str = DEFAULT_SPEC) -> set[str]:
    """Return the set of absolute ``.m`` paths attached by the spec tree rooted at ``spec_path``."""
    out: set[str] = set()
    seen: set[str] = set()
    _parse_spec_file(os.path.abspath(spec_path), out, seen)
    return out
