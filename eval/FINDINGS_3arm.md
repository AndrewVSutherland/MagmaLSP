# 3-arm experiment: what actually helps an LLM write Magma?

**Question.** PR #8 showed Sonnet jumps from 75% → 98% on 20 hard tasks when given the full Magma
LSP front-end (signature **lookup** + static **check** + sandboxed **run**). But that bundles two
very different capabilities: *being able to execute Magma at all* vs. *the LSP's offline
intelligence (the signature DB)*. This experiment isolates them.

**Design.** Same 20 hard tasks (`eval/hard_tasks.py`), 3 trials each (Sonnet), three conditions:

| arm | can execute Magma? | has signature lookup / static check? |
|---|:---:|:---:|
| **closed** | ✗ | ✗ — writes blind, one shot |
| **raw** | ✓ (plain `magma`, iterates on its own runtime errors) | ✗ |
| **lsp** | ✓ | ✓ — `lookup` + `check` + `run` front-end |

Scoring is deterministic and external (`eval/score.py`): run the program in Magma, compare stdout to
a validated reference answer (`eval/truth_hard_tasks.json`). 180 generations scored in parallel
(150 s timeout, 60 workers).

## Result

| arm | pass@1 | avg task success | solved ≥1 |
|---|:---:|:---:|:---:|
| closed | **75%** (45/60) | 75% | 16/20 |
| raw | **100%** (60/60) | 100% | 20/20 |
| lsp | **98%** (59/60) | 98% | 20/20 |

**The dominant lever is execution, not the signature DB.** Simply letting the model run Magma and
read its own error messages closed the *entire* 75→100 gap on this benchmark — and slightly edged out
the full LSP arm.

The six tasks the closed model failed are the hallucination-prone ones (nonexistent `PetersenGraph`,
wrong `GolayCode`/`KissingNumber` signatures, `TorsionUnitGroup`, `VarietySizeOverAlgebraicClosure`).
Both tool-equipped arms fixed all six. The mechanism differs: the LSP arm looks the name up *before*
writing; the raw arm writes its best guess, gets `... is not an intrinsic` or an argument-type error,
and corrects — usually within 1–2 iterations.

## The one lsp regression is instructive

`e8_aut_order`, lsp trial 1: the model wrote `Lattice("E8", 8)`. Magma rejects it with
`Argument 1 should be one of "A", "B", ... "E", ...`. This is an argument-**value** error — not a
missing name or a syntax slip — so the LSP's *static* check can't see it, and that trial submitted
without a dynamic run. The raw arm hit the same mistake but its iterate-on-error loop read Magma's
message (which enumerates the valid letters) and fixed it to `"E"`. All 3 raw trials and 2/3 lsp
trials got it right.

## Takeaways for the project

1. **Magma-backed execution is the highest-value feature, by a wide margin.** The
   `publishDiagnostics` + run loop is what moves the needle. Prioritize making the dynamic check
   fast, sandboxed, and always-on (it already runs ~548 checks/s across 64 workers).
2. **The signature DB's marginal value is real but smaller** on tasks that can be checked by
   running. Its niche is (a) the *first* draft (fewer wasted iterations), (b) hover/completion for
   the human, and (c) settings where execution is unavailable or slow.
3. **Static check ⊂ dynamic check.** The lsp miss shows static analysis can't catch argument-value
   errors. The LSP arm should *always* finish with an execution pass when Magma is available —
   never trust lookup + static check alone. (Effectively: make the `run` step mandatory, not
   optional, in the agent loop.)
4. **Caveat on the 100%.** These 20 tasks each have a short, well-posed numeric answer reachable in
   a handful of iterations — ideal for an iterate-on-error loop. Harder, multi-stage, or
   long-running problems (where a single run costs minutes, or where "it ran without error" ≠
   "correct") will not be saturated by raw execution alone, and are where lookup + a good first
   draft should pull back ahead. This benchmark establishes the floor, not the ceiling.

Artifacts: `eval/generations_3arm.json` (180 gens), `eval/scored_3arm.json`, `eval/report_3arm.md`
(auto-generated table). Reproduce: regenerate arms via the eval workflow, then
`uv run python eval/score.py eval/generations_3arm.json --truth eval/truth_hard_tasks.json
--out eval/scored_3arm.json && uv run python eval/report_trials.py eval/scored_3arm.json`.
