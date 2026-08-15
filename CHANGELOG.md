# Changelog

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
