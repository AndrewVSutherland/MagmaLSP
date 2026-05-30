"""Summarize scored eval results: closed-book vs LSP, per task and per domain.

Reads eval/scored.json (from score.py). Run: ``uv run python eval/report.py``.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict

CONDITIONS = ["closed", "lsp"]
MARK = {True: "✓", False: "✗"}


def _by(scored: list[dict]) -> dict[tuple[str, str], dict]:
    return {(r["task_id"], r["condition"]): r for r in scored}


def main() -> int:
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "scored.json"), encoding="utf-8") as fh:
        scored = json.load(fh)
    idx = _by(scored)
    task_ids = sorted({r["task_id"] for r in scored})

    totals = {c: {"correct": 0, "ran": 0, "n": 0} for c in CONDITIONS}
    per_domain = defaultdict(lambda: {c: {"correct": 0, "n": 0} for c in CONDITIONS})

    lines = ["# Magma LLM eval — closed-book vs LSP\n"]
    lines.append("| task | domain | closed | lsp | expected |")
    lines.append("|---|---|:---:|:---:|---|")
    for tid in task_ids:
        row = []
        domain = ""
        for c in CONDITIONS:
            r = idx.get((tid, c))
            if r is None:
                row.append("·")
                continue
            domain = r["domain"]
            totals[c]["n"] += 1
            totals[c]["ran"] += int(r["ran_ok"])
            totals[c]["correct"] += int(r["correct"])
            per_domain[domain][c]["n"] += 1
            per_domain[domain][c]["correct"] += int(r["correct"])
            cell = MARK[r["correct"]] if r["ran_ok"] or r["correct"] else "err"
            row.append(cell)
        exp = next((idx[(tid, c)]["expected"] for c in CONDITIONS if (tid, c) in idx), "")
        lines.append(f"| {tid} | {domain} | {row[0]} | {row[1]} | `{exp}` |")

    lines.append("\n## Overall\n")
    for c in CONDITIONS:
        t = totals[c]
        lines.append(
            f"- **{c}**: {t['correct']}/{t['n']} correct "
            f"({100 * t['correct'] / max(1, t['n']):.0f}%), "
            f"{t['ran']}/{t['n']} ran without error"
        )
    d = totals["lsp"]["correct"] - totals["closed"]["correct"]
    lines.append(f"- **LSP delta: {d:+d} tasks** correct vs closed-book")

    lines.append("\n## By domain (correct/n)\n")
    for dom in sorted(per_domain):
        pd = per_domain[dom]
        lines.append(
            f"- {dom}: closed {pd['closed']['correct']}/{pd['closed']['n']}, "
            f"lsp {pd['lsp']['correct']}/{pd['lsp']['n']}"
        )

    # Where the LSP changed the outcome
    flipped = [
        tid for tid in task_ids
        if (tid, "closed") in idx and (tid, "lsp") in idx
        and not idx[(tid, "closed")]["correct"] and idx[(tid, "lsp")]["correct"]
    ]
    regressed = [
        tid for tid in task_ids
        if (tid, "closed") in idx and (tid, "lsp") in idx
        and idx[(tid, "closed")]["correct"] and not idx[(tid, "lsp")]["correct"]
    ]
    lines.append("\n## Outcome changes\n")
    lines.append(f"- LSP fixed (closed ✗ → lsp ✓): {flipped or 'none'}")
    lines.append(f"- LSP regressed (closed ✓ → lsp ✗): {regressed or 'none'}")

    lines.append("\n## Closed-book failures (error detail)\n")
    for tid in task_ids:
        r = idx.get((tid, "closed"))
        if r and not r["correct"]:
            why = r["error"] or f"wrong output: {r['output']!r}"
            lines.append(f"- `{tid}`: {why[:160]}")

    report = "\n".join(lines)
    print(report)
    with open(os.path.join(here, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(report + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
