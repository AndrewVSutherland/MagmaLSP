# MagmaLSP

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
  - a `name;` probe to recover variadic intrinsics (`Sprintf`, `Explode`) that `ListSignatures` omits.
  - Powers **hover**, **completion**, **signature help**, **go-to-definition**, and **workspace symbols**.
  - **hover** is further enriched with the **handbook prose description** for the intrinsic, pulled from
    the local HTML handbook (`doc/html`).
- **Diagnostics** pushed to the editor after each edit:
  - **Magma-backed** syntax/binding check via a sandboxed, never-called-function wrap (CLAUDE.md §5) —
    the authoritative undefined-name check when Magma is available;
  - **static "unknown intrinsic"** check — flags a call whose target is neither a known intrinsic nor
    defined/imported/forward-declared in the file, nor defined in a sibling `.m` file of the project
    (a bounded workspace scan); the fast, every-edit, offline complement to the Magma pass;
  - **static lints Magma doesn't provide** — unused/dead local variables (CLAUDE.md §13);
  - tree-sitter syntax errors as a fast fallback when Magma is unavailable.
- **Document symbols** (intrinsics, top-level functions/procedures) from the tree-sitter AST.

Built on [`pygls`](https://github.com/openlawlibrary/pygls). The Magma grammar and an opinionated
formatter come from the MIT-licensed [`tree-sitter-magma`](https://github.com/edgarcosta/tree-sitter-magma)
and [`lava`](https://github.com/havarddj/lava) — we reuse rather than reinvent them.

## Install & build

Requires [`uv`](https://docs.astral.sh/uv/) and a C compiler (to build the tree-sitter grammar).

```bash
uv sync --extra dev                # create the venv, build tree-sitter-magma
uv run magma-lsp-build-db          # build the signature DB (needs Magma on PATH); ~15 s
```

`magma-lsp-build-db` writes a per-version artifact to `~/.cache/magma-lsp/<version>.magmadb.json`
(override with `--out` or `MAGMA_LSP_DB`). Without Magma it still builds a package-only DB
(no kernel intrinsics) and prints a note.

## Use in Claude Code

This repo *is* a Claude Code plugin. Add it as a local marketplace and install:

```
/plugin marketplace add /home/drew/MagmaLSP
/plugin install magma-lsp@magma-lsp-marketplace
/reload-plugins
```

The plugin maps `.magma` (primary) and `.m` (fallback) to languageId `magma` and launches the
server via `uv run`. Configure via `initializationOptions` in [`.lsp.json`](.lsp.json):
`magmaPath`, `magmaDiagnostics` (bool), `lints` (bool), `magmaTimeout` (seconds), `dbPath`.

### Two front-ends, one core

The plugin bundles **two** ways into the same core intelligence (`src/magma_lsp/frontend.py`):

- **LSP server** ([`.lsp.json`](.lsp.json)) — for the *editor*: pushes diagnostics on edit/save,
  plus hover, completion, go-to-definition, document/workspace symbols.
- **MCP server** ([`.mcp.json`](.mcp.json)) — for the *agent* writing Magma. Three stdio tools
  (auto-started with the plugin, visible in `/mcp` as `magma-lsp`):
  - `magma_lookup(names)` — intrinsic signatures + handbook docs (confirm a call before writing it);
  - `magma_check(code, execute=False)` — static + Magma syntax/binding diagnostics, optional run;
  - `magma_run(code, timeout=30)` — execute in a sandboxed Magma and return the output.

  These give the agent the execution loop (`run`/`check`) plus the signature DB (`lookup`) — the
  two levers our evals identified (see [`eval/FINDINGS_3arm.md`](eval/FINDINGS_3arm.md),
  [`eval/FINDINGS_trap.md`](eval/FINDINGS_trap.md)). The CLI ([`magma-lsp-cli`](src/magma_lsp/cli.py))
  is the same three operations from a shell.

**Execution sandbox (current):** every `run`/`check` is a fresh, hermetic Magma process under a
wall-clock `timeout` and an in-process `SetMemoryLimit`. This suits a trial with **trusted users**;
it does *not* yet block Magma `System(...)`/`Pipe(...)` shell-out, file writes, or network — add OS
isolation (restricted user / cgroup / namespace) before exposing it to untrusted input.

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
