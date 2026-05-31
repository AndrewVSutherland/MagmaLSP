"""Command-line front-end to the Magma LSP core — the same intelligence the editor server exposes,
usable from a shell (and by an agent driving Magma). Sibling of the MCP front-end
(``magma_lsp.mcp_server``); both are thin adapters over ``magma_lsp.frontend`` (CLAUDE.md §10).

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

from . import frontend


def cmd_lookup(args: argparse.Namespace) -> int:
    if frontend.load_index() is None:
        print("error: no signature DB; run magma-lsp-build-db", file=sys.stderr)
        return 2
    res = frontend.lookup(args.names, handbook=not args.no_handbook)
    print(res.text)
    return 0 if res.all_found else 1


def cmd_check(args: argparse.Namespace) -> int:
    outcome = frontend.check(Path(args.file).read_text(encoding="utf-8"), execute=args.execute)
    print(outcome.report)
    return 0 if outcome.ok else 1


def cmd_run(args: argparse.Namespace) -> int:
    res = frontend.run(Path(args.file).read_text(encoding="utf-8"), timeout=args.timeout)
    sys.stdout.write(res.output)
    if not res.output.endswith("\n"):
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
