"""Collect per-job result files from a gen_jobs.py run into a generations JSON for score.py.

Usage:
    python eval/collect.py --jobdir /path/to/jobs --out eval/generations_haiku.json

Reads ``<jobdir>/manifest.json`` (written by gen_jobs.py) and each job's ``out`` file
(``{"code", "n_runs", "n_lookups"}`` written by the generation agent). Jobs with a missing or
unparseable out-file are reported and emitted with ``code = ""`` (scored as failures, so a dead
agent can't silently shrink the denominator). Use ``--benches`` to split one run into per-benchmark
generation files matching each benchmark's truth file.
"""

from __future__ import annotations

import argparse
import json
import os


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobdir", required=True)
    ap.add_argument("--out", required=True, help="output path; with --benches, a {bench} template")
    ap.add_argument("--benches", nargs="*", default=None, help="split per bench, e.g. hard trap")
    args = ap.parse_args()

    with open(os.path.join(args.jobdir, "manifest.json")) as f:
        manifest = json.load(f)

    rows, missing = [], []
    for j in manifest:
        rec = {
            "task_id": j["task_id"],
            "condition": j["condition"],
            "trial": j["trial"],
            "bench": j["bench"],
            "code": "",
            "n_runs": None,
            "n_lookups": None,
        }
        try:
            with open(j["out"]) as f:
                r = json.load(f)
            rec["code"] = r.get("code", "") or ""
            rec["n_runs"] = r.get("n_runs")
            rec["n_lookups"] = r.get("n_lookups")
            if not rec["code"]:
                missing.append(j)
        except (OSError, json.JSONDecodeError):
            missing.append(j)
        rows.append(rec)

    for j in missing:
        print(f"MISSING/EMPTY: job {j['job']} {j['task_id']}:{j['condition']}:{j['trial']}")

    if args.benches:
        for bench in args.benches:
            sub = [r for r in rows if r["bench"] == bench]
            path = args.out.format(bench=bench)
            with open(path, "w") as f:
                json.dump(sub, f, indent=1)
            print(f"{bench}: {len(sub)} generations -> {path}")
    else:
        with open(args.out, "w") as f:
            json.dump(rows, f, indent=1)
        print(f"{len(rows)} generations -> {args.out}")
    print(f"complete: {len(rows) - len(missing)}/{len(rows)}")
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
