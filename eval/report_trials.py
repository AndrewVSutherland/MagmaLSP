"""Trials-aware report: aggregate multi-trial scored results by (task, condition).

Reads a scored.json whose entries carry a `trial` field. Reports, per condition:
- pass@1 = fraction of all (task, trial) runs that were correct,
- avg task success = mean over tasks of (correct trials / trials),
- solve-rate = fraction of tasks solved in >=1 trial (pass@k).
Plus a per-task closed-vs-lsp success table. Run:
``uv run python eval/report_trials.py eval/scored_hard.json``.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict

CONDITIONS = ["closed", "lsp"]


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "eval/scored_hard.json"
    with open(path, encoding="utf-8") as fh:
        scored = json.load(fh)

    # (task, condition) -> list of bool correct
    by: dict[tuple[str, str], list[bool]] = defaultdict(list)
    domain: dict[str, str] = {}
    for r in scored:
        by[(r["task_id"], r["condition"])].append(bool(r["correct"]))
        domain[r["task_id"]] = r.get("domain", "")
    tasks = sorted({t for t, _ in by})

    lines = ["# Magma LLM eval (hard tasks, multi-trial) — closed-book vs LSP\n"]
    lines.append("Cell = correct trials / total trials.\n")
    lines.append("| task | domain | closed | lsp |")
    lines.append("|---|---|:---:|:---:|")
    for t in tasks:
        cells = []
        for c in CONDITIONS:
            rs = by.get((t, c), [])
            cells.append(f"{sum(rs)}/{len(rs)}" if rs else "·")
        lines.append(f"| {t} | {domain[t]} | {cells[0]} | {cells[1]} |")

    lines.append("\n## Aggregate\n")
    for c in CONDITIONS:
        runs = [ok for (tk, cc), oks in by.items() if cc == c for ok in oks]
        task_rates = [sum(oks) / len(oks) for (tk, cc), oks in by.items() if cc == c and oks]
        solved = [1 for (tk, cc), oks in by.items() if cc == c and any(oks)]
        n_tasks = len({tk for (tk, cc) in by if cc == c})
        pass1 = 100 * sum(runs) / max(1, len(runs))
        avg = 100 * sum(task_rates) / max(1, len(task_rates))
        solve = 100 * len(solved) / max(1, n_tasks)
        lines.append(
            f"- **{c}**: pass@1 {pass1:.0f}% ({sum(runs)}/{len(runs)} runs), "
            f"avg task success {avg:.0f}%, solved≥1 {solve:.0f}% ({len(solved)}/{n_tasks} tasks)"
        )

    # tasks where lsp beats closed on solve-rate
    better, worse = [], []
    for t in tasks:
        cl = by.get((t, "closed"), [])
        lp = by.get((t, "lsp"), [])
        if cl and lp:
            csr, lsr = sum(cl) / len(cl), sum(lp) / len(lp)
            if lsr > csr:
                better.append(t)
            elif lsr < csr:
                worse.append(t)
    lines.append("\n## Where the LSP changed the success rate\n")
    lines.append(f"- lsp > closed: {better or 'none'}")
    lines.append(f"- lsp < closed: {worse or 'none'}")

    report = "\n".join(lines)
    print(report)
    out_md = path.replace("scored", "report").replace(".json", ".md")
    with open(out_md, "w", encoding="utf-8") as fh:
        fh.write(report + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
