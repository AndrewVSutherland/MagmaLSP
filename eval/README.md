# LLM eval: does the Magma LSP help an LLM write Magma?

The project's key objective is to improve LLMs' ability to write correct Magma. This harness
measures that directly: it has **Sonnet** write a complete Magma program for each benchmark task
under two conditions and scores the results against precomputed ground truth.

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
2. Generate programs with Sonnet under both conditions, writing
   `[{task_id, condition, code}, ...]` to `eval/generations.json`. In this repo that is orchestrated
   with a Claude Code workflow that spawns one Sonnet subagent per (task, condition); the `lsp`
   agents drive the `magma-lsp-cli` tools above. (Any harness that produces that JSON works.)
3. `uv run python eval/score.py eval/generations.json` → `eval/scored.json`.
4. `uv run python eval/report.py` → prints + writes `eval/report.md`.

Scoring is independent of generation, so the same `generations.json` can be re-scored and the
benchmark can grow without touching the model loop.
