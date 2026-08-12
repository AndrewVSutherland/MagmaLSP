"""False-positive audit of the static arity check (analysis/arity.py).

The package corpus and the handbook worked examples are correct Magma, so every arity flag on
them is (presumed) a false positive — either a DB signature gap or a check bug. Run after any
extractor/merge change; the check is only shippable while this stays ~0.

Usage: uv run python validation/arity_fp.py [--limit N]
"""

from __future__ import annotations

import argparse
import time
from concurrent.futures import ProcessPoolExecutor

from diff_diagnostics import extract_examples

from magma_lsp.analysis.arity import arity_problems
from magma_lsp.db.index import SignatureIndex
from magma_lsp.db.spec import attached_files
from magma_lsp.db.store import newest_cached_db

_INDEX: SignatureIndex | None = None


def _init() -> None:
    global _INDEX
    _INDEX = SignatureIndex.from_path(newest_cached_db())


def _check_one(item: tuple[str, str]) -> tuple[str, list[str]]:
    src_id, code = item
    assert _INDEX is not None
    lints = arity_problems(code, _INDEX.arities)
    return src_id, [f"{lint.line + 1}:{lint.col + 1} {lint.message}" for lint in lints]


def _package_items() -> list[tuple[str, str]]:
    out = []
    for path in sorted(attached_files()):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                out.append((path, fh.read()))
        except OSError:
            continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()

    _init()
    items = _package_items() + extract_examples()
    if args.limit:
        items = items[: args.limit]
    print(f"arity FP audit over {len(items)} sources ...")

    t0 = time.time()
    n_flagged_sources = 0
    n_flags = 0
    samples: list[str] = []
    by_name: dict[str, int] = {}
    with ProcessPoolExecutor(max_workers=args.workers, initializer=_init) as pool:
        for src_id, flags in pool.map(_check_one, items, chunksize=8):
            if flags:
                n_flagged_sources += 1
                n_flags += len(flags)
                for f in flags[:2]:
                    if len(samples) < 40:
                        samples.append(f"{src_id}: {f}")
                for f in flags:
                    name = f.split("'")[1] if "'" in f else "?"
                    by_name[name] = by_name.get(name, 0) + 1
    dt = time.time() - t0

    print(f"\n=== results ({dt:.1f}s) ===")
    print(f"sources: {len(items)}; flagged sources: {n_flagged_sources}; total flags: {n_flags}")
    if by_name:
        print("\n--- most-flagged intrinsics (DB gaps or check bugs) ---")
        for n, c in sorted(by_name.items(), key=lambda kv: -kv[1])[:25]:
            print(f"  {c:5d}  {n}")
        print("\n--- samples ---")
        for s in samples[:25]:
            print(f"  {s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
