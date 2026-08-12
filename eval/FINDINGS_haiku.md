# Haiku arms: does the LSP matter more for a smaller model?

**Question.** The Sonnet experiments (`FINDINGS_3arm.md`, `FINDINGS_trap.md`) found execution is
the dominant lever and the signature DB adds efficiency, not capability — because Sonnet
empirically reverse-engineers Magma's conventions (probing tuple shapes, cross-checking against
`#G`) whenever it lacks docs. The explicit open question: does that hold for the *smaller models
this project targets*, or does the DB become a capability lever when the model can't build its own
verification scaffolding? This run replicates both benchmarks with **Haiku 4.5**
(`claude-haiku-4-5`), same three arms, same deterministic external scoring, 3 trials.

**Harness** (now committed): `gen_jobs.py` emits one job-spec file per (task, arm, trial);
`run_agents.sh` drives one headless `claude -p --model haiku` agent per pending job at bounded
concurrency (idempotent — rerun to retry stragglers); `collect.py` gathers the per-job result
JSONs into `generations_*.json` for `score.py`. 306 generations (34 tasks × 3 arms × 3 trials)
took ~35 min at concurrency 14 on the dev box. One caveat vs the Sonnet runs: the Haiku lsp arm
uses the *current* CLI surface (adds `guide`/`search` and the pitfall/arity static checks, which
did not exist for the Sonnet runs).

## Results (pass@1; Sonnet numbers from the earlier runs for comparison)

| benchmark | arm | Sonnet | **Haiku 4.5** |
|---|---|:---:|:---:|
| hard (20) | closed | 75% | **70%** (42/60) |
| hard | raw | 100% | **100%** (60/60) |
| hard | lsp | 98% | **95%** (57/60) |
| trap (14) | closed | 81% | **64%** (27/42) |
| trap | raw | 100% | **95%** (40/42) |
| trap | lsp | 100% | **98%** (41/42) |

Per-task tables: `report_haiku_hard.md`, `report_haiku_trap.md`. Cell counts are small
(42–60/arm), so single-generation differences ≈ 2 pts; read mechanisms, not decimals.

## Finding 1 — the closed gap widens for the smaller model, most on conventions

Closed Haiku loses 5 pts to closed Sonnet on the hallucination-style tasks (70 vs 75) but **17 pts
on the convention traps** (64 vs 81): it fell for `subgroups_s4` (printed the class-rep count),
`field_disc` (printed the polynomial discriminant), `largest_class` (indexed the class tuple
wrongly) in **all nine** of those trials, plus `exp_floor` (default `RealField` precision) 3/3.
A smaller model both invents more names *and* knows fewer Magma conventions — the tool's target
audience has more to gain.

## Finding 2 — tools restore Haiku to Sonnet level

Every tool-equipped Haiku arm lands within 5 pts of the corresponding Sonnet arm (and
tool-equipped Haiku beats *closed Sonnet* by 14–23 pts). The project's premise holds directly:
**this tooling makes a small model competitive with a much larger bare model, and nearly
indistinguishable from the larger tooled model** — at a fraction of the cost/latency.

## Finding 3 — the first raw/lsp separation, in the DB's favor, exactly where predicted

On the traps, raw Haiku is no longer saturated: **its two failures are precisely the silent-wrong
convention traps** — `subgroups_s4:raw:0` ran clean twice and submitted `11` (conjugacy-class
reps, not subgroups); `field_disc:raw:0` ran clean and submitted `-2012` (the polynomial
discriminant; the field discriminant is `-503`, index 2). No error to iterate on, and — unlike
Sonnet, which probed tuple shapes and cross-checked class sizes against `#G` — Haiku did not
construct its own verification. **The lsp arm solved both tasks 3/3**: the doc strings supplied
the convention the model couldn't discover empirically. This is the mechanism `FINDINGS_trap.md`
predicted would separate the arms "when the agent can't build a cross-check": weaker agents hit
that regime on tasks where stronger agents don't. (lsp's one trap miss, `distinct_partitions:0`,
computed all partitions of 20 — a semantic slip the docs don't guard.)

## Finding 4 — on crash-style tasks, raw execution still wins; the losses are argument-VALUE errors

On hard tasks raw Haiku stays 100% while lsp drops 3: two E8 constructions
(`e8_aut_order` → 10321920, `e8_kissing` → 16 — wrong lattice built) and one
`petersen_chromatic` flail-out (21 runs / 26 lookups, submitted still-erroring code).
`e8_aut_order` is the *same task* Sonnet-lsp missed once, for the same reason: `Lattice("E8", 8)`
-style argument-**value** mistakes are invisible to lookup + static check, while the raw arm's
iterate-on-error loop reads Magma's message enumerating the valid arguments. Two product lessons,
sharpened: (a) the agent guidance should make a final `run` + output sanity-check non-negotiable
(the lsp arm's misses all *ran something*, but didn't verify the result made sense); (b) `lookup`
should surface valid-argument enumerations from the handbook prose where present.

## Finding 5 — the efficiency profile inverts: for Haiku the DB is a crutch, not a shortcut

Sonnet-lsp used *fewer* Magma runs than Sonnet-raw (1.07 vs 1.52 on traps). Haiku is the opposite
(self-reported counts, so approximate):

| arm | hard: runs / lookups per agent | trap: runs / lookups per agent |
|---|:---:|:---:|
| raw | 1.38 / — | 1.86 / — |
| lsp | 5.38 / 9.38 | 3.26 / 5.21 |

Haiku-lsp leans on the tools hard — 10–15 tool calls per task on hard problems, and its failures
flail hardest (21 runs / 26 lookups on the `petersen_chromatic` miss). For a small model the DB
isn't an iteration-saver; it's load-bearing scaffolding it consults constantly. That's fine for
correctness (Findings 2–3) but means the "fewer iterations / lower Magma load" benefit measured
with Sonnet does **not** transfer down-model; budget accordingly (per-call latency of the CLI
matters more for small models, not less).

## Bottom line

- **For the target audience (smaller models), the signature DB is a capability lever, not just an
  efficiency layer** — it fixes exactly the silent-wrong convention failures that raw execution
  cannot, because a small model doesn't improvise cross-checks the way Sonnet does.
- Execution remains the backbone (largest single jump in every comparison: +25–31 pts
  closed→raw).
- The remaining lsp losses are argument-value errors + missing result-sanity habits — addressable
  in the guide/prompt (mandatory final run + plausibility check) and by surfacing valid-argument
  enumerations in `lookup`.

Artifacts: `generations_haiku_{hard,trap}.json`, `scored_haiku_{hard,trap}.json`,
`report_haiku_{hard,trap}.md`. Reproduce:

```bash
python3 eval/gen_jobs.py --tasks hard trap --trials 3 --jobdir /tmp/haiku-jobs
bash eval/run_agents.sh /tmp/haiku-jobs 14 haiku     # rerun to retry any missing jobs
python3 eval/collect.py --jobdir /tmp/haiku-jobs --out 'eval/generations_haiku_{bench}.json' --benches hard trap
uv run python eval/score.py eval/generations_haiku_hard.json --truth eval/truth_hard_tasks.json --out eval/scored_haiku_hard.json
uv run python eval/score.py eval/generations_haiku_trap.json --truth eval/truth_trap_tasks.json --out eval/scored_haiku_trap.json
uv run python eval/report_trials.py eval/scored_haiku_hard.json "hard tasks, Haiku 4.5" > eval/report_haiku_hard.md
uv run python eval/report_trials.py eval/scored_haiku_trap.json "trap tasks, Haiku 4.5" > eval/report_haiku_trap.md
```
