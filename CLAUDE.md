# CLAUDE.md — Magma Language Server + Claude Code Plugin

Operational reference for building this project. Read alongside [`design.md`](design.md), which holds the
*why* (goals, architecture rationale, staged plan). This file holds the *verified facts* about the
environment and Magma, so build sessions don't have to re-discover them. **Everything below was tested
first-hand on this VM (Magma v2.29-7) unless explicitly marked inferred/LOW-confidence.**

---

## 1. What we're building

A **Magma language server** (the real engineering) plus a **thin Claude Code plugin** that wires it in, so
research mathematicians can use Claude Code to write reliable, idiomatic Magma. The two highest-value levers
(per design.md §3):

1. **Signature DB** — accurate, version-current intrinsic signatures so the agent stops inventing function
   names / argument types.
2. **Magma-backed error signal** — actually check generated code against real Magma so the agent
   self-corrects in-turn.

IP is cleared (Simons-funded MIT–Sydney project; endpoint is an official Magma add-on) — signatures and docs
may be bundled with attribution.

---

## 2. System & access (verified)

⚠️ **The project has moved hosts.** Originally built on a GCP VM (`claude-magma`, 192 vCPU,
Magma 2.29-7 — the source of some historical numbers below, marked where they differ).
Current environment (verified 2026-08):

| Thing | Value |
|---|---|
| Host | local AMD Ryzen AI Max+ 395, Linux, **16 cores / 32 vCPU / 128 GB RAM** |
| Magma version | **2.29-9** (`magma -V` → `V2.29-9`) |
| Binary | `/opt/magma/magma.exe` |
| Wrapper | `/usr/local/bin/magma` (a copy of the wrapper, not a symlink — derive the install dir by *probing*, not `realpath`; the code checks `dirname(realpath(magma))` then falls back to `/opt/magma`) |
| License | `/opt/magma/magmapassfile`; set by the wrapper |
| Repo | `https://github.com/AndrewVSutherland/MagmaLSP` — fetch/push confirmed, branch `main` (moved from `AndrewVSutherland2`, which still redirects) |

**The wrapper** (`/opt/magma/magma`) sets the env Magma needs and `exec`s the binary. If you call
`magma.avx2.exe` directly **without** `MAGMAPASSFILE` you get `Error: MAGMAPASSFILE not set.` — so **always
invoke the wrapper** (or replicate its env). The wrapper exports: `MAGMAPASSFILE`,
`MAGMA_SYSTEM_SPEC=/opt/magma/package/spec`, `MAGMA_STARTUP_FILE=$HOME/.magmarc` (if it exists),
`MAGMA_LIBRARY_ROOT=/opt/magma/libs`, `MAGMA_HTML_DIR=/opt/magma/doc/html`, and on Linux
`MKL_SERIAL=YES OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1` (single-threaded by default → deterministic).

Install layout: `/opt/magma/{package, doc, libs, InternalHelp, ThirdParty}`.

---

## 3. ⚠️ The golden Magma invocation recipe

**Every** programmatic Magma call MUST follow this pattern:

```bash
timeout <T> magma -b -n /tmp/<topic>-<uuid>.m </dev/null 2>&1
```

Non-negotiable pieces, each learned the hard way:

- **`</dev/null` is mandatory.** Without it Magma can **block forever reading stdin** after certain runtime
  errors (e.g. `Bad argument types`). This caused real hangs during recon. Redirecting stdin to EOF makes it
  terminate cleanly.
- **`timeout <T>`** is the hard wall-clock backstop (external `timeout` verified: a `while true` loop is
  killed, exit 124). Magma's own `-l` flag is **not** a usable resource limit (verified — it's ignored).
- **`-b`** = batch (suppresses banner / "total time" line). **`-n`** = no startup file (hermetic; ignores the
  user's `~/.magmarc`). Cold start is ~**11 ms** on the current box (was ~104 ms on the GCP VM), so
  **fresh-process-per-check is cheap and correct** — never reuse a process.
- **Unique temp filenames** (collisions between parallel checks are real).

**Recommended preamble** for a server-driven check (prevents output corruption):
```magma
SetColumns(0);        // MANDATORY: disables 80-col line wrapping (else signatures/error echoes wrap mid-token)
SetAutoColumns(false);
SetEchoInput(false);  // keeps stdout to results + error blocks only
SetIgnorePrompt(true);
// then optionally: SetMemoryLimit(<bytes>);  SetQuitOnError(true);
```

**Memory limit** (in-process, reliable): `SetMemoryLimit(<bytes>);` — verified it blocks an over-budget
allocation with `System Error: User memory limit has been reached`. **OS `ulimit -v` does NOT work** (Magma's
allocator escapes vsize); for a hard OS bound use a cgroup (`systemd-run --scope -p MemoryMax=…`).

**Reproducibility**: `magma -b -S <seed> …` seeds the PRNG deterministically (verified).

### 3b. OS execution sandbox (bubblewrap) — verified 2026-08-13, bwrap 0.11.0

Execution passes (`magma_run`, `magma_check(execute=True)`, eval scoring — NOT the parse-only
syntax strategies) run under bwrap when present (`magma/runner.py:_sandbox_argv`, policy
`sandbox_state()`: active/disabled/unavailable/**broken** — a cached one-shot probe catches
hosts where bwrap exists but user namespaces are disabled (common in containers) and falls back
to unsandboxed-with-a-warning; opt-out `MAGMA_LSP_NO_SANDBOX=1`, writable escape hatch
`MAGMA_LSP_SANDBOX_WRITABLE=dir:dir`). Verified facts that constrain the recipe:

- Working shape (tested end-to-end: licensed run OK, `System("touch …")` write FAILS):
  `timeout N bwrap --ro-bind / / --dev /dev --proc /proc --tmpfs /tmp [--ro-bind cwd cwd]
  --ro-bind <src.m> <src.m> --unshare-pid --unshare-ipc --new-session --die-with-parent
  [--chdir cwd] magma -b -n <src.m>`. `timeout` stays OUTSIDE bwrap (wall clock kills the
  whole tree; `--die-with-parent` + the PID namespace handle the inner side).
- ⚠️ **`--unshare-net` BREAKS Magma licensing**: the license check reads the host MAC address
  and fails with "This host has the following MAC address(es): <empty>". Never add it. Beware
  `magma -V` SKIPS the license check — a `-V` probe under `--unshare-net` misleadingly
  succeeds; test with a real program.
- Therefore the sandbox blocks filesystem *mutation* but NOT shell-out or network egress —
  the README/docstrings say exactly that; don't overclaim. Best-effort defence in depth:
  well-known privileged daemon sockets (docker/podman/containerd/crio/libvirt, incl. rootless
  per-user) are `/dev/null`-overmounted where present (`_socket_masks`, verified connect()
  then fails), since a reachable container daemon would mutate host paths and defeat the RO
  root — but a privileged socket at a path we don't enumerate is still reachable (IPC/network
  aren't blocked). The honest posture is "prevents casual/accidental writes", not "hardened
  against active escape" (codex PR #14 round 6; going further = allowlisted-root view, a
  design decision for the owner).
- The `--ro-bind / /` read-only IS recursive over inherited submounts (verified empirically:
  inside the sandbox, writes to `/run/user/1000` — a separate user-writable tmpfs — plus
  `/run` and `/var/tmp` all fail on bwrap 0.11.0/kernel 7.x; bubblewrap uses
  `mount_setattr(AT_RECURSIVE)` on kernels ≥ 5.12). Do not confuse this with the
  `--remount-ro` option, whose `--help` line says "does not recursively remount" — that
  caveat is about `--remount-ro`, not `--ro-bind` (a codex review conflated them, PR #14
  round 3). `test_separate_writable_submounts_are_read_only` re-verifies on each host.
- The temp `.m` is written to `tempfile.gettempdir()` (often under `/tmp`) and `--ro-bind`-ed
  back over the `--tmpfs /tmp`, which recreates the path inside the tmpfs — keep that shape.
- Mount ORDER matters (later shadows earlier): ro root → **/dev and /proc FIRST among the
  overmounts** (with `TMPDIR=/dev/shm` the temp source lives under /dev; a later `--dev`
  would hide it — every run fails "Can't open file" with rc 0, caught by codex on PR #14) →
  tmpfs /tmp → Magma exe file/dir if masked → user writable binds → source file ro → daemon
  socket masks LAST. **The caller's cwd is NEVER re-bound** (only `--chdir`ed into when
  visible): reproducing it with a bind repeatedly re-exposed masked mounts (codex PR #14
  rounds 3/6/7/8/10 — `/`, `/proc`, host `/tmp`, docker sockets), and relative `load`s
  already resolve through the read-only root for every non-masked directory. No bind ever
  replaces a masked root wholesale (the exe path binds just the *file* when it sits directly
  at /tmp or /dev). **By design** (drop-cwd-rebind, owner decision 2026-08-14): a source
  anywhere under a masked root (`filename="/tmp/proj/main.m"`) cannot `load` a dependency that
  is ALSO under a masked root — the mask hides it, relative OR absolute (only the generated
  source file is bound back), and cwd is not rebound. Workaround: keep source + deps at a
  non-masked path, or grant their directory via `MAGMA_LSP_SANDBOX_WRITABLE` (also
  chdir-visible). An absolute `load` of a file that already lives OUTSIDE the masked roots
  works from a /tmp source (verified); "use an absolute path" alone does NOT help when the
  dependency is itself under /tmp (codex PR #14).

---

## 4. Signature data sources (the heart of the DB)

Three complementary sources. **Merge strategy**: `ListSignatures` for completeness (incl. kernel intrinsics) +
package `.m` for doc strings, optional parameters, and idioms + handbook HTML for prose/examples.

### 4a. `ListSignatures` in a running Magma — authoritative full enumeration

- `ListSignatures(C)` — prints every signature **mentioning** category `C` (as an arg type **or** return type).
- `ListSignatures(F, C)` — signatures of intrinsic `F` involving `C`.
- ⚠️ **`ListCategories()` is a print-only PROCEDURE — it does NOT return a value.** `cats := ListCategories();`
  errors. There are **761** categories.
- ⚠️ `ListSignatures` output shows **positional args + return types but NOT optional parameters** (the
  `: P := default` part). Optional params come only from the package `.m` files (or the handbook).

**Full-enumeration recipe** (total wall time **~4 s**):
1. Capture category names: `echo 'ListCategories();' | magma -b </dev/null > cats.txt` (761 names, one/line).
2. Loop over names, turning each string into a `Cat` constant via `eval`, guarded by `try/catch`
   (6 parametric formers — `Aut, GSet, GaloisData, PowerGroup, Set, Thue` — can't be `eval`'d bare and are
   skipped; their sigs are reached via other categories anyway). **Don't name a variable `cat`** (collides with
   the `cat` concat operator). Prepend `SetColumns(0);`.
3. Dedup: strip `Signatures relevant to …:` headers + blanks, trim trailing ws, `sort -u`.

Result: **27,210 unique signature lines / 10,252 unique intrinsic names.** Raw output is ~259k lines (each sig
is reprinted under every category it mentions — `sort -u` is essential).

**Output line grammar:** `NAME(arg1::Type, arg2::Type, ...) -> ret1, ret2, ...`
- `NAME` = bareword identifier, or single-quoted operator: `'+' '-' '*' '/' '^' '#' '!' '!!' '.' '@' '@@'
  'and' 'or' 'in' 'eq' 'ne' 'cat' 'div' 'mod' 'meet' 'join' 'diff'` and in-place mutators `'+:=' '*:=' …`.
- Parametrized / element-of types use **square brackets**: `SeqEnum[RngOrdFracIdl]`, `CrvHyp[FldFin]`.
- **Reference / in-place args** are prefixed with `~` on the var: `'*:='(~D::LieRepDec, c::RngIntElt)`.
- **Untyped (Any) reference args** render as the angle-bracket token `<unknown>` (only angle-bracket form seen).
- **Procedures** (no return) simply have **no `->`** and end after `)`.
- Kernel-only examples (in `ListSignatures` but in NO `.m`): `Abs`, `AbsoluteOrder`, `AbsolutePrecision`,
  `AbelianBasis`, `Category`, `Type`.

⚠️ **`ListSignatures(C)` silently OMITS variadic intrinsics** (verified: `ListSignatures(MonStgElt)`
contains neither `Sprintf` nor any `...` line, yet `Sprintf` is an intrinsic). The earlier recon's
"no `...` variadic token" claim was wrong — the REPL `name;` form *does* show them, e.g.
`Sprintf(S::MonStgElt, ...) -> MonStgElt` and `Explode(x::SeqEnum) -> ., ...`. So the category
enumeration misses variadic kernel intrinsics. **Recovery**: probe candidate names (call-targets
harvested from the package corpus that aren't already in the DB) with `eval("<name>;")` — `eval`
(not a bare `name;`) is required so a reserved-word candidate raises a *catchable runtime* error
instead of a fatal parse error that aborts the whole batch. In practice only a couple of
commonly-used intrinsics are recovered this way (`Explode`, `Sprintf`); the ~17k other missing
call-targets are legitimate **package-local functions**, not intrinsics.

**Static "unknown intrinsic" diagnostic — viable with scope modeling.** A call `Foo(...)` is
genuinely undefined in Magma unless `Foo` is an intrinsic, defined locally, `forward`-declared, or
**`import`ed** (`import "file.m": Foo;`) — e.g. `ChangeBaseRing` is a package-local function, not an
intrinsic, and errors if called without an import. So flag a call only when its target is *neither*
in the signature DB *nor* available in the document (defined/imported/forward/bound — see
`analysis/scope.py`). Measured false-positive rate on the package corpus (worst case, with cross-file
package siblings) with this scope model: **~0.07%** (vs. ~38% naïvely). The earlier "not viable"
worry was an artifact of not excluding imports/forwards/defs. A bounded **project-wide scan** of the
workspace's other `.m` files (`analysis/workspace.py` → their `defined_symbols`) folds sibling
definitions into the known set, dropping the residual to **~0.025%** (the rest are genuine package
quirks, e.g. a file importing from an absolute developer path). Note Magma's own binding pass
(`magma.validate.syntax_check`) is the *authoritative* undefined-name check when Magma is available
(it reports `Identifier ... has not been declared or assigned`); the static check is the fast,
every-keystroke, offline complement.

### 4b. Package `.m` files — doc strings, optional params, house style

`/opt/magma/package`, **3456 `.m` files**, of which **1776 contain `intrinsic` declarations** totalling
**16,014 declarations**. See §6 for the full grammar. These are the **only** source for optional-parameter
defaults and the canonical `{...}` doc strings, and a large idiomatic-Magma corpus.

⚠️ **Only extract from files attached by the default spec.** A default Magma session loads just the
**~3004 `.m` files reachable from `/opt/magma/package/spec`** — not all 3456. Files outside the spec
(CompTree, `Classical/Bilinear.m`, `test/` files, …) declare `intrinsic`s that Magma **does not
register**, so scanning every `.m` over-includes ~84 non-existent "intrinsics" (verified: lifts
Magma-confirmation of DB names from 99.15% → 99.98%). Parse the spec tree to get the attached set
(`db/spec.py`): grammar is `NAME { … }` (subdir), bare `{ … }` (an included spec's body),
`+other.spec` / `+subdir/other.spec` (include — entries resolve relative to the **included spec
file's own directory**), and `file.m`. Also **skip commented-out declarations** — `intrinsic // …`
parses to an `intrinsic_definition` whose name child is a `comment`; require the name to be an
`identifier`.

### 4c. `.sig` files — compiled signature index (text, alongside each `.m`)

~3004 text `.sig` files sit beside the `.m` files (e.g. `…/CrvEll/6and12descent.sig`). They are line records:
- Line 1 = format stamp `178,<flag>`.
- **`S` record** = one intrinsic signature/overload: `S,<Name>,<docstring>,<flags>,<arity>,…,<numeric type
  codes>,…` — doc strings are **plaintext** (double-quoted if they contain commas; empty field = no doc).
- **`A`** = attribute decl (`declare attributes Cat: attr;`), **`V`** = verbose-flag decl, **`T`** = user type
  decl (`declare type …;`). (No `C` record type in this version.)
- The numeric type codes index an **internal kernel table that is NOT shipped human-readable and is NOT stable
  across versions** — they must be re-derived per install by aligning `.sig` ↔ `.m`. **Therefore prefer
  `ListSignatures` (prints category NAMES) over decoding `.sig` codes.** The `.sig` files are most useful as a
  fast plaintext source of doc strings keyed by name+arity.

---

## 5. Diagnostics / validation backend (design from verified behavior)

Hybrid fast→slow, per design.md §6. Magma's error block format (the parse target):

```
<blank line>
In file "<path>", line <N>, column <M>:
>> <source echo>
        ^
<SEVERITY>: <message>
```

- **Severities**: `User error: …` (syntax errors; `Identifier '…' has not been declared or assigned`; user
  `error`), `Runtime error: …` / `Runtime error in '<NAME>': …` / `Runtime error in evaluation: …`,
  `System Error: …` (e.g. memory limit). Map all → LSP severity **Error**. `WARNING …` lines are **free text
  with no position** — treat as positionless info.
- **`Bad argument types`** errors emit a follow-on line `Argument types given: <Cat>` (no `In file` header) —
  append it to the preceding block's message.
- **Column semantics**: `column M` counts from 1 with **tabs expanded to 8-col stops**. Normalize tabs (or
  strip them from source before sending) when mapping `M` back to a character offset. Trust the header column;
  ignore the caret line for parsing.
- **`-e`/`-E` one-shot** reports location as `In eval expression, line N, column M:` (no real path) — so
  **feed a temp `.m` FILE** instead, to get real file paths in diagnostics.

**Error-extraction regex** (Python `re.MULTILINE`, validated against all observed variants; `[PC]` prefix
appears only under `-c`):
```
(?m)^(?:\[PC\]\s)?In file "(?P<file>[^"]+)", line (?P<line>\d+), column (?P<col>\d+):\s*\n
(?:\[PC\]\s)?>> (?P<src>.*)\n(?:\[PC\]\s)?\s*\^[ \t]*\n
(?:\[PC\]\s)?(?P<sev>User error|Runtime error(?: in [^:]+)?|System [Ee]rror)(?::\s?)(?P<msg>.*)
```
Plus a looser positionless pass for `In eval expression` / system errors. Use `finditer` to collect all blocks.

### Fatal vs. non-fatal (drives how many diagnostics you get per run) — verified

- **Syntax error** → aborts parsing of the whole file → **exactly one** diagnostic per run.
- **Genuine runtime/evaluation error** (e.g. `1/0` → "Illegal zero denominator", `Factorial(-3)`,
  `Bad argument types`) → in **batch (`-b`)** mode **aborts the rest of the file** (verified: code after it does
  not run). Continuation-after-error happens only in *interactive* mode.
- **`Identifier '…' has not been declared or assigned`** (raised at function-definition/binding time) is the
  **only non-fatal** class: execution continues past it. ⚠️ **Correction (verified on 2.29-9):** the
  collection is per *top-level statement* — **within one function body, binding aborts at the FIRST
  undefined identifier**, so the single-function wrap yields at most one undefined-name diagnostic per run.
  (The static unknown-intrinsic check reports them all at once; that is its main edge over the Magma pass.)
  It stays exit 0 **even with `SetQuitOnError(true)`** → **never trust exit code alone; always parse stdout
  text.**

### Syntax-only check — strategy dispatch per file shape (see `magma/validate.py`) — verified

The never-called-function wrap parses (and binds) user code without executing it:
```magma
SetColumns(0); SetEchoInput(false); SetIgnorePrompt(true);
__chk := function()
<USER CODE>
return 0; end function;
```
- **Pure syntax error** in the body → **caught** at parse time, exact line/col.
- **Undefined name** in the body → **caught** at binding time (only the FIRST per body — see above).
- **Bad-argument-type / value errors** → **NOT caught** (only fire on real execution). For those, run an
  **optional execution pass**: drop the wrapper, prepend `SetMemoryLimit(<bytes>); SetQuitOnError(true);`, run
  under `timeout`, collect the first runtime error.

⚠️ **The wrap is only safe for code that tree-sitter-parses cleanly and contains no `intrinsic`/`load`**
(verified failure modes: an `intrinsic` declaration inside a function is a phantom `bad syntax` at 1:1; a
stray top-level `end function;` closes the wrapper early → phantom errors past EOF **and the remainder
executes**). Therefore `syntax_check` dispatches:
- tree-sitter ERROR nodes → report tree-sitter positions, never invoke Magma;
- contains `intrinsic` declarations → write to a temp `.m` and **`Attach(...)`** it (parse errors come back
  with real positions in that file; nothing user-level executes; binding is lazy, so undefined names in
  bodies are left to the static check, which models `import`/`forward`);
- otherwise → the wrap, with `load "…"` directives blanked out first (illegal inside a function) and
  binding errors suppressed for such files (resolved `load` targets are handled by the callers instead).
Positioned diagnostics are filtered to *our* temp file (printed output cannot spoof them), clamped to the
document, and tab-expanded columns are mapped back to character offsets.

`-c <spec>` package-compile catches syntax and exits nonzero, but only for genuine `intrinsic…end intrinsic`
package files, prefixes output `[PC] `, and gives poor/locationless in-body messages — `Attach` gives the
same coverage with better positions, so the code uses `Attach`.

### Exit codes (verified, with `SetQuitOnError(true)`)
clean → 0; syntax error → 1; thrown runtime error → 1; memory-limit System Error → 1; **but** non-fatal
`Identifier not declared` → still 0. Without `SetQuitOnError`, errors generally leave exit 0.

---

## 6. Intrinsic declaration grammar (for the parser)

Shape (header may span multiple lines; `->` clause may be on its own line; doc immediately follows the header):
```
intrinsic Name(x::RngIntElt, S::SeqEnum[RngIntElt] : Al := "Default", Bound := 0) -> RngIntElt, BoolElt
{ The one-line description that ?Name prints. May span multiple lines. }
    ... body ...
end intrinsic;
```

Grammar elements (all confirmed against real files, with corpus counts):

| Element | Notes / form | Count |
|---|---|---|
| Total `intrinsic` declarations | across 1776 files | **16,014** |
| Single-line header with `-> ` | | 13,575 |
| **Procedure-style** (no `->`) | e.g. `intrinsic AssignNames(~C::AlgClff, S::SeqEnum[MonStgElt])` | 2,367 |
| **Multi-line headers** (split across lines) | continuation when line ends mid-args or before `->` | 334 |
| **Optional params** `: P := expr` | defaults may be **expressions/calls** (`C3 := Curve(model3)`, `[0 : i in …]`) | 2,414 |
| **Reference args** `~x::Type` | mutated in place | 165 |
| **Operator intrinsics** (quoted name) | `'+'`, `'#'`, `'!'`, `'in'`, `'*:='`, … | 1,297 |
| Parametrized types | `SeqEnum[Crv]`, `AlgMatElt[RngOrd]`, nested | ~3,500 |
| Multiple return types | `-> AlgMatElt, Map` | ~800 |
| `.` (Any/wildcard) arg type | `'+:='(~x::., y::.)` | ~200 |
| `Any`-typed arg | `'in'(x::Any, y::PathLS)` | ~75 |

**Doc strings** `{ … }`:
- Inline, next-line, or multi-line; **empty `{}`** is valid (~750).
- ⚠️ **Ditto form `{"}`** = "same doc string as the previous overload" — **680 occurrences in 179 files**
  (often written `{"} //"`, where the `//"` is a comment to rebalance the quote for editors). The parser MUST
  resolve `{"}` to the prior overload's doc. *(The recon's grammar pass missed this; verified directly.)*
- Doc text can contain `->`, braces, brackets — match the **balanced** `{…}`, don't parse internals.
- A `require …: "msg";` / `error if …` may follow the doc before the body.

**Parsing strategy**: this header grammar is for the **DB builder** that scans the `.m` corpus to extract
signatures + doc strings. Tokenize rather than rely on a single regex — headers are multi-line, args nest
brackets, defaults are arbitrary expressions, and doc strings contain delimiters. A reasonable pipeline: locate
`^intrinsic\b`, scan balanced `(...)` for the arg list, split the optional-params tail at the top-level `:`,
capture the `-> …` up to the `{`, then capture the balanced `{…}` doc (resolving `{"}`). Validate any parser
against the real corpus and keep a regression set (design.md §8).

For parsing **user source** in the live server (syntax diagnostics, document symbols, navigation, the §13
lints), do **not** hand-roll a parser — use **`tree-sitter-magma`** (§12).

---

## 7. Package tree map (`/opt/magma/package`)

20 top-level areas; each has an `<Area>.spec`. `.spec` files are declarative: nested `Dir { … }` blocks, bare
`file.m` entries (load order), and `+Nested.spec` includes; attached in Magma via `AttachSpec(path)`.

| Area | `.m` | Domain |
|---|---:|---|
| **Group** | 1678 | Finite/infinite groups: perm, matrix, FP, p-groups, abelian, cohomology, ATLAS/data libs |
| **Geometry** | 874 | Algebraic geometry: curves (ell/hyp/genus>1), schemes, surfaces, modular forms/symbols, L-series, Jacobians |
| **Ring** | 245 | Rings & fields: number/function/finite/cyclotomic/p-adic fields, poly rings, Galois groups |
| **Algebra** | 166 | Associative/Lie/quaternion/matrix algebras |
| **RepThry** | 97 | Representation theory: characters, Artin/Galois reps, p-adic Galois modules |
| **Module** | 92 | Modules over rings, (sparse) matrices, multilinear/tensor |
| **LieThry** | 53 | Lie algebras/groups, root systems, Weyl/Coxeter groups |
| **Code** | 47 | Error-correcting codes |
| **Lattice** | 47 | Lattices, binary quadratic forms, Hermitian lattices |
| **HomAlg** | 44 | Homological algebra: complexes, modules over multivariate poly rings |
| **Commut** | 43 | Commutative algebra: Gröbner, ideals, invariant theory, solving |
| **System** | 22 | Env, I/O, process, profiling, parallelization |
| **Aggregate** | 18 | Built-in containers (List, Set, SeqEnum, Assoc, Tup, MonStgElt, …) |
| **Incidence** | 15 | Designs, graphs, incidence geometry, tableaux |
| **Forms** | 13 | Quadratic/bilinear forms, Clifford algebras |
| Opt / Semigroup / Topology | ≤1 | stubs/placeholders |

**Where common research domains live** (for navigation):
elliptic curves `Geometry/CrvEll`; hyperelliptic `Geometry/CrvHyp`; genus-2 `Geometry/CrvG2`; general curves
`Geometry/Crv`; modular forms `Geometry/ModFrm`(+`ModSym`); L-functions `Geometry/LSeries`; number fields
`Ring/FldNum`/`FldAlg`; finite fields `Ring/FldFin`; p-adics `Ring/XPadic`,`RngLoc`; Galois groups
`Ring/GaloisGrp`; finite/perm/matrix groups `Group/{GrpFin,GrpPerm,GrpMat}`; quaternion algebras
`Algebra/AlgQuat`; lattices `Lattice/Lat`; Gröbner/commutative `Commut/RngMPol`. Top-tier for this project:
`CrvEll, CrvHyp, FldNum, ModFrm, GrpFin, AlgLie`.

---

## 8. Handbook documentation (`/opt/magma/doc`)

For richer hover docs / examples (secondary to §4 for signatures).

- **HTML** (`doc/html`, ~2057 `textNN.htm` content pages + 667 index files). Entry points `MAGMA.htm`,
  `doc.htm`. An intrinsic entry is an `<H5><A NAME="<anchorID>">Signature</A></H5>` followed by a
  `<BLOCKQUOTE>` of prose; multiple overloads = consecutive `<H5>` then shared prose. Optional params appear as
  `<PRE>ParamName: Type Default: value</PRE>` inside the blockquote.
- **Name → doc lookup**: build a hashmap from **`doc/html/ind-all`** (a 3.7 MB flat, pipe/`<->`-delimited index)
  keyed by intrinsic name → `(textNN.htm, anchorID)`; then read that page and jump to the anchor. Verified
  paths: `EllipticCurve` → `text1544.htm#17806`; `Factorization` → `text185.htm#1479`. Anchor IDs are numeric
  and stable across versions.
- **Examples**: `<H3><A NAME="…">Example <CanonicalName> (H<part>E<num>)</A></H3>` then `<PRE>` code+output. Many
  loadable in Magma via `load "<CanonicalName>";`.
- **PDF** (`doc/*.pdf`): `Handbook.pdf` (full) + `HandbookVolume01..13.pdf` + `Overview.pdf` + `ReleaseNotes.pdf`.
  Prefer HTML for programmatic hover (reliable anchors); PDF for archival/typeset math.

---

## 9. Claude Code plugin (LSP) integration

The plugin is the **thin part** — it points Claude Code at our server. (Sourced from live
`code.claude.com/docs` as of 2026-05-29; HIGH confidence except where noted.)

- **LSP support** shipped Dec 2025 (Claude Code ≥ v2.0.74). Claude Code uses these LSP operations:
  **`publishDiagnostics`** (server **auto-pushes** after every edit — Claude sees errors without asking and
  fixes in-turn), **`hover`**, **`completion`**, **`definition`**, **`references`**, **`documentSymbol`**,
  **`workspace/symbol`**. It does **not** use `formatting`, `codeAction`, `rename`, `typeDefinition`,
  `implementation`, `executeCommand`. → our server must prioritize `publishDiagnostics`, `hover`, `completion`,
  `definition`, `documentSymbol`; `signatureHelp` is useful but secondary.
- **`.lsp.json`** lives at the **plugin root** (NOT inside `.claude-plugin/`). Required fields: `command`,
  `extensionToLanguage`. Optional: `args`, `transport` (`stdio`|`socket`, default `stdio`), `env`,
  `initializationOptions`, `settings`, `startupTimeout`, `restartOnCrash`, `maxRestarts`, … Supports var
  substitution `${CLAUDE_PLUGIN_ROOT}`, `${CLAUDE_PROJECT_DIR}`, `${ENV_VAR}`. Magma example:
  ```json
  { "magma": { "command": "magma-lsp", "args": ["--stdio"],
      "extensionToLanguage": { ".magma": "magma", ".m": "magma" },
      "initializationOptions": { "magmaPath": "/opt/magma/magma" } } }
  ```
- **Manifest** `.claude-plugin/plugin.json` (only `plugin.json` goes in that dir; **all other component dirs —
  `skills/`, `agents/`, `hooks/`, `.lsp.json` — live at the plugin ROOT**). Required: `name` (kebab-case).
  Useful: `displayName`, `version` (omit → git SHA), `description`, `author`, `license`, `lspServers`
  (path like `"./.lsp.json"` or inline).
- **Distribution**: official (`claude-plugins-official`), community (`claude-community`), or custom marketplace
  (`.claude-plugin/marketplace.json` in a git repo / local dir). Install: `/plugin marketplace add <src>` then
  `/plugin install <name>@<marketplace>`; `/reload-plugins` to activate.
- **`.m` extension collision** (MATLAB / Objective-C): **NOT formally addressed in current docs (LOW
  confidence).** Recommended: support **`.magma` as primary + `.m` as fallback**, both mapped to languageId
  **`magma`**. Consider content sniffing for `.m` files. Verify actual behavior in a live Claude Code session.

---

## 10. Build plan & conventions (condensed from design.md §7–8)

- **Phase 0** — repo skeleton + trivial plugin (`.lsp.json` + manifest) + minimal server that attaches and emits
  one diagnostic, to prove the Claude Code ↔ server channel.
- **Phase 1** — signature DB (parse `.m` + merge `ListSignatures`; schema handles overloads + optional params) +
  a `pygls` server giving completion / signatureHelp / hover + syntax diagnostics.
- **Phase 2** — Magma-backed validation (§3, §5), sandboxed.
- **Phase 3** — hardening, profiling, handbook doc integration, packaging toward an official add-on; optional
  MCP front-end.

Conventions: **functional Python prototype first**; **C only for measured hot paths** (target modern x86,
128-bit arith + AVX-512; explicit `int32_t/int64_t/int128_t`; avoid `const` in type decls). Keep a **regression
corpus** of Magma snippets with expected diagnostics from day one. Build a shared **core** with thin LSP (and
later MCP) adapters — not parallel implementations.

---

## 11. Open items to confirm in a live session

- `.lsp.json` variable substitution parity with MCP (docs describe MCP); inline `lspServers` in `plugin.json`.
- Actual `.m`/`.magma` association + languageId `magma` behavior inside Claude Code.
- DB build time end-to-end and server query latency (informs any later C work).
- Decisions pending from Drew (asked): Python/`pygls` lock-in; whether to lean on `.sig` doc strings; repo
  tooling (uv/poetry, test framework, formatter); whether to proceed straight into Phase 0/1 or pause for review.
- `tree-sitter-magma` (§12) coverage gaps vs. real Magma (16 open issues upstream) — which constructs it
  mis-parses; whether to vendor/pin a commit and/or upstream fixes.

---

## 12. External tooling we build on (don't reinvent) — both MIT, actively maintained

- **`tree-sitter-magma`** — https://github.com/edgarcosta/tree-sitter-magma (MIT; **generated from Magma's own
  yacc grammar** → high-fidelity). Ships `grammar.js`, a compiled C parser, **Python bindings**
  (`bindings/python/tree_sitter_magma`, importable as `tree_sitter_magma`), and queries
  `queries/{highlights,locals,indents,folds}.scm`. **Recommended parser foundation for the live server**
  (supersedes the hand-rolled Python parser design.md originally sketched): error-recovering AST for syntax
  diagnostics, `documentSymbol` + navigation, semantic tokens (`highlights.scm`), and scope/binding resolution
  via `locals.scm` — the basis for the static lints in §13. ~187 commits, 16 open issues → expect coverage
  gaps; pin/vendor a known-good commit and keep a regression corpus.
- **`lava`** — https://github.com/havarddj/lava (MIT, Rust). Magma CLI on tree-sitter-magma + topiary:
  `lava format` (opinionated reformatter), `lava highlight` (ANSI/HTML), `lava test` (parallel runner),
  `lava parse` (planned). No LSP. **Use lava directly for formatting** rather than reimplementing it — Claude
  Code's LSP doesn't call `formatting` (§9), so formatting is a CLI/pre-commit nicety, not core. Its
  `crates/lava-core/tests/topiary-tests/{input,expected}/*.m` is a ready-made **parser regression corpus**
  (~30 constructs: comprehensions, where-clauses, intrinsics, optional args, quantifiers, …).

---

## 13. Static-analysis value-add (beyond what Magma reports)

Magma's interpreter reports errors only at parse/run time; it does **not** flag unused variables and similar.
A tree-sitter `locals.scm`-driven pass (scopes = function/procedure/intrinsic/for/while/repeat/where;
definitions = params, `local`, assignment targets, loop vars, `where` bindings; references = all identifiers)
gives pyflakes-style lints that help both the agent and the user — all **static** (no Magma process), so they
run on every edit and complement the dynamic Magma check (§5):

- **Unused variable / parameter** — bound but never referenced in scope → LSP Warning/Hint.
- **Undefined / use-before-assignment** — referenced with no binding *and* not a known intrinsic (cross-check
  the signature DB §4 / kernel names to avoid false positives).
- **Shadowing** of an outer binding or an intrinsic name.
- (Later) unreachable code after `return`/`error`; redundant re-assignment.

⚠️ Magma gotcha: **`_` is the discard placeholder** in multi-value assignment (`a, _ := Foo();`) — never warn
on `_`. Honor `~ref` params and `where`/quantifier bindings per Magma scoping when deciding "used".

---

## 14. Validation harnesses & measured performance (this VM)

Run-on-demand parallel validators live in `validation/` (use the 192 cores; need Magma + the package
tree). They are how DB/extractor/diagnostics correctness is checked at scale, and they found the §4b
spec-attachment and comment-skip bugs and the comprehension/for-loop binder scope bug.

- `scan_corpus.py` — parse+extract all 3456 `.m`: **0 extractor crashes**, 0.2% tree-sitter parse
  errors (all in giant data files / localized), ~12 s.
- `validate_db.py` — probe every DB name against Magma (`name;`) to confirm it is a real intrinsic
  (**99.98%**; the ~2 stragglers are guarded/non-loaded declarations).
- `diff_diagnostics.py` — static unknown-intrinsic check vs Magma's binding pass over all **1018
  handbook chapter programs**; after the binder fix, 7 residual "FPs" are all `Attach`/`load`-defined
  example helpers (test-corpus artifacts, not check bugs).
- `bench.py` — performance.

**Measured perf (informs design.md §8 — no C hot path is warranted; current box, 2.29-9):** DB load
~200 ms (10,304 names); hover **0.01 ms**, completion **~1 ms**, static checks on an ~80-line file
**0.2–0.5 ms**; Magma syntax check **~12.5 ms** median (cold-start bound), **~1,583 checks/sec**
across 32 workers. Full DB build (package extract + ListSignatures + variadic probe + kernel doc
harvest) **~30 s**. (GCP-VM-era numbers: 104 ms cold start, 548 checks/sec on 64 workers, ~40 s
build without the doc harvest.) Validation results on 2.29-9 after the extraction fixes:
`validate_db` 99.98% (2 known guarded declarations), `recall` 100% on 49,456 typo injections,
`diff_diagnostics` 7 residual corpus-artifact FPs, `arity_fp` 25 flags over 4,031 sources — each
inspected one is a *latent bug in the package corpus itself* (calls to overloads that don't exist),
i.e. ≈0 true false positives.
