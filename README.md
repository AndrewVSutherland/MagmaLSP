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
  - `ListSignatures` in a running Magma → completeness, including kernel intrinsics.
  - Powers **hover**, **completion**, **signature help**, and **go-to-definition**.
- **Diagnostics** pushed to the editor after each edit:
  - **Magma-backed** syntax/binding check via a sandboxed, never-called-function wrap (CLAUDE.md §5);
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

## Develop

```bash
uv run pytest              # tests (a few are marked `magma` and skip without a Magma install)
uv run ruff check src tests
uv run ruff format src tests
```

Layout: `src/magma_lsp/{db,magma,analysis}` is the framework-agnostic core; `server.py` is the
thin pygls adapter. A later MCP front-end would be a second thin adapter over the same core.

## Status

Phase 0 (end-to-end plugin/server channel) and the core of Phase 1 (signature DB + read-only
intelligence) are in place, plus a first slice of Phase 2 (Magma-backed diagnostics). See
`design.md §7` for the staged plan.
