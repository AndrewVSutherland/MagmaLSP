"""A compact, curated "Magma for people who know other CASes" brief, served to agents.

Everything below was verified against a live Magma (2.29-x) — most of it is exactly the
conventions our trap benchmark (eval/trap_tasks.py) showed LLMs get wrong when writing Magma
from their prior. Small models especially benefit from reading this once instead of
rediscovering each convention by trial; the content is deliberately terse and example-driven.
"""

from __future__ import annotations

GUIDE = """\
# Magma conventions & pitfalls (read once before writing Magma)

## Syntax essentials
- Assignment is `:=` ; equality is `eq` (and `ne`, `lt`, `le`, `gt`, `ge`). `=` alone is NOT
  assignment, `==`/`!=` do not exist. Booleans are lowercase `true`/`false`; no `None`.
- Every statement ends with `;`. Comments: `//` line, `/* ... */` block. `//` is a COMMENT,
  never division: integer division is `div`, remainder `mod`; `a/b` on integers makes a
  *rational number*.
- Exponentiation is `^` (not `**`). Cardinality/length is the `#` operator: `#S`, `#"abc"`.
- No method calls: `L.append(3)` is wrong; intrinsics are global: `Append(~L, 3)`.
  `X.i` means "the i-th generator of X" (e.g. `R.1` for a polynomial variable).
- Indexing is 1-based: `[10,20,30][1]` is `10`. Ranges: `[1..10]`, `[1..10 by 2]`.
- Multiple return values: `b, r := IsSquare(16);` (b=true, r=4). Use `_` to discard.
  Many predicates return a witness as the second value — take it instead of recomputing.
- Generator-naming syntax on constructors: `R<x> := PolynomialRing(Rationals());`,
  `K<a> := NumberField(x^2-2);` — then `x`/`a` denote the generators.

## Convention traps (these run cleanly and give WRONG answers if you guess)
- `Subgroups(G)` returns one representative per CONJUGACY CLASS of subgroups (as records:
  `s`subgroup`, `s`order`), not all subgroups (#Subgroups(Sym(4)) is 11, but S4 has 30
  subgroups). `AllSubgroups(G)` gives literally all.
- `ConjugacyClasses(G)` returns `<order, class_size, representative>` triples: `c[2]` is the
  class size, `c[3]` the element.
- `Factorization(n)` / `Roots(f)` return pairs: `[<prime, exponent>, ...]` /
  `[<root, multiplicity>, ...]` — take `t[1]` for the prime/root.
- `Divisors(n)` includes 1 and n itself. `EulerPhi`, `MoebiusMu`, `DivisorSigma` exist —
  don't reimplement.
- `Coefficients(f)` lists the CONSTANT term first: `Coefficients(x^2+2*x+3)` is `[3,2,1]`;
  `Coefficient(f, i)` takes the exponent i.
- `ModularForms(N, k)` includes Eisenstein series; use `CuspForms(N, k)` for cusp forms only.
- `Discriminant(f)` of a defining polynomial differs from `Discriminant(Integers(K))` (field/
  maximal-order discriminant) by a square factor — for number fields use the latter.
- Real precision: `RealField()` defaults to ~30 digits; huge exact values printed through it
  lose digits. Stay exact (integers/rationals) whenever possible; `RealField(100)` if not.
- Random algorithms: `SetSeed(n)` for reproducibility. Print with `printf "%o\\n", x;`
  (`%o` formats any Magma object).

## Sequences, sets, and efficiency
- `[...]` sequence (ordered, duplicates), `{...}` set, `{@ ... @}` indexed set,
  `<a, b>` tuple. Comprehensions: `[f(x) : x in S | P(x)]`.
- Grow a sequence with `Append(~L, x)` (in-place procedure; `L := L cat [x]` copies).
  The `~` marks in-place mutation; `Sort(~L)`, `Reverse(~L)` similarly.
- Reductions: `&+S` (sum), `&*S` (product), `&cat S` (concatenate), `&and`, `&or`.
  `&+[]` errors ("null sequence"); give the universe explicitly: `&+[Integers()| ]` is 0.
- `exists(t){x : x in S | P(x)}` finds a witness and binds it to `t`;
  `forall{...}` similarly. Both short-circuit — prefer them over manual loops.
- Prefer intrinsics over hand-written loops: they are C-level and usually algorithmically
  smarter (e.g. `IsPrime`, `Factorization`, `PrimesUpTo(n)`, `DivisorSigma(1, n)`).
- Time things with `time statement;` or `t := Cputime(); ...; Cputime(t)`. Turn on progress
  info for long algorithms with `SetVerbose("ClassGroup", 1)` (etc.).

## Workflow that works (tools of this plugin)
1. Don't guess intrinsic names — `magma_search` by concept, then `magma_lookup` the
   candidates: the doc states the exact return convention.
2. `magma_check` every draft (it executes by default and catches bad-argument-type errors
   that static checks cannot).
3. A clean `magma_run` is NOT correctness: cross-check a known case (e.g. verify your
   formula on a small group whose answer you know) before trusting the output.
"""

# Appended to the guide so the agent knows up front whether file writes will fail — a
# confused agent burning iterations on failed writes is a real failure mode.
_SANDBOX_NOTES = {
    "active": (
        "ACTIVE — code passed to magma_run / magma_check(execute) runs with the filesystem\n"
        "READ-ONLY (bubblewrap): file writes fail, even in the program's own directory, and\n"
        "/tmp is a throwaway tmpfs. PRINT results instead of writing files. Reading files and\n"
        "relative `load`s work for sources at a normal path; a source located directly under\n"
        "/tmp cannot resolve a relative sibling load (use an absolute path). Shell-out and\n"
        "network are NOT blocked (Magma\n"
        "licensing constraint; well-known container-daemon sockets are masked best-effort).\n"
        "The server admin can grant writable directories via MAGMA_LSP_SANDBOX_WRITABLE or\n"
        "disable the sandbox via MAGMA_LSP_NO_SANDBOX=1."
    ),
    "disabled": (
        "DISABLED (MAGMA_LSP_NO_SANDBOX is set) — executed code runs with the caller's full\n"
        "filesystem access; file writes succeed."
    ),
    "unavailable": (
        "UNAVAILABLE (no `bwrap` on PATH) — executed code runs with the caller's full\n"
        "filesystem access; install bubblewrap to enable the read-only sandbox."
    ),
    "broken": (
        "BROKEN (bwrap is installed but cannot create a sandbox here — user namespaces\n"
        "disabled?) — executed code runs with the caller's full filesystem access."
    ),
}


def guide() -> str:
    from .magma.runner import sandbox_state

    state = sandbox_state()
    return GUIDE + f"\n## Execution sandbox (this session)\n{_SANDBOX_NOTES[state]}\n"
