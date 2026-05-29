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
3. **Static analysis Magma itself doesn't provide.** The interpreter only reports parse/run-time errors;
   it never flags unused variables, use-before-assignment, undefined names, or shadowing. A lightweight
   pyflakes-style linter — driven by the parser's scope/binding resolution (see §4, and `locals.scm` from
   the tree-sitter grammar in §5) — catches a class of mistakes *before* code is even run, on every edit.
   Cheap, instant, and a genuine value-add over a bare Magma session.

Classic editor niceties (hover, go-to-definition) mostly serve a *human* typing; provide them, but
they are secondary to the levers above. Don't over-invest in them early.

## 4. Architecture

A shared core with thin front-ends:

- **Core engine**
  - *Signature DB* — built once per Magma version, queried at runtime.
  - *Parser* — for parsing **user source** in the live server (syntax diagnostics, document symbols,
    navigation, the §6 lints), use the existing **`tree-sitter-magma`** grammar (MIT; generated from Magma's
    own yacc grammar; ships Python bindings) rather than hand-rolling a parser — see §5. It gives an
    error-recovering AST plus ready-made `highlights`/`locals`/`indents`/`folds` queries; `locals.scm`
    (scopes + definitions + references) is exactly the machinery for the static lints. A *separate*, simpler
    extractor still scans the package `.m` corpus to build the Signature DB (intrinsic headers + doc strings).
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
5. **Existing open-source Magma tooling (reuse, don't reinvent).** Two MIT-licensed, actively-maintained
   projects already solve parsing/highlighting/formatting:
   - **`tree-sitter-magma`** (https://github.com/edgarcosta/tree-sitter-magma) — a tree-sitter grammar
     *generated from Magma's own yacc grammar*, with **Python bindings** (`tree_sitter_magma`) and
     `queries/{highlights,locals,indents,folds}.scm`. This is the parser foundation for the live server
     (§4): error-recovering AST, document symbols, semantic tokens, and — via `locals.scm` — the scope/binding
     resolution that powers the unused/undefined-variable lints (§3, §6). Moderately mature; expect coverage
     gaps, so pin/vendor a known-good commit, keep a regression corpus, and consider upstreaming fixes.
   - **`lava`** (https://github.com/havarddj/lava) — a Rust CLI built on tree-sitter-magma + topiary providing
     `format`, `highlight`, and a `test` runner. We get **reformatting for free**: use lava directly rather
     than reimplementing a formatter (and note Claude Code's LSP doesn't request `formatting` anyway, so it's
     a CLI/pre-commit nicety, not core). Its `tests/topiary-tests/{input,expected}/*.m` doubles as a ready-made
     parser regression corpus (~30 syntactic constructs).

IP: cleared (see §1) — signatures and docs may be bundled, with attribution. The two tools above are MIT, so
bundling/depending on them is unproblematic (preserve their license/attribution).

## 6. Diagnostics design

Hybrid, fast-to-slow:

- **Static (no Magma process).** Syntax errors from the parser; unknown-intrinsic, arity, and
  argument-type checks against the signature DB. Cheap, instant, runs on every edit.
- **Scope/lint pass (no Magma process).** Pyflakes-style checks from the tree-sitter `locals.scm`
  scope/binding resolution (lever 3, §3): unused variable/parameter, undefined / use-before-assignment
  (cross-checked against the signature DB to avoid flagging intrinsics), and shadowing. Also static, so
  it runs on every edit. *Magma gotcha:* `_` is the discard placeholder in multi-value assignment
  (`a, _ := Foo();`) — never warn on `_`; honor `~ref` params and `where`/quantifier bindings.
- **Dynamic (Magma in the loop).** Deeper validation by checking the code against a real Magma. This
  is the high-value error signal.

**Resolved (was an open question):** Magma has no pure parse-only flag, but a **never-called function wrapper**
parses + binds user code *without executing* it — catching syntax errors and undefined names cheaply; genuine
type/value errors need a real execution pass. Sandbox every check: fresh per-check process (~104 ms cold
start), external `timeout`, `SetMemoryLimit`, and `</dev/null` to avoid stdin hangs. See `CLAUDE.md` §3/§5 for
the verified invocation recipe, error-block format, and exit-code semantics.

## 7. Staged build plan

Prototype in Python first to validate understanding; optimize later and only where measured
(see §8). Keep a growing regression corpus of Magma snippets with expected diagnostics from the
start.

0. **Scaffolding + end-to-end loop.** Repo skeleton, the trivial Claude Code plugin (`.lsp.json` +
   manifest), and a minimal server that attaches and emits one trivial diagnostic — purely to prove
   the Claude Code ↔ server channel works before building anything real.
1. **Signature DB + read-only intelligence (Python prototype).** Build a `.m`-corpus extractor for the DB
   (intrinsic headers + doc strings, resolving `{"}` ditto docs) and merge `ListSignatures`; define the DB
   schema (handle multi-signature intrinsics and optional parameters); build the DB. Wire **`tree-sitter-magma`**
   (§5) in via its Python bindings for user-source parsing. Stand up a `pygls` server providing completion,
   signature help, and hover from the DB, plus syntax diagnostics + document symbols from the tree-sitter AST,
   plus the **scope/lint pass** (§3 lever 3, §6) from `locals.scm`. Validate against real package files, the
   lava test corpus, and real Magma snippets.
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

## 9. Open questions

**Resolved by the recon session** (all findings captured in `CLAUDE.md` — read it for specifics): exact
`ListSignatures` enumeration recipe + output grammar; package-tree / spec layout by area; syntax-check-only
approach (function-wrap trick); handbook HTML structure + name→doc lookup; the intrinsic declaration grammar
incl. edge cases (`{"}` ditto, multi-line headers, operator names, `~ref`/`.`/`Any` args); current Claude Code
`.lsp.json` / plugin-manifest / marketplace specifics.

**Still open:**
- DB modeling of overloaded (multi-signature) intrinsics and optional parameters (schema design).
- Full-DB build time and server query latency (informs any later C work).
- `tree-sitter-magma` coverage vs. real Magma (16 open issues upstream) — which constructs it mis-parses;
  whether to vendor/pin a commit and/or upstream fixes.
- `.m`/`.magma` extension association + languageId behavior inside a live Claude Code session.

## 10. Start here (first task for the Claude Code session)

**Recon is done** — the install layout, `ListSignatures` enumeration, intrinsic grammar, validation behavior,
handbook structure, and the Claude Code plugin format have all been verified first-hand and written up in
`CLAUDE.md` (read it first). Next: begin **Phase 0** (repo skeleton + trivial plugin + minimal server to prove
the Claude Code ↔ server channel), then **Phase 1** — stand up `tree-sitter-magma` (§5) and the `.m`-corpus
extractor, design the DB schema, and validate the parser against the package corpus + the lava test corpus.
