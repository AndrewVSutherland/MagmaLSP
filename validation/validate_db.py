"""Validate the signature DB against real Magma, in parallel across the cores.

Every name in the DB should be a genuine intrinsic. We probe all of them with ``name;`` (the same
mechanism as ``db/probe.py``), splitting the work across many concurrent Magma processes, and report
any DB name Magma does *not* confirm as an intrinsic — those would be extraction artifacts or
package-locals wrongly admitted.

Run: ``uv run python validation/validate_db.py``.
"""

from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

from magma_lsp.db.index import SignatureIndex
from magma_lsp.db.probe import build_probe_script, parse_probe_output
from magma_lsp.db.store import newest_cached_db
from magma_lsp.magma.runner import run_source

CHUNK = 250
MAX_WORKERS = 64  # each worker spawns a single-threaded Magma; keep well under 192


def _confirm_chunk(names: list[str]) -> list[str]:
    res = run_source(build_probe_script(names), timeout=120.0)
    return list(parse_probe_output(res.stdout).keys())


def _is_operator(name: str) -> bool:
    return name.startswith("'") and name.endswith("'")


def main() -> int:
    db_path = newest_cached_db()
    if db_path is None:
        print("no DB found; run magma-lsp-build-db first", file=sys.stderr)
        return 2
    idx = SignatureIndex.from_path(db_path)
    names = list(idx.db.intrinsics)
    # Operators (e.g. '+') can't be probed by `eval("name;")`; validate them separately/skip.
    barewords = [n for n in names if not _is_operator(n)]
    operators = [n for n in names if _is_operator(n)]
    print(f"DB {idx.version}: {len(names)} names ({len(barewords)} barewords, "
          f"{len(operators)} operators); probing barewords on {MAX_WORKERS} workers ...")

    chunks = [barewords[i : i + CHUNK] for i in range(0, len(barewords), CHUNK)]
    t0 = time.time()
    confirmed: set[str] = set()
    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for got in pool.map(_confirm_chunk, chunks):
            confirmed.update(got)
    dt = time.time() - t0

    unconfirmed = [n for n in barewords if n not in confirmed]
    print(f"\n=== results ({dt:.1f}s) ===")
    print(f"barewords confirmed as intrinsics by Magma: {len(confirmed)}/{len(barewords)} "
          f"({100 * len(confirmed) / max(1, len(barewords)):.2f}%)")
    print(f"NOT confirmed: {len(unconfirmed)}")
    if unconfirmed:
        # categorize by provenance in the DB
        pkg = [n for n in unconfirmed if any(s.kind == "package" for s in idx.signatures(n))]
        ker = [n for n in unconfirmed if n not in pkg]
        print(f"  of which have a package-sourced signature: {len(pkg)}")
        print(f"  kernel-only: {len(ker)}")
        print("\n--- first 40 unconfirmed names ---")
        for n in unconfirmed[:40]:
            kinds = {s.kind for s in idx.signatures(n)}
            src = next((s.source.file for s in idx.signatures(n) if s.source), None)
            print(f"  {n:30} kinds={kinds} src={src}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
