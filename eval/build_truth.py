"""Run each task's reference solution in Magma to capture the expected output (ground truth).

Writes eval/truth.json: {id: {prompt, domain, expected}}. Re-run when tasks change.
Run: ``uv run python eval/build_truth.py``.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from tasks import TASKS

from magma_lsp.magma.runner import run_source

PREAMBLE = "SetColumns(0);\n"


def main() -> int:
    truth: dict[str, dict] = {}
    bad = 0
    for task in TASKS:
        res = run_source(PREAMBLE + task["reference"] + "\n", timeout=60.0)
        out = res.stdout.strip()
        # reference must run cleanly and print something
        if res.returncode not in (0,) or not out or "error" in out.lower():
            print(f"[BAD REF] {task['id']}: rc={res.returncode}\n{out[:300]}", file=sys.stderr)
            bad += 1
            continue
        truth[task["id"]] = {
            "prompt": task["prompt"],
            "domain": task["domain"],
            "expected": out,
        }
        print(f"  {task['id']:20} -> {out!r}")
    out_path = os.path.join(os.path.dirname(__file__), "truth.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(truth, fh, indent=2, ensure_ascii=False)
    print(f"\nwrote {out_path}: {len(truth)} tasks, {bad} bad references", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
