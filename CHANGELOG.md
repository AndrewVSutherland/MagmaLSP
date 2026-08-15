# Changelog

## 0.1.0 — initial public release (2026-08)

- Per-Magma-version signature DB (package `.m` + `ListSignatures` + variadic/doc probes) powering
  hover, completion, signature help, search, and "did you mean" suggestions.
- Magma-backed diagnostics with per-file-shape strategies, plus static unknown-intrinsic, arity,
  pitfall, and unused-variable checks.
- Three front-ends over one core: LSP server, MCP tools (`guide`/`search`/`lookup`/`check`/`run`),
  and a CLI — packaged as a Claude Code plugin.
