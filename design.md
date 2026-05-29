# Magma Language Server + Claude Code Plugin — Design & Handover

> **Purpose.** This document hands the project from an architecture/planning conversation into a
> Claude Code build session. It records the decisions made and *why*, how to access each resource
> on the VM, a staged build plan, working conventions, and the open questions to resolve
> empirically. It is deliberately **not** an exhaustive spec — later phases should adapt as the real
> package layout and `ListSignatures` output are observed. Keep it in the repo as `DESIGN.md` and/or
> use it to seed `CLAUDE.md`.

## 1. Goal

Build a Magma language server plus a thin Claude Code plugin that wires it in, so Magma users —
mostly research mathematicians who are not necessarily programmers — can use Claude Code to write
reliable, idiomatic Magma for their research. Claude can already produce *plausible* Magma; the
point of this project is to make it *reliable* and *efficient* by giving the agent (a) accurate,
version-current knowledge of Magma's intrinsics, and (b) a real error signal from Magma itself.

**Institutional context.** Developed under the Simons Foundation–funded joint MIT–University of
Sydney project (co-PIs Drew and John Voight); two Magma developers are at MIT. The intended endpoint
is for the server and plugin to become official Magma add-ons. Consequently there is **no IP
obstacle** to bundling Magma's signatures or documentation (with attribution).

## 2. How Claude Code uses an LSP (and why the plugin is the easy part)

Claude Code gained LSP support (early 2026) through its plugin system. A plugin supplies a
`.lsp.json` that maps file extensions to a language-server command; once attached, the server
**pushes diagnostics to Claude automatically after each edit**, and Claude can request hover,
signature help, go-to-definition, and symbol search on demand. The plugin does *not* contain the
server — it only configures the connection.

Implication: the Claude Code plugin is a few lines of JSON plus a manifest. The engineering is the
**Magma language server itself**. (Verify the current plugin manifest fields and marketplace
packaging against the live Claude Code docs in-session — the format may have moved since this was
written.)

Note the `.m` extension collides with MATLAB and Objective-C. Plan to disambiguate (content
heuristics and/or supporting an explicit `.magma` extension or project config).

## 3. Where the value actually is

Prioritize the two levers that make Claude write *correct* Magma:

1. **A signature database.** Accurate, version-current intrinsic signatures (names, argument types,
   optional parameters, return types, doc strings) so the agent stops inventing function names and
   argument types — the dominant failure mode for any LLM writing Magma. Powers completion,
   signature help, hover, and the "unknown intrinsic / wrong arity / wrong argument type" static
   diagnostics.
2. **A Magma-backed error signal.** A way to actually check generated code against Magma and surface
   its real errors, so the agent self-corrects within the same turn instead of handing the user
   broken code.

Classic editor niceties (hover, go-to-definition) mostly serve a *human* typing; provide them, but
they are secondary to the two levers above. Don't over-invest in them early.

## 4. Architecture

A shared core with thin front-ends:

- **Core engine**
  - *Signature DB* — built once per Magma version, queried at runtime.
  - *Parser* — tokenizes/parses Magma source for syntax diagnostics and structural features
    (document symbols, navigation).
  - *Validation backend* — shells out to a running Magma to check code (see §6).
- **LSP front-end** (proposed: `pygls`) — consumes the core; this is what the Claude Code plugin
  attaches to.
- **MCP front-end** (later, optional) — the same core exposed as MCP tools. Natural sibling given the
  existing LMFDB MCP server; lets non–Claude-Code contexts use the signature/validation tools. Not
  required for v1.
- **Claude Code plugin** — `.lsp.json` mapping Magma files to the server command, plus
  manifest/marketplace packaging.

Build the core so the LSP and MCP front-ends are thin adapters over it, not parallel
implementations.

## 5. Data sources and how to access them (on the VM)

1. **Magma package files** (`.m`, the Magma-language standard library). Primary signature source for
   library intrinsics. Each declaration has the regular shape:
   ```
   intrinsic Name(x::RngIntElt, S::SeqEnum : Al := "Default") -> RngIntElt
   { The one-line description that ?Name prints }
       ...
   end intrinsic;
   ```
   Parse these for name, argument types, optional parameters, return type(s), and the `{ ... }` doc
   string. Notes: a single name may carry several signatures (overloading on argument types);
   signatures can span multiple lines; the doc block immediately follows the header. Locate the
   package tree / spec files in the install (layout to be confirmed in-session). These files are
   also a large corpus of idiomatic Magma — use them as a parser-validation corpus and as
   house-style reference.
2. **`ListSignatures` in a running Magma.** Exposes intrinsic signatures including **kernel-defined**
   intrinsics that are *not* present in the package files (confirmed by Drew). This is the
   authoritative full enumeration; merge it with the package parse (package files add doc strings and
   idioms; `ListSignatures` adds completeness). The exact invocation to enumerate the full set and
   its output format are to be determined and parsed in-session.
3. **HTML handbook.** Prose documentation and worked examples; has Chapters / Examples / Intrinsics
   indices and a global INDEX. Use for richer hover docs and examples; secondary to (1)–(2) for
   signatures. Available locally in the install `doc` directory and online.
4. **PDF handbook.** Reference/fallback copy of the same content.

IP: cleared (see §1) — signatures and docs may be bundled, with attribution.

## 6. Diagnostics design

Hybrid, fast-to-slow:

- **Static (no Magma process).** Syntax errors from the parser; unknown-intrinsic, arity, and
  argument-type checks against the signature DB. Cheap, instant, runs on every edit.
- **Dynamic (Magma in the loop).** Deeper validation by checking the code against a real Magma. This
  is the high-value error signal.

**Open question that shapes this:** does Magma have a parse / syntax-check-only mode, or does
checking require actually executing the code (with side effects and runtime cost)? Determine
in-session. If execution is the only option, sandbox it — per-check process with time and memory
limits, no persistent side effects — and treat it as opt-in / on-save rather than on every
keystroke.

## 7. Staged build plan

Prototype in Python first to validate understanding; optimize later and only where measured
(see §8). Keep a growing regression corpus of Magma snippets with expected diagnostics from the
start.

0. **Scaffolding + end-to-end loop.** Repo skeleton, the trivial Claude Code plugin (`.lsp.json` +
   manifest), and a minimal server that attaches and emits one trivial diagnostic — purely to prove
   the Claude Code ↔ server channel works before building anything real.
1. **Signature DB + read-only intelligence (Python prototype).** Parse package files; merge
   `ListSignatures`; define the DB schema (handle multi-signature intrinsics and optional
   parameters); build the DB. Stand up a `pygls` server providing completion, signature help, and
   hover from the DB, plus syntax diagnostics from the parser. Validate against real package files
   and real Magma snippets.
2. **Magma-backed validation.** Add the dynamic diagnostics backend from §6, sandboxed. Delivers the
   in-turn self-correction lever.
3. **Hardening, performance, packaging.** Profile; port only hot paths to C if needed (much of the
   server may remain Python). Robust handbook-doc integration. Package toward an official Magma
   add-on. Optionally add the MCP front-end.

## 8. Working conventions

- Functional, non-optimized **Python prototype first** for any new algorithm, used to validate
  understanding and to test later optimized versions.
- **C only for hot paths identified by profiling.** When writing C: assume performance matters and
  target a modern x86 CPU with efficient 128-bit arithmetic and AVX-512 (Intel Icelake-server+ /
  AMD Zen3+); prefer explicit integer types (`int32_t`, `int64_t`, `int128_t`) over generic ones;
  avoid `const` in type declarations.
- Maintain awareness of what already exists in the workspace (code/data from earlier work); don't
  ask questions answerable by inspecting it.
- Keep a regression test corpus of Magma snippets with expected diagnostics; grow it as bugs surface.

## 9. Open questions to resolve in-session

- Exact `ListSignatures` invocation to enumerate all intrinsics, and its output format for parsing.
- Package-tree / spec-file layout in the install; how packages are organized by area.
- Whether Magma supports a parse / syntax-check-only mode (§6).
- Precise handbook HTML structure for the intrinsic and INDEX pages (for doc extraction).
- DB modeling of overloaded (multi-signature) intrinsics and optional parameters.
- Full-DB build time and server query latency (informs any later C work).
- Current Claude Code plugin manifest / marketplace specifics (verify against live docs).

## 10. Start here (first task for the Claude Code session)

On the VM: inspect the Magma install layout (package tree, `doc` directory). Capture a small sample —
the `ListSignatures` output for a couple of intrinsics (one package-level, one kernel-level) and one
or two representative package files (arithmetic-geometry flavored is ideal). Confirm the `intrinsic`
declaration grammar against those files. Then begin Phase 1: write the declaration parser and the DB
schema, validating the parser against the sample files.
