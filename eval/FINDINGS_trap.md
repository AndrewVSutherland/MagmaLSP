# "ran ≠ correct" benchmark: does the signature DB beat raw execution when nothing errors?

**Motivation.** PR #9 (the 3-arm experiment on `hard_tasks`) found raw execution saturates (100%) and
even edges out the full LSP, because *every* failure there is a **crash** — an invented intrinsic or a
bad argument type — that Magma flags and a run-loop iterates against. The open question (the explicit
caveat in `FINDINGS_3arm.md`): what happens in the regime where the plausible naive program **runs
cleanly and prints a wrong answer**? There the run-loop has no error to chew on, so this is where the
signature DB's doc strings *should* pull the LSP arm ahead of raw — if anything does.

**Benchmark.** `eval/trap_tasks.py`: 14 tasks whose naive solution runs clean but is wrong, via
Magma-specific conventions/precision (not syntax): `Subgroups` returns conjugacy-class reps;
`Roots`/`Factorization`/`ConjugacyClasses` return tuples; `Divisors` includes n; `ModularForms`
includes Eisenstein (vs `CuspForms`); poly-disc ≠ field-disc; `a/b` is rational not `div`;
`Coefficients[1]` is the constant term; rational vs real roots; default `RealField` truncates a large
`Floor`; `IsSquare` returns `(bool, root)`. Same 3 arms (closed / raw / lsp), 3 trials, deterministic
external scoring.

## Result

| arm | pass@1 | avg task success | solved ≥1 |
|---|:---:|:---:|:---:|
| closed | **81%** (34/42) | 81% | 13/14 |
| raw | **100%** (42/42) | 100% | 14/14 |
| lsp | **100%** (42/42) | 100% | 14/14 |

The traps clearly separate **closed from tooled** (+19 pts): the closed model fell into the convention
traps — `subgroups_s4` (printed `11`, the conjugacy-class count, twice out of three), `cusp_dim`,
`largest_class`, `real_roots`. **But raw and lsp are tied at 100%.** The "ran ≠ correct" regime did
*not* separate raw execution from the signature DB.

## Why raw ties lsp even with no error to iterate on — the mechanism

The transcripts are the interesting part. On `largest_class` ("size of the largest conjugacy class of
S_6", where `ConjugacyClasses` returns `(order, size, rep)` triples):

- **lsp** reached the answer in **3 calls**: `lookup ConjugacyClasses` → read that it returns
  `(order, size, representative)` → wrote `c[2]` correctly first try → `run`. The doc *told* it the
  convention.
- **raw** reached the same answer in **6 calls**, with no docs, by *empirically reverse-engineering*
  the convention: it printed `c[1]`, `c[2]`, `#c` to discover the tuple shape, annotated
  "`c[1]=order, c[2]=size`", **and cross-checked by summing the class sizes against |S_6| = 720**
  before committing. Execution let it probe structure and verify — a documentation substitute.
- **closed** had neither, reached for `#(c[3]^G)` (class as an orbit), and crashed all 3 trials.

This generalizes: in a computer algebra system, almost any semantic error is *empirically detectable*
by running an alternate computation or inspecting a return value. A capable agent with execution will
probe and cross-check, so **raw ≈ lsp in capability** even here. The honest reading of the closed-arm
failures supports this: of the 8 closed misses, only **2 were genuinely silent-wrong** (`subgroups_s4`
→ `11`); the other 6 were **crashes** (the trap-reaching code happened to error) — i.e. constructing a
trap that stays silent *and* survives a probing agent is genuinely hard.

## Where the DB's value actually is: efficiency, not capability

The arms differ not in *whether* they get there but in *how much Magma they burn*:

| arm | Magma runs / agent | lookups / agent |
|---|:---:|:---:|
| raw | **1.52** | — |
| lsp | **1.07** | 1.79 |

Raw does ~50% more Magma executions on average (and 5–6 vs 1 on the hardest trap) — the cost of
discovering conventions by trial instead of reading them. So the signature DB's marginal value over
raw execution is **fewer iterations / lower Magma load / lower latency**, and it matters most where
probing is *expensive or impossible*:

1. **Long-running computations** — when one run costs minutes, you cannot afford 6 probing iterations;
   reading the convention once is decisive.
2. **No cheap cross-check** — `largest_class` had an obvious sanity check (sizes sum to |G|). Many real
   problems don't; there the DB substitutes for a verification the agent can't construct.
3. **Latency / cost / Magma-process budget** — fewer executions per task at scale.
4. **The human in the loop** — hover/completion needs the DB regardless; a mathematician reading a
   signature is the lookup arm.

## Bottom line (refines PR #9)

- Execution remains the dominant lever; it now also matches the DB in the *"ran ≠ correct"* regime,
  because a probing agent reverse-engineers Magma's conventions and cross-checks results.
- The signature DB is an **efficiency and robustness** layer on top of execution — fewer iterations,
  and a safety net exactly when empirical probing is too slow or has no ground truth — not a separate
  source of capability on tasks that are cheap to run.
- Product implication: keep always-on execution as the backbone; surface the DB's conventions
  *proactively* (in hover/completion and as context to the agent) so the agent doesn't pay the
  probing tax — and lean on it hardest for expensive/unverifiable computations.

Artifacts: `eval/trap_tasks.py`, `eval/truth_trap_tasks.json`, `eval/generations_trap.json` (126),
`eval/scored_trap.json`, `eval/report_trap.md`.
