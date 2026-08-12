"""Deterministic scoring: run a candidate Magma program and compare to the expected answer.

Scoring is done outside the model loop so a model cannot self-report success. A program is
``correct`` iff it runs cleanly and its output equals (or contains as a token) the expected answer.
"""

from __future__ import annotations

import json
import os
import re

from magma_lsp.magma.diagnostics import parse_diagnostics
from magma_lsp.magma.runner import run_source

PREAMBLE = "SetColumns(0);\n"


def score_program(code: str, expected: str, *, timeout: float = 60.0, strict: bool = False) -> dict:
    res = run_source(PREAMBLE + code + "\n", timeout=timeout)
    out = res.stdout.strip()
    diags = parse_diagnostics(res.stdout)
    errored = bool(diags) or res.timed_out
    exp = expected.strip()
    if strict:
        # exact output, or exact final line — chatty output containing the answer as a
        # stray token does NOT count (weak models exercise the lax path; see eval review)
        last = out.splitlines()[-1].strip() if out else ""
        correct = (not errored) and (out == exp or last == exp)
        return {
            "ran_ok": not errored,
            "correct": correct,
            "timed_out": res.timed_out,
            "output": out[:500],
            "error": diags[0].message if diags else None,
        }
    # exact (after strip) or expected appears as a standalone token in the output
    tokens = set(re.split(r"[\s,\[\]<>]+", out))
    correct = (not errored) and (out == exp or exp in tokens or exp in out.splitlines())
    return {
        "ran_ok": not errored,
        "correct": correct,
        "timed_out": res.timed_out,
        "output": out[:500],
        "error": diags[0].message if diags else None,
    }


def _score_one(args: tuple[dict, str, str, float, bool]) -> dict:
    r, expected, domain, timeout, strict = args
    s = score_program(r.get("code", ""), expected, timeout=timeout, strict=strict)
    return {**r, **s, "expected": expected, "domain": domain}


def score_results(
    results: list[dict],
    truth: dict[str, dict],
    *,
    timeout: float = 120.0,
    workers: int = 48,
    strict: bool = False,
) -> list[dict]:
    """Score each generation by running it in Magma (in parallel across the cores)."""
    from concurrent.futures import ProcessPoolExecutor

    jobs = [
        (r, truth[r["task_id"]]["expected"], truth[r["task_id"]].get("domain", ""), timeout, strict)
        for r in results
        if r["task_id"] in truth
    ]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_score_one, jobs))


def main() -> int:
    import argparse

    here = os.path.dirname(__file__)
    ap = argparse.ArgumentParser()
    ap.add_argument("generations", nargs="?", default=os.path.join(here, "generations.json"))
    ap.add_argument("--truth", default=os.path.join(here, "truth.json"))
    ap.add_argument("--out", default=os.path.join(here, "scored.json"))
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--workers", type=int, default=48)
    ap.add_argument(
        "--strict",
        action="store_true",
        help="require the answer as the exact output or exact final line "
        "(no token-containment credit)",
    )
    args = ap.parse_args()
    with open(args.truth, encoding="utf-8") as fh:
        truth = json.load(fh)
    with open(args.generations, encoding="utf-8") as fh:
        results = json.load(fh)
    scored = score_results(
        results, truth, timeout=args.timeout, workers=args.workers, strict=args.strict
    )
    out_path = args.out
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(scored, fh, indent=2, ensure_ascii=False)
    print(f"scored {len(scored)} -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
