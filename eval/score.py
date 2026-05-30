"""Deterministic scoring: run a candidate Magma program and compare to the expected answer.

Scoring is done outside the model loop so a model cannot self-report success. A program is
``correct`` iff it runs cleanly and its output equals (or contains as a token) the expected answer.
"""

from __future__ import annotations

import json
import os
import re
import sys

from magma_lsp.magma.diagnostics import parse_diagnostics
from magma_lsp.magma.runner import run_source

PREAMBLE = "SetColumns(0);\n"


def score_program(code: str, expected: str, *, timeout: float = 60.0) -> dict:
    res = run_source(PREAMBLE + code + "\n", timeout=timeout)
    out = res.stdout.strip()
    diags = parse_diagnostics(res.stdout)
    errored = bool(diags) or res.timed_out
    exp = expected.strip()
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


def score_results(results: list[dict], truth: dict[str, dict]) -> list[dict]:
    """results: [{task_id, condition, code, ...}] -> adds scoring fields."""
    scored = []
    for r in results:
        t = truth.get(r["task_id"])
        if t is None:
            continue
        s = score_program(r.get("code", ""), t["expected"])
        scored.append({**r, **s, "expected": t["expected"], "domain": t["domain"]})
    return scored


def main() -> int:
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "truth.json"), encoding="utf-8") as fh:
        truth = json.load(fh)
    gen_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "generations.json")
    with open(gen_path, encoding="utf-8") as fh:
        results = json.load(fh)
    scored = score_results(results, truth)
    out_path = os.path.join(here, "scored.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(scored, fh, indent=2, ensure_ascii=False)
    print(f"scored {len(scored)} -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
