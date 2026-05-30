"""Command-line front-end to the Magma LSP core — the same intelligence the editor server exposes,
usable from a shell (and by an agent driving Magma). A stepping stone toward an MCP front-end.

Subcommands:
  lookup NAME...   intrinsic signature(s) + handbook description (the hover content)
  check FILE       diagnostics: static unknown-intrinsic + Magma syntax/binding (+ runtime)
  run FILE         execute the program in a sandboxed Magma and print its output

Exit code is nonzero when `check` finds errors or `run` fails, so it scripts cleanly.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .analysis.undefined import undefined_intrinsics
from .db.index import SignatureIndex
from .db.store import newest_cached_db
from .handbook import HandbookIndex
from .magma.runner import find_magma, run_source
from .magma.validate import execution_check, syntax_check


def _load_index() -> SignatureIndex | None:
    p = newest_cached_db()
    return SignatureIndex.from_path(p) if p else None


def _default_handbook() -> HandbookIndex | None:
    resolved = find_magma(None)
    import os

    base = os.path.dirname(os.path.realpath(resolved)) if resolved else "/opt/magma"
    hb = os.path.join(base, "doc", "html")
    return HandbookIndex.load(hb) if os.path.isdir(hb) else None


def cmd_lookup(args: argparse.Namespace) -> int:
    idx = _load_index()
    if idx is None:
        print("error: no signature DB; run magma-lsp-build-db", file=sys.stderr)
        return 2
    hb = _default_handbook() if not args.no_handbook else None
    rc = 0
    for name in args.names:
        intr = idx.lookup(name)
        if intr is None:
            sugg = idx.complete(name, limit=8)
            print(f"# {name}: not a known intrinsic", end="")
            print(f" (did you mean: {', '.join(sugg)}?)" if sugg else "")
            rc = 1
            continue
        print(f"# {name}")
        print(idx.hover_markdown(name))
        if hb is not None:
            doc = hb.doc_markdown(name)
            if doc:
                print(f"\n{doc}")
        print()
    return rc


def _fmt_diags(diags, source: str) -> list[str]:
    lines = source.splitlines()
    out = []
    for d in diags:
        loc = "" if getattr(d, "positionless", False) else f" (line {d.line}, col {d.col})"
        src = lines[d.line - 1].strip() if 0 < d.line <= len(lines) else ""
        out.append(f"  [{d.severity}]{loc}: {d.message}" + (f"\n      >> {src}" if src else ""))
    return out


def cmd_check(args: argparse.Namespace) -> int:
    source = Path(args.file).read_text(encoding="utf-8")
    idx = _load_index()
    problems: list[str] = []

    if idx is not None:
        names = frozenset(idx.db.intrinsics)
        for lint in undefined_intrinsics(source, names):
            problems.append(
                f"  [error] (line {lint.line + 1}, col {lint.col + 1}): {lint.message}"
            )

    syn = syntax_check(source)
    problems += _fmt_diags(syn.diagnostics, source)
    if not syn.diagnostics and not problems and args.execute:
        ex = execution_check(source)
        problems += _fmt_diags(ex.diagnostics, source)

    if problems:
        print("FAIL: issues found:")
        print("\n".join(problems))
        return 1
    print("OK: no static or Magma errors detected.")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    source = Path(args.file).read_text(encoding="utf-8")
    res = run_source(source, timeout=args.timeout)
    sys.stdout.write(res.stdout)
    if not res.stdout.endswith("\n"):
        sys.stdout.write("\n")
    if res.timed_out:
        print(f"(timed out after {args.timeout}s)", file=sys.stderr)
        return 124
    return res.returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="magma-lsp-cli", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    lk = sub.add_parser("lookup", help="intrinsic signature(s) + handbook doc")
    lk.add_argument("names", nargs="+")
    lk.add_argument("--no-handbook", action="store_true")
    lk.set_defaults(func=cmd_lookup)

    ck = sub.add_parser("check", help="static + Magma diagnostics for a .m file")
    ck.add_argument("file")
    ck.add_argument("--execute", action="store_true", help="also run an execution pass")
    ck.set_defaults(func=cmd_check)

    rn = sub.add_parser("run", help="execute a .m file in sandboxed Magma")
    rn.add_argument("file")
    rn.add_argument("--timeout", type=float, default=30.0)
    rn.set_defaults(func=cmd_run)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
