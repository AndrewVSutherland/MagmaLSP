# DRAFT — bug report to the Magma group (for Drew's review; NOT sent)

Prepared 2026-08-13 from the MagmaLSP arity audit (`uv run python validation/arity_fp.py`,
baseline 26 flags / 4031 sources). Every item below was re-verified today on this box against
live Magma V2.29-9: the flagged name was probed with `Name;` and in each case **no overload
accepts the call's positional argument count**, so the statement raises
`Runtime error: Bad argument types` whenever control reaches it. Each flagged file was also
checked for local/imported/forward definitions of the name (none exist — nothing shadows the
intrinsic). Two demonstrative repros: `ModularForms();` and `SymplecticForm(16, Rationals());`
both fail with exactly that error. "Likely intended" notes are guesses, marked as such.

---

Subject: 24 latent bad-arity intrinsic calls in the V2.29-9 package tree (+2 handbook examples)

Dear Magma group,

While building a language-server / static-analysis tool for Magma (MagmaLSP — a
Simons-collaboration side project that will be public shortly), we ran its arity checker over
the V2.29-9 package tree (the ~3000 `.m` files attached by the default spec) and the handbook's
worked examples. The checker flags a call when its positional argument count matches no
overload of any registered intrinsic; names defined locally, imported, or forward-declared in
the file are excluded, and every remaining flag below was hand-verified against the live
signature listing (`Name;`) in V2.29-9.

The result: 24 call sites in the shipped packages (in 13 files) and 2 handbook example lines
invoke intrinsics with an argument count that no overload accepts. Each such statement raises
`Runtime error: Bad argument types` whenever it is reached, so these are latent bugs on
less-traveled branches (several look like calls against older internal APIs that have since
changed shape). Locations are file:line in the V2.29-9 `package/` tree.

## Package files

1. `Algebra/AlgStar/form.m:553` and `:555` — `ConformalOrthogonalGroup(-1, d, k)` /
   `ConformalOrthogonalGroup(1, d, k)`: no 3-argument overload (accepted: `(d, q)`, `(d, K)`,
   `(V)`). These are the `"orthogonalminus"` / else branches of a name dispatch; likely
   intended `ConformalOrthogonalGroupMinus(d, k)` / `ConformalOrthogonalGroupPlus(d, k)`,
   which exist.

2. `Geometry/CrvEll/CrvEll_FldFun/countpoints.m:496` —
   `Mknown := FrobeniusActionOnPoints(s, q, gram);`: the only signature is
   `(s::SeqEnum[PtEll], q::RngIntElt : gram := 0)` — `gram` is an optional parameter passed
   here positionally (likely intended `: gram := gram`). The line even carries the comment
   `// syntax looks wrong?`.

3. `Geometry/GrpPSL2/GrpPSL2/comparison.m:66` — `K := NumberField(R1, R2);`: no 2-argument
   overload of `NumberField`. Reached when comparing points whose base fields are two distinct
   `FldNum`s; presumably a compositum was intended (`Compositum(R1, R2)` exists).

4. `Geometry/HypGeomMot/lseries.m:522` — `GAMMA := GammaFactors(HS`w, HS`A);`: `GammaFactors`
   takes a single `HodgeStruc` (or `LSer`); the two-argument `(w, A)` form looks like a
   pre-`HodgeStruc` API. Likely intended `GammaFactors(HS)`.

5. `Geometry/ModFrm/arithmetic.m:107` — `M := ModularForms();`: no 0-argument overload
   (repro: `ModularForms();` → `Runtime error in 'ModularForms': Bad argument types`).

6. `Geometry/RieSrf/paths.m:319` — `ResCh := Chain(Point(Ch`StartPt));`: no 1-argument
   overload of `Point` (accepted arities 2, 3).

7. `Geometry/Sch/norm_form_of_sing.m:573` — `MonomialCoefficient(t)` inside a `where`
   comprehension: `MonomialCoefficient` needs `(f, monomial)`; for a term `t` the intended
   value was presumably `LeadingCoefficient(t)`.

8. `Group/GrpFP/SolQ/sq_proc.m` — ten sites calling soluble-quotient helpers with argument
   counts no current overload accepts (all look like an older internal API):
   - `:2896` — `MSQLetternonsplit(G, R, tr, ws, Q, epi : Setup := Setup)`: 6 positional args;
     accepted 2, 3, 4.
   - `:2996` — `MSQLettersplit(G, R, ws, Q, epi : Setup := Setup)`: 5 args; accepted 2, 3, 4.
   - `:3178, :3181, :3188, :3199, :3206, :3211` —
     `AbsolutelyIrreducibleModules(G, GF(p), <list> : Process := true, GaloisAction := ...)`:
     3 positional args; accepted 1 (`(G : ...)`) and 2 (`(G, p)`, `(G, K)` variants). No
     current overload takes a third positional argument or the `Process`/`GaloisAction`
     parameters used here.
   - `:3737, :3751` — `IrreducibleModules(Group(...), GF(p), <list>)`: 3 args; the only
     signature is `(G, K : ...)`.

9. `LieThry/GrpLie/Lang.m:94` — `WriteOverSmallerField(c, K, k)`: no 3-argument overload
   (accepted: `(G::GrpMat, F::FldFin)`, `(M::ModRng, F::FldFin)`); presumably
   `WriteOverSmallerField(c, k)`.

10. `RepThry/ModGrp/pimintrinsics.m:85` — inside
    `intrinsic CohomologicalDimension(M::ModGrp, k::RngIntElt)`: the `k eq 1 and
    ISA(Type(G), GrpFP)` branch does `return H1Dimension(M);`, but `H1Dimension` only exists
    with 3 arguments (`(G::GrpFP, phi::Map, R::Rng)` / `(F, phi, HM::ModGrp)`) — so
    `CohomologicalDimension(M, 1)` errors for any FP-group module. The commented-out line
    directly below (`CohomologicalDimension(CohomologyModule(G, M), 1)`) looks like the
    working replacement.

11. `Ring/FldFun/FldAb/FldAbFun.m:330` — `val, z_Pm := ArtinSchreierReduction(F, uum[m], P);`:
    the only signature is `(u::FldFunGElt, P::PlcFunElt : MinimumPlace)` — the leading `F`
    argument is one too many (the commented-out line below uses the 2-argument shape).

12. `Ring/FldFun/FldAb/HCF.m:387` and `:389` —
    `MyExpand(x, I, PE : RelPrec := 1)` and `assert ... Expand(x, I, PE : RelPrec := 1)`:
    both pass `PE` positionally. `MyExpand` is `(g, P : RelPrec, Store, PE := false)` — the
    intended call is presumably `MyExpand(x, I : RelPrec := 1, PE := PE)`; `Expand` has no
    3-argument overload at all. Both statements error whenever the
    `Degree(I) eq 1 and PE cmpne false` path is taken.

13. `Ring/RngDiff/DiffOpForRngDiff.m:108` — `R := DifferentialOperatorRing(K, dz);` inside a
    local helper: the only overload — declared later in the same file (line 116) — is the
    1-argument `(F::RngDiff)`.

## Handbook worked examples

14. `text825.htm` (Example H73E21, symplectic group database) —
    `J := SymplecticForm(16, Rationals());`: the only intrinsic `SymplecticForm` takes a
    `GrpMat` (and returns a BoolElt first). Likely intended
    `StandardAlternatingForm(16, Rationals())`. As printed the example errors
    (repro: `Runtime error in 'SymplecticForm': Bad argument types`).

15. `text942.htm` (Example H82E10, braid groups) — `time _, c := MyIsConjugate(x, y1);`:
    `MyIsConjugate` exists only as `(G::GrpPerm, H1::GrpPerm, H2::GrpPerm)` (a
    permutation-group helper); for braid elements this line was surely meant to be
    `IsConjugate(x, y1)` — which the same example uses a few lines later.

Happy to provide the checker output, the verification scripts, or re-runs against a newer
release. None of this affects correct results on the mainline paths — every site is on a
branch that errors rather than silently misbehaving — but the fixes all look small.

Best,
[Drew]

---
*(End of draft. Verification artifacts from the 2026-08-13 session: full flag dump with
source context and DB signatures, live `Name;` probe of all 19 names with per-arity verdicts,
local-shadowing greps, and the two runtime repros.)*
