# LLM eval: does the Magma LSP help an LLM write Magma?

The project's key objective is to improve LLMs' ability to write correct Magma. This harness
measures that directly: it has a model write a complete Magma program for each benchmark task
under controlled conditions and scores the results against precomputed ground truth.

**Findings so far** (each file is self-contained):
- `FINDINGS_3arm.md` — Sonnet, hard tasks: execution is the dominant lever; raw ties lsp.
- `FINDINGS_trap.md` — Sonnet, "ran ≠ correct" traps: raw still ties lsp (Sonnet improvises
  cross-checks); the DB's edge is efficiency (fewer iterations), not capability.
- `FINDINGS_haiku.md` — **Haiku 4.5, both benchmarks: for a smaller model the DB becomes a
  capability lever** — raw's only failures are the silent-wrong convention traps the docs fix,
  and tool-equipped Haiku plays at tool-equipped-Sonnet level.

## Conditions
- **closed** — Sonnet uses only its own knowledge; no tools, no execution (one shot).
- **lsp** — Sonnet can call the Magma-LSP CLI (`magma_lsp/cli.py`) and iterate:
  - `magma-lsp-cli lookup <Name>` — exact intrinsic signature(s) + handbook docs (stops invented names),
  - `magma-lsp-cli check <file.m>` — undefined-intrinsic + Magma syntax/binding errors,
  - `magma-lsp-cli run <file.m>` — execute and show output.

The delta = the LSP's contribution (signature DB + error signal) to LLM-written-Magma correctness.

## Pieces
- `tasks.py` — 15 tasks across elliptic curves, number fields, finite fields, groups, linear algebra,
  modular forms, combinatorics, lattices; each asks for one scalar answer.
- `build_truth.py` — runs each task's reference solution in Magma to capture the expected output
  (`truth.json`). Re-run if tasks change.
- `score.py` — **deterministic** scoring: runs the model's program in Magma and compares to the
  expected answer (the model cannot self-report success).
- `report.py` — per-task + aggregate comparison (`report.md`).

## Running it
1. `uv run python eval/build_truth.py` — build ground truth (needs Magma).
2. Generate programs under each condition, writing `[{task_id, condition, trial, code}, ...]`.
   The committed harness (used for the Haiku runs; any harness producing that JSON works):
   - `gen_jobs.py --tasks hard trap --trials 3 --jobdir <dir>` — one job-spec file per
     (task, arm, trial), each a self-contained agent brief with the arm's tool rules;
   - `run_agents.sh <dir> [concurrency] [model]` — one headless `claude -p` agent per pending
     job; idempotent (existing results are skipped), so rerunning retries stragglers;
   - `collect.py --jobdir <dir> --out 'eval/generations_{bench}.json' --benches hard trap` —
     gather per-job results (missing jobs are emitted as failures, never dropped).
3. `uv run python eval/score.py <generations.json> --truth <truth.json> --out <scored.json>`.
4. `uv run python eval/report_trials.py <scored.json> "<label>"` → per-task + aggregate tables
   (`report.py` for the older single-trial format).

Scoring is independent of generation, so the same `generations.json` can be re-scored and the
benchmark can grow without touching the model loop.
