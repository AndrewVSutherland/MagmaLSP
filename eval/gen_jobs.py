"""Emit per-agent job spec files for an LLM generation run of the Magma eval benchmarks.

Each job = (task, condition, trial). The spec file holds the full instructions for one
generation agent under one of three arms (CLAUDE.md / eval/FINDINGS_3arm.md):

- ``closed`` — no tools, one shot, model knowledge only;
- ``raw``    — may execute plain ``magma`` and iterate on errors (no LSP);
- ``lsp``    — may use the ``magma-lsp-cli`` suite (guide/search/lookup/check/run), no plain magma.

The agent writes its result JSON (``{"code", "n_runs", "n_lookups"}``) to the job's ``out`` path;
``collect.py`` gathers those into a ``generations``-format JSON for ``score.py``. Orchestration
(spawning one subagent per job, any model) is done by the driver — in this repo a Claude Code
workflow; any harness that runs each spec with an LLM agent and leaves the out-files works.

Usage:
    python eval/gen_jobs.py --tasks hard trap --trials 3 --jobdir /path/to/jobs
"""

# ruff: noqa: E501  (the arm prompt templates below are verbatim run provenance — do not rewrap)

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path

ARMS = ("closed", "raw", "lsp")

CORE = """You are being evaluated on writing correct Magma (computer algebra system) code.

TASK: {prompt}

Deliverable: a complete, standalone Magma program that PRINTS the requested answer — the bare
value only, no labels or extra prose. No user input, no file access.

When finished, write a JSON object to the file {out}
with exactly these fields: {{"code": "<your final Magma program>", "n_runs": <int>, "n_lookups": <int>}}.
Then end with a final message that is just: done
"""

CLOSED = """
RULES: answer purely from your own knowledge of Magma, in ONE SHOT. Do not run, check, or look up
anything — no Magma execution, no documentation lookups, no reading files other than writing your
result. Decide on your best-guess program and write the result file immediately. If unsure of an
intrinsic name, give your best guess. Set n_runs = 0 and n_lookups = 0.
"""

RAW = """
You may check your work by actually running Magma. Create your private working directory {dir}
(mkdir -p), write your candidate program to a file there (e.g. {dir}/prog.m), and execute it
EXACTLY like this:

  timeout 60 magma -b -n {dir}/prog.m </dev/null 2>&1

Iterate on errors and output as needed until you are confident, then write the result file.

RULES: the plain `magma` binary is your ONLY Magma access — do NOT use magma-lsp-cli or any other
helper, and do NOT read any files outside {dir} (in particular nothing under {repo}).
Set n_runs = number of magma executions, n_lookups = 0.
"""

LSP = """
You have the Magma-LSP tool suite (and no other Magma access). Create your private working
directory {dir} (mkdir -p) and write candidate programs there. The commands:

  uv run --project {repo} magma-lsp-cli guide            # one-page Magma conventions & pitfalls brief (worth reading first)
  uv run --project {repo} magma-lsp-cli search <words>   # find intrinsic names by concept/keyword
  uv run --project {repo} magma-lsp-cli lookup <Name>    # exact signatures + docs for an intrinsic
  uv run --project {repo} magma-lsp-cli check <file.m>   # static + Magma diagnostics for your program
  uv run --project {repo} magma-lsp-cli run <file.m>     # execute the program sandboxed, see its output

Iterate as needed; it is wise to `run` your final program and confirm its output before finishing.

RULES: do NOT invoke the plain `magma` binary directly, and do NOT read any files under
{repo} yourself — interact with Magma only through the commands above.
Set n_runs = number of check/run calls, n_lookups = number of guide/search/lookup calls.
"""

ARM_TEXT = {"closed": CLOSED, "raw": RAW, "lsp": LSP}


def load_tasks(names: list[str]) -> list[dict]:
    here = os.path.dirname(os.path.abspath(__file__))
    out = []
    for short in names:
        mod = f"{short}_tasks"
        spec = importlib.util.spec_from_file_location(mod, os.path.join(here, f"{mod}.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        out.extend({"bench": short, "id": t["id"], "prompt": t["prompt"]} for t in m.TASKS)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", nargs="+", default=["hard", "trap"], help="task modules: hard trap")
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--arms", nargs="+", default=list(ARMS), choices=ARMS)
    ap.add_argument("--jobdir", required=True, help="directory for job specs, workdirs, and outputs")
    ap.add_argument(
        "--repo-root",
        default=None,
        help="MagmaLSP checkout the raw/lsp arm prompts reference (default: this file's repo)",
    )
    args = ap.parse_args()

    default_root = Path(__file__).resolve().parents[1]
    repo = os.path.abspath(args.repo_root) if args.repo_root else str(default_root)
    tasks = load_tasks(args.tasks)
    os.makedirs(os.path.join(args.jobdir, "out"), exist_ok=True)
    manifest = []
    i = 0
    for t in tasks:
        for arm in args.arms:
            for k in range(args.trials):
                out = os.path.join(args.jobdir, "out", f"job-{i}.json")
                work = os.path.join(args.jobdir, "work", f"job-{i}")
                spec_text = CORE.format(prompt=t["prompt"], out=out) + ARM_TEXT[arm].format(
                    dir=work, repo=repo
                )
                spec_path = os.path.join(args.jobdir, f"job-{i}.txt")
                with open(spec_path, "w") as f:
                    f.write(spec_text)
                manifest.append(
                    {
                        "job": i,
                        "bench": t["bench"],
                        "task_id": t["id"],
                        "condition": arm,
                        "trial": k,
                        "spec": spec_path,
                        "out": out,
                    }
                )
                i += 1
    with open(os.path.join(args.jobdir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    print(f"{i} jobs -> {args.jobdir} (tasks={len(tasks)}, arms={args.arms}, trials={args.trials})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
