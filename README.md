# MagmaLSP

[![CI](https://github.com/AndrewVSutherland2/MagmaLSP/actions/workflows/ci.yml/badge.svg)](https://github.com/AndrewVSutherland2/MagmaLSP/actions/workflows/ci.yml)

A language server and Claude Code plugin for the [Magma](http://magma.maths.usyd.edu.au/) computer
algebra system. It gives an LLM (and human) accurate, version-current knowledge of Magma's
intrinsics and a real error signal from Magma itself, so generated Magma is *reliable* and
*idiomatic* rather than merely plausible.

See [`design.md`](design.md) for the *why* and [`CLAUDE.md`](CLAUDE.md) for verified facts about
Magma and the build environment.

## What it does

- **Signature intelligence** from a database built per Magma version (CLAUDE.md §4):
  - package `.m` files → arg names, optional parameters, doc strings, and source locations
    (parsed with [`tree-sitter-magma`](https://github.com/edgarcosta/tree-sitter-magma));
  - `ListSignatures` in a running Magma → completeness, including kernel intrinsics;
  - a `name;` probe that recovers variadic intrinsics (`Sprintf`, `Explode`) *and* harvests doc
    strings + optional-parameter names for kernel intrinsics `ListSignatures` leaves bare
    (doc coverage ~96% of names).
  - Powers **hover**, **completion**, **signature help**, **go-to-definition**, **workspace
    symbols**, plus **keyword search** and **"did you mean" suggestions** (fuzzy + cross-system
    aliases: `FactorInteger` → `Factorization`).
  - **hover** is further enriched with the **handbook prose description** for the intrinsic, pulled from
    the local HTML handbook (`doc/html`).
- **Diagnostics** pushed to the editor after each edit:
  - **Magma-backed** syntax/binding check (CLAUDE.md §5), strategy chosen per file shape:
    plain scripts run parsed-but-not-executed in a never-called-function wrap; package files
    (`intrinsic` declarations) are `Attach`ed; files that don't parse are reported from
    tree-sitter without touching Magma (so unbalanced fragments can't corrupt the check);
  - **static "unknown intrinsic"** check with **spelling suggestions** — flags a call whose target
    is neither a known intrinsic nor defined/imported/forward-declared/`load`-ed in the project
    (a cached workspace scan); reports *all* unknown names at once (Magma stops at the first);
  - **static arity check** — a call with an argument count no overload accepts is flagged
    before Magma ever runs (validated ≈0 false positives on the package corpus + handbook);
  - **pitfall lints** for the mistakes LLMs actually make: `x = 5;` (vs `:=`), `==`/`**`,
    method-call syntax (`L.append(3)`), `True`/`False`, `//` as division, discarded
    `Append(L, x)` results, shadowing an intrinsic the file also calls;
  - **unused-variable lints** (CLAUDE.md §13); tree-sitter syntax errors when Magma is off.
- **Document symbols** (intrinsics, named + assigned functions/procedures, `func<...>` forms).

Built on [`pygls`](https://github.com/openlawlibrary/pygls). The Magma grammar and an opinionated
formatter come from the MIT-licensed [`tree-sitter-magma`](https://github.com/edgarcosta/tree-sitter-magma)
and [`lava`](https://github.com/havarddj/lava) — we reuse rather than reinvent them.

## Requirements

- A **licensed [Magma](http://magma.maths.usyd.edu.au/) installation** (developed and tested
  against V2.29-9). The signature DB is built from *your* install — nothing Magma-owned ships
  with this repo. Without a runnable Magma everything degrades honestly rather than silently:
  the server falls back to static-only diagnostics (tree-sitter syntax errors + the static
  checks), `magma_check`/`magma_run` say so explicitly, and the DB build produces a package-only
  DB (no kernel intrinsics) from the install's package tree.
- **Linux** (macOS is untested).
- [`uv`](https://docs.astral.sh/uv/).
- A **C compiler** and Python headers, to build the tree-sitter grammar (if the system Python
  lacks `Python.h`, `uv python install 3.12` first — uv-managed Pythons bundle headers).

## Install & build

```bash
uv sync --extra dev                # create the venv, build tree-sitter-magma
uv run magma-lsp-build-db          # build the signature DB (needs Magma on PATH); ~30 s
# non-/opt install: also point it at your package tree, e.g.
#   uv run magma-lsp-build-db --package-root /path/to/magma/package
```

`magma-lsp-build-db` reads Magma's **package tree** (the `.m` source library). It defaults to
`/opt/magma/package` and exits if that directory is absent — so if your Magma lives elsewhere,
having the binary on `PATH` is not enough: pass `--package-root <dir>` or set `MAGMA_PACKAGE_ROOT`
to your install's `package/` directory (the kernel-intrinsic half of the build, which does use the
`magma` binary, still finds it via `PATH`/`--magma-path`). It writes a per-version artifact to
`~/.cache/magma-lsp/<version>.magmadb.json` (override with `--out` or `MAGMA_LSP_DB`). The loader
prefers the artifact matching the installed Magma version and warns when it has to serve a stale
one. Without Magma the build still produces a package-only DB (no kernel intrinsics) and prints a
note.

## Use in Claude Code

This repo *is* a Claude Code plugin. Add your clone as a local marketplace and install:

```
/plugin marketplace add /path/to/MagmaLSP
/plugin install magma-lsp@magma-lsp-marketplace
/reload-plugins
```

The plugin maps both `.m` (Magma's usual file suffix — Magma itself uses `.m`) and `.magma` to
languageId `magma`, and launches the server via `uv run`. (`.m` is shared with MATLAB and
Objective-C; in a mixed repo, narrow the mapping or use `.magma` for the files you want treated as
Magma.) Configure via `initializationOptions` in [`.lsp.json`](.lsp.json):
`magmaPath`, `magmaDiagnostics` (bool), `lints` (bool), `magmaTimeout` (seconds), `dbPath`.

### Two front-ends, one core

The plugin bundles **two** ways into the same core intelligence (`src/magma_lsp/frontend.py`):

- **LSP server** ([`.lsp.json`](.lsp.json)) — for the *editor*: pushes diagnostics on edit/save,
  plus hover, completion, go-to-definition, document/workspace symbols.
- **MCP server** ([`.mcp.json`](.mcp.json)) — for the *agent* writing Magma. Five stdio tools
  (auto-started with the plugin, visible in `/mcp` as `magma-lsp`):
  - `magma_guide()` — a one-page, Magma-verified conventions & pitfalls brief (read once);
  - `magma_search(query)` — keyword search over names + docs, for when the agent only knows
    the concept (the hardest small-model failure: not knowing the name at all);
  - `magma_lookup(names)` — signatures + handbook docs; forgiving resolution (case,
    operators) and ranked "did you mean" suggestions on misses;
  - `magma_check(code, execute=True)` — static (names/arity/pitfalls) + Magma syntax/binding
    diagnostics + an execution pass by default; degrades honestly (no DB / no Magma /
    timeout are explicit notes, never a silent "OK");
  - `magma_run(code, timeout=30)` — sandboxed execution with error locations remapped to the
    program's own line numbers and head+tail output truncation.

  These give the agent the execution loop (`run`/`check`) plus the signature DB
  (`search`/`lookup`) — the two levers our evals identified. For frontier models the DB is an
  efficiency layer over execution; for smaller models it is a *capability* lever — Haiku 4.5 with
  these tools plays at tooled-Sonnet level, and the DB's docs fix exactly the silent-wrong
  convention failures raw execution can't see (see [`eval/FINDINGS_3arm.md`](eval/FINDINGS_3arm.md),
  [`eval/FINDINGS_trap.md`](eval/FINDINGS_trap.md), [`eval/FINDINGS_haiku.md`](eval/FINDINGS_haiku.md)).
  The CLI ([`magma-lsp-cli`](src/magma_lsp/cli.py)) exposes the same operations from a shell.

**Execution sandbox:** every `run`/`check` is a fresh, hermetic Magma process under a wall-clock
`timeout` and an in-process `SetMemoryLimit`. In addition, the passes that actually *execute* user
code (`magma_run`, `magma_check(execute=True)`, and the CLI equivalents) run inside a
[bubblewrap](https://github.com/containers/bubblewrap) sandbox whenever `bwrap` is on PATH:
the entire filesystem is remounted **read-only** (`/tmp` becomes a throwaway tmpfs), with fresh
PID/IPC namespaces and no controlling terminal. Relative `load`s resolve through the read-only
root, which exposes every directory except the masked ones — so a source at a normal path loads
its siblings fine, while a source anywhere **under `/tmp` or `/dev`** cannot load a dependency
that is *also* under a masked root: the throwaway tmpfs/devfs hides it whether it's referenced
relatively or by absolute path (only the generated source file is bound back), and the sandbox
deliberately never re-binds a caller-controlled directory over the masks. Keep the source and its
`load` dependencies at a normal path, or grant their directory via `MAGMA_LSP_SANDBOX_WRITABLE`
(an absolute `load` of a file that already lives outside the masked roots works from anywhere).
The read-only remount is recursive: separately-mounted writable filesystems
(a separate `/home`, the `/run/user/<uid>` tmpfs, …) are covered too — bubblewrap remounts
inherited submounts read-only, using `mount_setattr` on kernels ≥ 5.12 — and the test suite
asserts this against a real submount of the host it runs on. Well-known privileged control sockets (Docker, Podman, containerd, CRI-O, libvirt, incl. the
rootless per-user ones) are additionally masked with `/dev/null` where present, since a container
daemon reached through one would mutate host paths on the caller's behalf and defeat the read-only
root. Precisely stated: the sandbox blocks **filesystem mutation** through the normal filesystem —
the worst vector — but does **not** block `System(...)`/`Pipe(...)` shell-out per se and does
**not** block network egress. The socket masking is best-effort defence in depth, not a complete
boundary: because IPC/network isn't blocked, a privileged daemon socket at a path we don't know to
mask remains reachable. Treat the sandbox as preventing casual and accidental filesystem writes
by generated code, not as a hardened boundary against code actively trying to escape. The network namespace must stay shared because Magma's license check reads the
host MAC address (an unshared network namespace makes licensing fail); a shell can therefore
still be spawned, but it runs against the same read-only filesystem. The parse-only diagnostics
passes execute nothing user-level and are not sandboxed, which keeps the every-edit syntax check
at its measured ~12.5 ms.

Policy: **on automatically** when `bwrap` is present and working — a one-time probe detects hosts
where bwrap exists but cannot create namespaces (unprivileged user namespaces disabled, common
inside containers) and falls back rather than failing every run. Set `MAGMA_LSP_NO_SANDBOX=1` in
the server's environment to opt out; without (working) bwrap — e.g. macOS, untested anyway —
execution passes run unsandboxed and a loud one-time warning on stderr says so. Programs that legitimately write output
files can be granted specific directories with `MAGMA_LSP_SANDBOX_WRITABLE=/path/a:/path/b`
(bind-mounted read-write; unset by default). `magma_guide()` reports the live sandbox state, and
the `magma_run`/`magma_check` tool docs tell the agent up front that writes will fail.

## Develop

```bash
uv run pytest              # tests (a few are marked `magma` and skip without a Magma install)
uv run ruff check src tests
uv run ruff format src tests
```

Layout: `src/magma_lsp/{db,magma,analysis}` is the framework-agnostic core; `frontend.py` is the
shared agent-facing logic; `server.py` (LSP), `mcp_server.py` (MCP), and `cli.py` (shell) are the
three thin adapters over it.

## Status

Phase 0 (end-to-end plugin/server channel) and the core of Phase 1 (signature DB + read-only
intelligence) are in place, plus a first slice of Phase 2 (Magma-backed diagnostics). See
`design.md §7` for the staged plan.
