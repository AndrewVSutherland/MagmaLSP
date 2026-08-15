"""Performance benchmarks: DB load, query latency, static-analysis throughput, Magma capacity.

Informs whether any path needs the C optimization (design.md §8). Run:
``uv run python validation/bench.py``.
"""

from __future__ import annotations

import os
import statistics
import time
from concurrent.futures import ProcessPoolExecutor

from magma_lsp.analysis.lints import unused_variables
from magma_lsp.analysis.scope import analyze
from magma_lsp.analysis.symbols import document_symbols
from magma_lsp.analysis.undefined import undefined_intrinsics
from magma_lsp.db.index import SignatureIndex
from magma_lsp.db.store import newest_cached_db
from magma_lsp.magma.validate import syntax_check

SAMPLE = """
intrinsic MyThing(x::RngIntElt, S::SeqEnum[RngIntElt] : Bound := 10) -> RngIntElt
{ Does a thing. }
    total := 0;
    for i in [1..#S] do
        total +:= S[i] * x;
    end for;
    f := function(n) return n^2 + Factorial(n); end function;
    vals := [ f(j) : j in [1..Bound] ];
    E := EllipticCurve([0, total]);
    return total + &+vals + #Points(E);
end intrinsic;
""" * 6  # ~80 lines, realistic working file


def _ms(xs: list[float]) -> str:
    xs = sorted(xs)
    med = statistics.median(xs) * 1000
    p95 = xs[int(0.95 * (len(xs) - 1))] * 1000
    return f"median {med:.3f} ms, p95 {p95:.3f} ms"


def _time(fn, n=200) -> list[float]:
    out = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        out.append(time.perf_counter() - t)
    return out


def _magma_check(_i: int) -> float:
    t = time.perf_counter()
    syntax_check("E := EllipticCurve([0,1]);\np := Factorial(20);\n", timeout=15)
    return time.perf_counter() - t


def main() -> int:
    db_path = newest_cached_db()
    print(f"DB: {db_path}")
    t = time.perf_counter()
    idx = SignatureIndex.from_path(db_path)
    load_s = time.perf_counter() - t
    names = frozenset(idx.db.intrinsics)
    print(f"DB load: {load_s * 1000:.0f} ms ({len(names)} names)\n")

    print("=== query latency ===")
    print(f"hover(EllipticCurve):     {_ms(_time(lambda: idx.hover_markdown('EllipticCurve')))}")
    print(f"completion('Ell'):        {_ms(_time(lambda: idx.complete('Ell')))}")
    print(f"definition(EllipticCurve):{_ms(_time(lambda: idx.definition('EllipticCurve')))}")
    print(f"search_symbols('Curve'):  {_ms(_time(lambda: idx.search_symbols('Curve'), n=50))}")

    print("\n=== static analysis on a ~80-line file ===")
    print(f"document_symbols:    {_ms(_time(lambda: document_symbols(SAMPLE)))}")
    print(f"analyze (scope):     {_ms(_time(lambda: analyze(SAMPLE)))}")
    print(f"undefined_intrinsics:{_ms(_time(lambda: undefined_intrinsics(SAMPLE, names)))}")
    print(f"unused_variables:    {_ms(_time(lambda: unused_variables(SAMPLE)))}")

    print("\n=== Magma syntax check (subprocess) ===")
    single = [_magma_check(0) for _ in range(10)]
    print(f"single check: {_ms(single)}")
    n = 512
    workers = min(64, os.cpu_count() or 8)
    t = time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        list(pool.map(_magma_check, range(n)))
    dt = time.perf_counter() - t
    print(f"{n} checks on {workers} workers: {dt:.1f}s -> {n / dt:.0f} checks/sec")

    print("\n=== Magma execution pass (bwrap sandbox on vs off) ===")
    from magma_lsp.magma.runner import NO_SANDBOX_ENV, sandbox_state
    from magma_lsp.magma.validate import execution_check

    prior = os.environ.pop(NO_SANDBOX_ENV, None)
    print(f"sandbox state: {sandbox_state()}")
    on = _time(lambda: execution_check("print 1;", timeout=15), n=10)
    os.environ[NO_SANDBOX_ENV] = "1"
    off = _time(lambda: execution_check("print 1;", timeout=15), n=10)
    if prior is None:
        del os.environ[NO_SANDBOX_ENV]
    else:
        os.environ[NO_SANDBOX_ENV] = prior
    print(f"execution check, sandboxed:   {_ms(on)}")
    print(f"execution check, no sandbox:  {_ms(off)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
