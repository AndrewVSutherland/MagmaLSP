"""Benchmark of Magma "ran != correct" traps.

Unlike hard_tasks.py (where the failure mode is a *crash* — invented intrinsics, bad argument
types — that Magma flags and a run-loop can iterate against), every task here has a plausible naive
program that **runs cleanly and prints a wrong answer**. The trap is a Magma-specific semantic
convention or a precision pitfall, not a syntax/name error:

- ``Subgroups(G)`` returns conjugacy-class representatives, not all subgroups.
- ``Roots`` / ``Factorization`` / ``ConjugacyClasses`` return tuples — (root,mult), (p,e),
  (order,size,rep) — not bare values.
- ``Divisors`` includes n itself; ``ModularForms`` includes Eisenstein series (vs ``CuspForms``).
- ``Discriminant`` of a polynomial != discriminant of the field's maximal order.
- ``a/b`` is exact rational division, not integer ``div``.
- ``Coefficients(f)[1]`` is the constant term, not the leading coefficient.
- ``Roots(f)`` over Q finds rational roots, not real roots.
- default ``RealField`` precision silently truncates a large ``Floor``.
- ``IsSquare`` returns a (bool, root) pair, not 0/1.

This is the regime where raw iterate-on-error cannot help (the program does not error), and where
the signature DB's doc strings / handbook prose should — if anything does — pull the LSP arm ahead.

Each task asks for a single verifiable scalar; build_truth.py runs the reference to capture it.
"""

from __future__ import annotations

TASKS: list[dict] = [
    {
        "id": "subgroups_s4",
        "domain": "groups",
        "prompt": "Print the total number of subgroups of the symmetric group S_4 — count every "
        "subgroup individually, not one representative per conjugacy class.",
        "reference": "G := Sym(4); print &+[s`length : s in Subgroups(G)];",
    },
    {
        "id": "omega_1e6",
        "domain": "integers",
        "prompt": "Print the number of prime factors of 1000000 counted with multiplicity (i.e. "
        "the total number of primes appearing in its prime factorization, the function Omega).",
        "reference": "print &+[f[2] : f in Factorization(1000000)];",
    },
    {
        "id": "roots_mult",
        "domain": "polynomials",
        "prompt": "Print the number of roots of (x-1)^3*(x-2)^2 in the rationals, counted with "
        "multiplicity.",
        "reference": "P<x> := PolynomialRing(Rationals()); f := (x-1)^3*(x-2)^2; "
        "print &+[r[2] : r in Roots(f)];",
    },
    {
        "id": "proper_div_sum",
        "domain": "integers",
        "prompt": "Print the sum of the proper divisors of 220 (the positive divisors of 220, "
        "excluding 220 itself).",
        "reference": "print DivisorSigma(1, 220) - 220;",
    },
    {
        "id": "int_quotient",
        "domain": "integers",
        "prompt": "Print the integer quotient (the floor) when 1000000 is divided by 7.",
        "reference": "print 1000000 div 7;",
    },
    {
        "id": "leading_coeff",
        "domain": "polynomials",
        "prompt": "Print the leading coefficient of the polynomial 3*x^5 + 2*x + 7.",
        "reference": "P<x> := PolynomialRing(Rationals()); "
        "print LeadingCoefficient(3*x^5 + 2*x + 7);",
    },
    {
        "id": "distinct_partitions",
        "domain": "combinatorics",
        "prompt": "Print the number of partitions of 20 into distinct parts (partitions in which "
        "no part is repeated).",
        "reference": "print #[p : p in Partitions(20) | #p eq #Set(p)];",
    },
    {
        "id": "cusp_dim",
        "domain": "modular forms",
        "prompt": "Print the dimension of the space of weight-12 cusp forms of level 1 (for "
        "SL_2(Z)).",
        "reference": "print Dimension(CuspForms(1, 12));",
    },
    {
        "id": "minpoly_deg",
        "domain": "linear algebra",
        "prompt": "Print the degree of the minimal polynomial of the 2x2 scalar matrix 2*I (the "
        "diagonal matrix whose two diagonal entries both equal 2).",
        "reference": "M := ScalarMatrix(Rationals(), 2, 2); print Degree(MinimalPolynomial(M));",
    },
    {
        "id": "field_disc",
        "domain": "number fields",
        "prompt": "Print the discriminant of the number field Q[x]/(x^3 - x^2 - 2*x - 8) — the "
        "discriminant of the field itself (equivalently of its ring of integers / maximal order).",
        "reference": "P<x> := PolynomialRing(Rationals()); K := NumberField(x^3 - x^2 - 2*x - 8); "
        "print Discriminant(MaximalOrder(K));",
    },
    {
        "id": "largest_class",
        "domain": "groups",
        "prompt": "Print the size of the largest conjugacy class of the symmetric group S_6.",
        "reference": "C := ConjugacyClasses(Sym(6)); m := Max([c[2] : c in C]); print m;",
    },
    {
        "id": "real_roots",
        "domain": "polynomials",
        "prompt": "Print the number of real roots of the polynomial x^5 - x - 1.",
        "reference": "P<x> := PolynomialRing(Rationals()); "
        "print #Roots(x^5 - x - 1, RealField(40));",
    },
    {
        "id": "exp_floor",
        "domain": "real analysis",
        "prompt": "Print the floor of exp(pi * sqrt(2023)) as an exact integer, with every digit "
        "correct.",
        "reference": "R := RealField(120); print Floor(Exp(Pi(R) * Sqrt(R ! 2023)));",
    },
    {
        "id": "qr_mod7",
        "domain": "finite fields",
        "prompt": "Print 1 if 2 is a square modulo 7, and 0 otherwise.",
        "reference": "print (IsSquare(GF(7) ! 2) select 1 else 0);",
    },
]
