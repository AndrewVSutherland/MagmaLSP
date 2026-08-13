# Pre-public-release plan

Work to do before making this repo public, in priority order. Written 2026-08-13 after the
PR #12 merge (comprehensive review + 17 codex rounds; see `eval/`, `reports/`, and the memory
of that session). Every "verified" claim below was tested on this box (Magma 2.29-9,
bwrap 0.11.0) on 2026-08-13 — trust them, but re-verify anything that seems off.

**Process:** two PRs off `main`. PR A = items 2–4 (small, mechanical). PR B = item 1 (the
sandbox — substantial, wants its own review). Arm the watch-codex monitor at each PR-open
(`~/.claude/skills/watch-codex`); the PR-12 engagement averaged 2–3 genuine P2s per push, so
budget for a few disposition rounds. Repo conventions (commit trailers, rendered-artifact
links in PR bodies) are in the user-level CLAUDE.md. Gates for every commit:
`uv run pytest -q` (194 tests at time of writing), `uv run ruff check src tests`, and for
anything touching `analysis/` or the DB: `uv run python validation/arity_fp.py` must stay at
**26 flags / 4031 sources** (all verified latent bugs in Magma's own packages, not FPs).

---

## 1. OS-level execution sandbox (the release gate) — PR B

**Why:** `magma_run` / `magma_check(execute=True)` give an LLM agent unrestricted code
execution. Magma's `System(...)`, `Pipe(...)`, file writes, and network all work, bounded
only by `timeout` + `SetMemoryLimit`. Fine for a trusted single user; not for a public
plugin whose users won't expect their coding agent to have acquired a shell.

**Verified facts that constrain the design (2026-08-13, this box):**
- Working recipe, tested end-to-end (Magma licensed, program ran, write blocked):
  ```
  bwrap --ro-bind / / --tmpfs /tmp --ro-bind <src.m> <src.m> \
        --dev /dev --proc /proc --die-with-parent [--chdir <cwd>] magma -b -n <src.m>
  ```
  Under this, `System("touch /home/claude/X")` runs but the write FAILS (RO root); the
  program's own output and exit behavior are unchanged.
- **`--unshare-net` BREAKS Magma licensing**: the license check reads the host MAC address
  and reports "not authorised ... This host has the following MAC address(es): <empty>".
  Do NOT unshare the network namespace. Beware: `magma -V` SKIPS the license check, so a
  `-V` probe under `--unshare-net` misleadingly succeeds — test with a real program.
- Therefore the sandbox blocks filesystem *mutation* (the worst vector) but NOT shell-out
  per se and NOT network egress. Say exactly that in the README — no overclaiming.
  (`--unshare-pid --unshare-ipc --new-session` are safe to add and worth it.)
- The runner writes the temp `.m` to `/tmp` and then the recipe `--ro-bind`s that single
  file back over the `--tmpfs /tmp` — verified working, keep that shape.
- The execution pass runs with `cwd` = the source file's dir so relative `load`s resolve
  (Magma resolves load paths against the process cwd — nested loads too). RO root already
  makes loaded siblings readable; add `--chdir "$cwd"` to preserve the cwd inside bwrap.

**Design (recommended):**
- Single choke point: `magma/runner.py:run_source()` grows a `sandbox` mode that prefixes
  the argv with the bwrap recipe when enabled. `timeout` stays OUTSIDE bwrap
  (`timeout N bwrap ... magma ...`) so the wall clock kills the whole tree
  (`--die-with-parent` handles the inner side).
- **Sandbox only the passes that execute user code**: `execution_check` and `frontend.run`.
  The parse-only strategies (function wrap, `Attach`) were verified twice during PR 12 to
  execute nothing user-level (Attach rejects top-level statements at parse time; top-level
  assignments bind lazily) — leaving them unsandboxed keeps the every-save hot path at its
  measured ~12.5 ms.
- Policy: default ON when `bwrap` is present; `MAGMA_LSP_NO_SANDBOX=1` (and an
  `initializationOptions` / MCP-visible note) to opt out; loud one-time warning when bwrap
  is absent (macOS has no bwrap — the warning IS the fallback there; do not pretend
  `sandbox-exec` works without testing it).
- Optional (decide when there): a `writable_dir` escape hatch (`--bind <dir> <dir>`) for
  users whose programs legitimately write output files; if added, default it to unset.
- Report the sandbox state in `magma_guide()` and the `magma_run` docstring so agents know
  writes will fail inside the sandbox (a confused agent burning iterations on EACCES is a
  real failure mode — tell it up front).

**Tests** (magma-marked, skip without Magma; also skipif no bwrap):
- `System("touch ...")` inside a sandboxed run does not create the file; run output intact.
- A program that `load`s a sibling still works sandboxed (cwd + RO reads).
- `SetMemoryLimit` + timeout still enforced (existing behavior unchanged).
- Opt-out env var really disables it.
- Measure: `validation/bench.py` before/after — bwrap adds a few ms per run at most; the
  syntax-check path must be UNCHANGED (it isn't sandboxed).

**Docs:** README "Execution sandbox" section rewritten to state precisely: writes blocked,
shell-out/network not blocked (and why — the MAC licensing constraint), opt-out, no-bwrap
behavior. Update the `magma_run`/`magma_check` MCP docstrings to match.

## 2. CI — PR A

`.github/workflows/ci.yml`: on push + PR. Steps: checkout, `astral-sh/setup-uv`,
`uv sync --extra dev` (needs a C compiler for tree-sitter-magma — present on
ubuntu-latest; uv-managed Python bundles headers), `uv run ruff check src tests`,
`uv run pytest -q`. All Magma-dependent tests carry skipif markers (verified) so the suite
passes on a runner with no Magma. Keep it to one job; no caching cleverness needed at this
repo size. Add the badge to README.

## 3. Un-hardcode the eval harness — PR A

`eval/gen_jobs.py` embeds `/home/claude/MagmaLSP` in the raw/lsp arm prompt templates
(`uv run --project /home/claude/MagmaLSP magma-lsp-cli ...`), so the committed harness is
not runnable by anyone else. Fix: derive the repo root from the file's own location
(`Path(__file__).resolve().parents[1]`), template it into CORE/RAW/LSP, allow
`--repo-root` override. Add a provenance line to `eval/FINDINGS_haiku.md`: the committed
Haiku generations (2026-08-12) used the then-verbatim templates; regenerated runs use the
parameterized ones. Keep the `# ruff: noqa: E501` — the templates still shouldn't be
rewrapped. Re-run `bash -n eval/run_agents.sh` and the collect/score path on the existing
jobdir fixture if still present (or a fresh 2-job dry run with a stub `claude`).

## 4. README "Requirements" section — PR A

At the top, before install: a licensed Magma installation (developed/tested against
V2.29-9; the DB is built from YOUR install, so nothing Magma-owned ships with this repo),
Linux (macOS untested — say so), `uv`, a C compiler for the tree-sitter build. One
sentence on what happens without Magma (static-only mode, package-only DB) — the code
already degrades honestly; the README should promise exactly that.

---

## Tier 2 (nice-to-have, non-blocking — bundle into PR A where trivial)

- **Version + tag**: bump `pyproject.toml` 0.0.1 → 0.1.0; tag `v0.1.0` at the public
  commit; 5-line CHANGELOG.md (initial public release; capabilities summary).
- **LICENSE copyright line** currently reads "AndrewVSutherland2" (GitHub handle) — Drew
  may prefer his name/institution. ASK, don't guess.
- **Old PR descriptions** contain `claude-box.tail29674b.ts.net` rendered-artifact links;
  they become visible text when the repo goes public (they only resolve inside the
  tailnet, so it's cosmetic). The same reports are committed under `reports/` — optionally
  edit PR #12's body to point at the in-repo files. Low priority.
- **Magma bug report to the Sydney group** (goodwill + public validation): the arity
  checker's 26 corpus flags are latent dead-code bugs in Magma's own package tree —
  regenerate the list with `uv run python validation/arity_fp.py`, spot-verify each flagged
  call against `magma-lsp-cli lookup` before including it (the PR-12 session verified
  e.g. `Ring/FldFun/FldAb/HCF.m:389` asserts on a 3-arg call to the 1-or-2-arg `Expand`,
  and `GrpFP/SolQ/sq_proc.m` calls a 3-arg `AbsolutelyIrreducibleModules` that doesn't
  exist). Draft an email/issue for Drew to review — do NOT send anything yourself.
- **`.m` extension collision** (MATLAB/ObjC in Claude Code): documented open item
  (CLAUDE.md §9/§11); verify in a live Claude Code session when convenient; not a release
  blocker since `.magma` is the primary mapping.

## Explicitly checked, no action needed

- MIT LICENSE present; no secrets or internal hostnames in committed files (only the
  gen_jobs templates, item 3).
- IP: the signature DB builds from the user's own Magma install at their site — no Magma
  content is redistributed by this repo.
- Test suite (194) and `validation/` harnesses green at merge; corpus arity audit at
  26/4031 verified-class flags.
