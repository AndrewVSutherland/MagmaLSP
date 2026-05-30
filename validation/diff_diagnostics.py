"""Differential test of the static unknown-intrinsic check against real Magma.

Handbook worked examples are real, idiomatic Magma programs. We extract their input code (the
``> ``-prefixed REPL lines in ``<PRE>`` blocks), then for each example compare:
- our static ``undefined_intrinsics`` verdict, against
- Magma's authoritative binding pass (``syntax_check``),
restricted to undefined-name disagreements. Examples are clean, so both should report ~nothing;
any divergence is a static false positive (we flag, Magma doesn't) or false negative.

Runs one Magma process per example across the cores. ``--limit N`` for a quick pass.
"""

from __future__ import annotations

import argparse
import html
import os
import re
import time
from concurrent.futures import ProcessPoolExecutor

from magma_lsp.analysis.undefined import undefined_intrinsics
from magma_lsp.db.index import SignatureIndex
from magma_lsp.db.store import newest_cached_db
from magma_lsp.magma.validate import syntax_check

HTML_DIR = "/opt/magma/doc/html"
PRE_RE = re.compile(r"<PRE>(.*?)</PRE>", re.S)
_NAMES: frozenset[str] = frozenset()


def extract_examples(html_dir: str = HTML_DIR) -> list[tuple[str, str]]:
    """Return (file, code) — all REPL input in a chapter page concatenated in order.

    PRE blocks within a chapter share REPL session state (a name defined in one block is used in
    a later one), so we concatenate per file to keep each program self-contained. Files with no
    REPL input are skipped.
    """
    out: list[tuple[str, str]] = []
    for fn in sorted(os.listdir(html_dir)):
        if not (fn.startswith("text") and fn.endswith(".htm")):
            continue
        path = os.path.join(html_dir, fn)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        lines: list[str] = []
        for m in PRE_RE.finditer(text):
            for raw in m.group(1).splitlines():
                s = html.unescape(raw).rstrip()
                if s.startswith("> "):
                    lines.append(s[2:])
                elif s.startswith(">>"):
                    lines.append(s[2:].lstrip())
        if lines:
            out.append((fn, "\n".join(lines) + "\n"))
    return out


def _diff_one(item: tuple[str, str]) -> dict:
    ex_id, code = item
    static = {lint.message.split("'")[1] for lint in undefined_intrinsics(code, _NAMES)}
    magma_undef: set[str] = set()
    try:
        res = syntax_check(code, timeout=15.0)
        for d in res.diagnostics:
            m = re.search(r"Identifier '([^']+)' has not been declared", d.message)
            if m:
                magma_undef.add(m.group(1))
    except Exception as exc:
        return {"id": ex_id, "error": str(exc), "fp": [], "fn": []}
    return {
        "id": ex_id,
        "fp": sorted(static - magma_undef),  # we flag, Magma doesn't
        "fn": sorted(magma_undef - static),  # Magma flags, we miss
    }


def _init_names() -> None:
    global _NAMES
    _NAMES = frozenset(SignatureIndex.from_path(newest_cached_db()).db.intrinsics)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=64)
    args = ap.parse_args()

    _init_names()
    examples = extract_examples()
    if args.limit:
        examples = examples[: args.limit]
    print(f"differential check on {len(examples)} handbook examples, {args.workers} workers ...")

    t0 = time.time()
    results: list[dict] = []
    with ProcessPoolExecutor(max_workers=args.workers, initializer=_init_names) as pool:
        for r in pool.map(_diff_one, examples, chunksize=8):
            results.append(r)
    dt = time.time() - t0

    fp = [r for r in results if r.get("fp")]
    fn = [r for r in results if r.get("fn")]
    errs = [r for r in results if r.get("error")]
    fp_names: dict[str, int] = {}
    for r in fp:
        for n in r["fp"]:
            fp_names[n] = fp_names.get(n, 0) + 1

    print(f"\n=== results ({dt:.1f}s) ===")
    print(f"examples: {len(results)}")
    print(f"examples with a static FALSE POSITIVE (we flag, Magma doesn't): {len(fp)}")
    print(f"examples with a static FALSE NEGATIVE (Magma flags undefined, we miss): {len(fn)}")
    print(f"examples Magma errored on (setup/exec issues, ignore): {len(errs)}")
    if fp_names:
        print("\n--- most common false-positive names ---")
        for n, c in sorted(fp_names.items(), key=lambda kv: -kv[1])[:25]:
            print(f"  {c:4d}  {n}")
        print("\n--- sample FP examples ---")
        for r in fp[:8]:
            print(f"  {r['id']}: {r['fp']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
