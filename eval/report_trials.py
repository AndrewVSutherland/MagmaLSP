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

ORDER = ["closed", "raw", "lsp"]  # display order; only conditions present are shown


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "eval/scored_hard.json"
    with open(path, encoding="utf-8") as fh:
        scored = json.load(fh)

    # (task, condition) -> list of bool correct
    by: dict[tuple[str, str], list[bool]] = defaultdict(list)
    domain: dict[str, str] = {}
    present = set()
    for r in scored:
        by[(r["task_id"], r["condition"])].append(bool(r["correct"]))
        domain[r["task_id"]] = r.get("domain", "")
        present.add(r["condition"])
    conditions = [c for c in ORDER if c in present] + sorted(present - set(ORDER))
    tasks = sorted({t for t, _ in by})

    lines = ["# Magma LLM eval (hard tasks, multi-trial) — " + " vs ".join(conditions) + "\n"]
    lines.append("Cell = correct trials / total trials.\n")
    lines.append("| task | domain | " + " | ".join(conditions) + " |")
    lines.append("|---|---|" + ":---:|" * len(conditions))
    for t in tasks:
        cells = []
        for c in conditions:
            rs = by.get((t, c), [])
            cells.append(f"{sum(rs)}/{len(rs)}" if rs else "·")
        lines.append(f"| {t} | {domain[t]} | " + " | ".join(cells) + " |")

    lines.append("\n## Aggregate\n")
    for c in conditions:
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

    # pairwise: tasks where one condition beats another on success rate
    def rate(t, c):
        oks = by.get((t, c), [])
        return (sum(oks) / len(oks)) if oks else None

    lines.append("\n## Pairwise outcome changes (by task success rate)\n")
    for a, b in [(x, y) for i, x in enumerate(conditions) for y in conditions[i + 1 :]]:
        both = [t for t in tasks if rate(t, a) is not None and rate(t, b) is not None]
        up = [t for t in both if rate(t, b) > rate(t, a)]
        down = [t for t in both if rate(t, b) < rate(t, a)]
        lines.append(f"- **{b} vs {a}**: {b} better on {up or 'none'};")
        lines.append(f"  {b} worse on {down or 'none'}")

    report = "\n".join(lines)
    print(report)
    out_md = path.replace("scored", "report").replace(".json", ".md")
    with open(out_md, "w", encoding="utf-8") as fh:
        fh.write(report + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
