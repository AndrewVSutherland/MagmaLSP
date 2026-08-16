# Changelog

## Unreleased

- Validation hardening (#15): a non-Magma executable or licensing failure can no longer read
  as a clean check (computed ready sentinel in every Magma pass); bounded output capture in
  the runner (a print-flooding program can't OOM the server); LSP positions are correct on
  non-ASCII lines (UTF-16/byte/code-point conversions centralized); timed-out DB
  enumerations/probes are rejected instead of saved as complete; the no-`timeout`-binary
  fallback kills the whole process group; `mcp` pinned `<2`.
- Workspace intelligence + README overhaul (#16): the workspace scan now records definition
  *sites* (and follows project `*.spec` files), so project-defined intrinsics/functions are
  completable, searchable via workspace/symbol, and go-to-definition targets;
  go-to-definition returns *all* overload locations (no type inference — the editor shows a
  picker) instead of an arbitrary first one. README rewritten around setup + honest feature
  expectations, with an editor-setup section and a configuration table; sandbox deep-dive
  moved to `docs/sandbox.md`; stale `AndrewVSutherland2` URLs fixed.

## 0.1.0 — initial public release (2026-08)

- Per-Magma-version signature DB (package `.m` + `ListSignatures` + variadic/doc probes) powering
  hover, completion, signature help, search, and "did you mean" suggestions.
- Magma-backed diagnostics with per-file-shape strategies, plus static unknown-intrinsic, arity,
  pitfall, and unused-variable checks.
- Three front-ends over one core: LSP server, MCP tools (`guide`/`search`/`lookup`/`check`/`run`),
  and a CLI — packaged as a Claude Code plugin.
- Execution sandbox: `run` / `check(execute=True)` run inside a bubblewrap read-only-filesystem
  sandbox where available (masked container-daemon sockets; on by default, opt out with
  `MAGMA_LSP_NO_SANDBOX`, grant writable dirs with `MAGMA_LSP_SANDBOX_WRITABLE`), on top of the
  wall-clock timeout and in-process memory limit. Blocks casual/accidental filesystem writes by
  generated code; shell-out and network are not blocked (Magma licensing needs the host network).
