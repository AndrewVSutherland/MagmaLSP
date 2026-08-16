# MagmaLSP

[![CI](https://github.com/AndrewVSutherland/MagmaLSP/actions/workflows/ci.yml/badge.svg)](https://github.com/AndrewVSutherland/MagmaLSP/actions/workflows/ci.yml)

A language server and Claude Code plugin for the [Magma](http://magma.maths.usyd.edu.au/)
computer algebra system.

Magma ships ~10,000 intrinsics whose names, argument types, and return conventions are hard
to remember (for people) and hard to guess (for LLMs). MagmaLSP builds a database of every
intrinsic in *your* Magma installation and puts it behind two front-ends: an **LSP server**
for editors (completion, hover, diagnostics as you type, go-to-definition) and an **MCP
server** for coding agents (look up signatures, check and run Magma code, read real Magma
errors). See [`design.md`](design.md) for the why and [`CLAUDE.md`](CLAUDE.md) for verified
facts about Magma itself.

## What you get

**A signature database built from your install.** The build (`magma-lsp-build-db`, ~30 s)
parses the package `.m` sources shipped with Magma (argument names, optional parameters, doc
strings, source locations) and cross-checks a running Magma's own `ListSignatures`
enumeration (which adds the kernel intrinsics that exist only in C). The result: every
intrinsic your Magma actually registers, with its overloads and documentation, current for
your exact version — nothing hard-coded, nothing shipped, no Magma content redistributed.

**In your editor** (any LSP-capable editor — see [Editor setup](#editor-setup)):

- **Diagnostics after every edit**: real Magma parses your code on open/save (exact
  syntax/binding errors with positions, without executing anything); between saves, fast
  static checks flag *unknown intrinsics* (with "did you mean" suggestions), *calls whose
  argument count no overload accepts*, common pitfalls (`=` vs `:=`, `==`, `L.append(x)`
  method-call syntax, `True`/`False`, discarded in-place results), and unused variables.
- **Completion, hover, and signature help** for all ~10k intrinsics: every overload, doc
  strings, optional parameters, plus the handbook's prose description on hover.
- **Go-to-definition** jumps into the package source that defines an intrinsic. Magma is
  dynamically typed and the server does **no type inference**, so for an overloaded name like
  `Dimension` it cannot know which overload your call resolves to — it returns *all*
  definition sites (documented ones first) and your editor shows a picker. Kernel-only
  intrinsics (defined in C, e.g. `Type`) have no `.m` source to jump to.
- **Your project's code is first-class**: the server scans the workspace's `.m`/`.magma`
  files — including files listed by any `*.spec` package spec it finds — so intrinsics and
  functions you define in sibling files are recognized by the unknown-intrinsic check,
  completable, listed in workspace-symbol search, and valid go-to-definition targets.

**For a coding agent** (Claude Code or any MCP client): five stdio tools — `magma_guide`
(a one-page conventions/pitfalls brief), `magma_search` (find the intrinsic when you only
know the concept), `magma_lookup` (exact signatures + docs), `magma_check` (all the
diagnostics above **plus an execution pass by default** — unlike the editor path, which
never executes), and `magma_run` (sandboxed execution with errors mapped to your line
numbers). Execution with real error text is the single biggest lever for getting LLM-written
Magma correct; the DB is what catches the *silently wrong* conventions a clean run can't
(details and measurements: [`eval/`](eval/)).

## Requirements

- A **licensed [Magma](http://magma.maths.usyd.edu.au/) installation** (developed and tested
  against V2.29-9). Without a runnable Magma everything degrades honestly rather than
  silently: static-only diagnostics, explicit notes from `magma_check`/`magma_run`, and a
  package-only DB.
- **Linux** (macOS is untested).
- [`uv`](https://docs.astral.sh/uv/), and a **C compiler** + Python headers to build the
  tree-sitter grammar (if the system Python lacks `Python.h`: `uv python install 3.12` —
  uv-managed Pythons bundle headers).

## Install & build the database

```bash
git clone https://github.com/AndrewVSutherland/MagmaLSP && cd MagmaLSP
uv sync --extra dev                # create .venv, build tree-sitter-magma
uv run magma-lsp-build-db          # build the signature DB (needs Magma); ~30 s
```

`magma-lsp-build-db` reads Magma's **package tree** (default `/opt/magma/package`; if your
Magma lives elsewhere pass `--package-root <dir>` or set `MAGMA_PACKAGE_ROOT` — having
`magma` on PATH is not enough by itself). It writes a per-version artifact to
`~/.cache/magma-lsp/<version>.magmadb.json` (override: `--out` / `MAGMA_LSP_DB`); the server
prefers the artifact matching the installed Magma and warns when serving a stale one.

## Editor setup

The language server binary is **`.venv/bin/magma-lsp`** inside your clone after `uv sync`
(equivalently: `uv run --project /path/to/MagmaLSP magma-lsp`). It speaks stdio. Point any
LSP client at it with filetype/language `magma`; examples:

**Neovim ≥ 0.11** (built-in LSP config):

```lua
-- .m also belongs to MATLAB/Objective-C: keep the mapping if your repos are Magma-only,
-- drop it (using .magma) if they're mixed.
vim.filetype.add({ extension = { magma = "magma", m = "magma" } })

vim.lsp.config("magma_lsp", {
  cmd = { "/path/to/MagmaLSP/.venv/bin/magma-lsp" },
  filetypes = { "magma" },
  root_markers = { ".git" },
})
vim.lsp.enable("magma_lsp")
```

**Emacs** (eglot, with [magma-mode](https://github.com/ThibautVerron/magma-mode)):

```elisp
(add-to-list 'eglot-server-programs
             '(magma-mode . ("/path/to/MagmaLSP/.venv/bin/magma-lsp")))
```

**VS Code** has no built-in generic LSP client; use Claude Code's plugin support (below) or
a generic LSP-client extension.

(These are reference configurations, not CI-tested; if you wire up another editor, a PR
adding it here is welcome.)

## Use in Claude Code

This repo *is* a Claude Code plugin bundling both front-ends — the LSP server (via
[`.lsp.json`](.lsp.json)) and the MCP tools (via [`.mcp.json`](.mcp.json), visible in `/mcp`
as `magma-lsp`). Add your clone as a local marketplace and install:

```
/plugin marketplace add /path/to/MagmaLSP
/plugin install magma-lsp@magma-lsp-marketplace
/reload-plugins
```

The plugin maps both `.m` (Magma's usual suffix) and `.magma` to language `magma`; in a repo
that mixes MATLAB/Objective-C `.m` files, narrow the mapping in `.lsp.json`. The same core
is scriptable from a shell as [`magma-lsp-cli`](src/magma_lsp/cli.py)
(`guide`/`search`/`lookup`/`check`/`run`).

Note one deliberate asymmetry: the MCP `magma_check` **executes the code by default**
(agents want runtime errors; pass `execute=False` for a parse-only check), while the editor
diagnostics path and the CLI `check` never execute unless asked.

## Execution sandbox

`magma_run` and `magma_check(execute=True)` run user code. Every run is a fresh, hermetic
Magma process under a hard wall-clock timeout, a memory limit, and bounded output capture —
and where [bubblewrap](https://github.com/containers/bubblewrap) is available (most Linux
distros), the process additionally sees a **read-only filesystem**: file writes by executed
code fail, with `/tmp` a throwaway tmpfs. Shell-out and network egress are deliberately
*not* blocked (Magma's license check needs the host network identity), so treat this as
protection against casual/accidental writes, not a hardened boundary. Opt out with
`MAGMA_LSP_NO_SANDBOX=1`; grant specific writable directories with
`MAGMA_LSP_SANDBOX_WRITABLE=/path/a:/path/b`. Full details, rationale, and `load`-path
semantics: [`docs/sandbox.md`](docs/sandbox.md).

## Configuration

`initializationOptions` (in `.lsp.json` for Claude Code, or your editor's LSP settings):

| Option | Default | Meaning |
|---|---|---|
| `magmaPath` | auto-detect | Path to the `magma` wrapper (else `MAGMA_PATH`, then PATH, then `/opt/magma/magma`) |
| `magmaDiagnostics` | `true` | Run the real-Magma syntax/binding pass on open/save |
| `magmaTimeout` | `10.0` | Seconds allowed for that pass |
| `lints` | `true` | Pitfall + unused-variable lints |
| `unknownIntrinsics` | `true` | Static unknown-intrinsic check (needs the DB) |
| `workspaceSymbols` | `true` | Scan the workspace (and its `*.spec` files) for project symbols |
| `workspaceMaxFiles` | `2000` | Workspace scan cap; larger projects skip the scan |
| `handbook` | `true` | Enrich hover with the HTML handbook's prose |
| `handbookDir` | auto-detect | Handbook location (default `<install>/doc/html`) |
| `dbPath` | newest cached | Signature DB artifact to load |

Environment variables: `MAGMA_PATH`, `MAGMA_PACKAGE_ROOT`, `MAGMA_LSP_DB`,
`MAGMA_LSP_NO_SANDBOX`, `MAGMA_LSP_SANDBOX_WRITABLE`.

## Develop

```bash
uv run pytest              # tests marked `magma` skip without a Magma install
uv run ruff check src tests
```

Layout: `src/magma_lsp/{db,magma,analysis}` is the framework-agnostic core; `frontend.py`
the shared agent-facing logic; `server.py` (LSP), `mcp_server.py` (MCP), and `cli.py`
(shell) are thin adapters over it. Validation harnesses that check the DB and diagnostics
against all of Magma's own packages live in `validation/`.

## Status

v0.1.x: signature DB, editor intelligence, Magma-backed diagnostics, sandboxed execution,
MCP tools. See `design.md §7` for the staged plan.
