"""Corpus-wide robustness scan of the tree-sitter parser and the signature extractor.

Parses and extracts every ``.m`` file under the Magma package root in parallel, surfacing:
- extractor exceptions (hard bugs),
- tree-sitter parse errors (grammar gaps),
- degenerate extractions (empty names, no type info),

and aggregate counts. Run: ``uv run python validation/scan_corpus.py [package_root]``.
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

import tree_sitter_magma as tsm
from tree_sitter import Language, Parser

from magma_lsp.db.package import extract_file

DEFAULT_ROOT = "/opt/magma/package"


def _scan_one(path: str) -> dict:
    rec: dict = {
        "path": path,
        "intrinsics": 0,
        "parse_error": False,
        "error_nodes": 0,
        "empty_names": 0,
        "no_type_args": 0,
        "exception": None,
    }
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        lang = Language(tsm.language())
        tree = Parser(lang).parse(data)
        rec["parse_error"] = tree.root_node.has_error
        if tree.root_node.has_error:
            stack = [tree.root_node]
            n = 0
            while stack:
                node = stack.pop()
                if node.type == "ERROR" or node.is_missing:
                    n += 1
                else:
                    stack.extend(node.children)
            rec["error_nodes"] = n
        sigs = extract_file(path)
        rec["intrinsics"] = len(sigs)
        for s in sigs:
            if not s.name or s.name == "?":
                rec["empty_names"] += 1
    except Exception:
        rec["exception"] = traceback.format_exc().splitlines()[-1]
    return rec


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    root = argv[0] if argv else DEFAULT_ROOT
    files = [
        os.path.join(dp, fn)
        for dp, _d, fns in os.walk(root)
        for fn in fns
        if fn.endswith(".m")
    ]
    print(f"scanning {len(files)} .m files under {root} with {os.cpu_count()} cores ...")

    t0 = time.time()
    recs: list[dict] = []
    with ProcessPoolExecutor(max_workers=os.cpu_count()) as pool:
        for rec in pool.map(_scan_one, files, chunksize=16):
            recs.append(rec)
    dt = time.time() - t0

    total_intr = sum(r["intrinsics"] for r in recs)
    exceptions = [r for r in recs if r["exception"]]
    parse_errs = [r for r in recs if r["parse_error"]]
    empty = [r for r in recs if r["empty_names"]]

    print(f"\n=== results ({dt:.1f}s) ===")
    print(f"files: {len(recs)}")
    print(f"intrinsics extracted: {total_intr}")
    print(f"files with extractor EXCEPTION: {len(exceptions)}")
    print(f"files with tree-sitter parse error: {len(parse_errs)} "
          f"({100 * len(parse_errs) / max(1, len(recs)):.1f}%)")
    print(f"files with empty/'?' intrinsic names: {len(empty)}")

    if exceptions:
        print("\n--- EXCEPTIONS (first 15) ---")
        for r in exceptions[:15]:
            print(f"  {r['path']}: {r['exception']}")
    if parse_errs:
        worst = sorted(parse_errs, key=lambda r: r["error_nodes"], reverse=True)[:15]
        print("\n--- worst parse-error files (by error-node count) ---")
        for r in worst:
            print(f"  {r['error_nodes']:4d} errs  {r['intrinsics']:3d} intr  {r['path']}")
        by_dir = Counter(os.path.dirname(r["path"]).replace(root, "") for r in parse_errs)
        print("\n--- parse errors by area (top 12) ---")
        for d, c in by_dir.most_common(12):
            print(f"  {c:4d}  {d or '/'}")
    if empty:
        print("\n--- files with empty names (first 10) ---")
        for r in empty[:10]:
            print(f"  {r['empty_names']} in {r['path']}")

    # Non-zero exit if hard bugs (exceptions) found.
    return 1 if exceptions else 0


if __name__ == "__main__":
    raise SystemExit(main())
