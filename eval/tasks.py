"""Benchmark of Magma tasks for the LLM eval.

Each task asks the model to write a complete Magma program that PRINTS one specific answer. The
``reference`` is a known-correct program; running it yields the expected output (build_truth.py).
Scoring compares the model program's stdout to that expected output.

Tasks span domains where (a) Magma is the natural tool and (b) exact intrinsic names matter — the
place a signature DB + error signal should most help an LLM.
"""

from __future__ import annotations

TASKS: list[dict] = [
    {
        "id": "ec_conductor",
        "domain": "elliptic curves",
        "prompt": "Print the conductor of the elliptic curve y^2 = x^3 - x over the rationals.",
        "reference": "E := EllipticCurve([-1, 0]); print Conductor(E);",
    },
    {
        "id": "ec_rank",
        "domain": "elliptic curves",
        "prompt": "Print (only) the Mordell-Weil rank of the elliptic curve y^2 = x^3 - 2 over Q.",
        "reference": "E := EllipticCurve([0, -2]); r := Rank(E); print r;",
    },
    {
        "id": "ec_points_ff",
        "domain": "elliptic curves",
        "prompt": "Print the number of points on the elliptic curve y^2 = x^3 + x + 1 "
        "over the finite field GF(101).",
        "reference": "E := EllipticCurve([GF(101) | 1, 1]); print #E;",
    },
    {
        "id": "nf_class_number",
        "domain": "number fields",
        "prompt": "Print the class number of the imaginary quadratic field Q(sqrt(-23)).",
        "reference": "K := QuadraticField(-23); print ClassNumber(K);",
    },
    {
        "id": "nf_discriminant",
        "domain": "number fields",
        "prompt": "Print the discriminant of the maximal order of the number field "
        "defined by x^3 - x - 1.",
        "reference": "P<x> := PolynomialRing(Rationals()); K := NumberField(x^3 - x - 1); "
        "print Discriminant(MaximalOrder(K));",
    },
    {
        "id": "minpoly_degree",
        "domain": "number fields",
        "prompt": "Print the degree of the minimal polynomial over Q of sqrt(2) + sqrt(3).",
        "reference": "K := QuadraticField(2); L := ext<K | Polynomial([K| -3, 0, 1])>; "
        "print Degree(MinimalPolynomial(L.1 + K.1, Rationals()));",
    },
    {
        "id": "ff_factor",
        "domain": "finite fields",
        "prompt": "Print the number of distinct irreducible factors of the polynomial x^4 + 1 "
        "over GF(3).",
        "reference": "P<x> := PolynomialRing(GF(3)); print #Factorization(x^4 + 1);",
    },
    {
        "id": "grp_gl_order",
        "domain": "groups",
        "prompt": "Print the order of the general linear group GL(2, GF(3)).",
        "reference": "print #GL(2, GF(3));",
    },
    {
        "id": "grp_sym_classes",
        "domain": "groups",
        "prompt": "Print the number of conjugacy classes of the symmetric group S_8.",
        "reference": "print #ConjugacyClasses(SymmetricGroup(8));",
    },
    {
        "id": "matrix_det",
        "domain": "linear algebra",
        "prompt": "Print the determinant of the integer matrix [[2,1,0],[1,2,1],[0,1,2]].",
        "reference": "M := Matrix(Integers(), 3, 3, [2,1,0, 1,2,1, 0,1,2]); print Determinant(M);",
    },
    {
        "id": "modform_dim",
        "domain": "modular forms",
        "prompt": "Print the dimension of the space of weight-12 cusp forms for SL_2(Z) "
        "(level 1).",
        "reference": "print Dimension(CuspForms(1, 12));",
    },
    {
        "id": "partitions",
        "domain": "combinatorics",
        "prompt": "Print the number of partitions of the integer 100.",
        "reference": "print NumberOfPartitions(100);",
    },
    {
        "id": "nth_prime",
        "domain": "number theory",
        "prompt": "Print the 1000th prime number.",
        "reference": "print NthPrime(1000);",
    },
    {
        "id": "lattice_min",
        "domain": "lattices",
        "prompt": "Print (only) the minimum (squared length of a shortest nonzero vector) of "
        "the root lattice E8.",
        "reference": "L := Lattice(\"E\", 8); m := Minimum(L); print m;",
    },
    {
        "id": "cyclotomic_degree",
        "domain": "number theory",
        "prompt": "Print the degree of the 100th cyclotomic polynomial (i.e. Euler phi of 100).",
        "reference": "print Degree(CyclotomicPolynomial(100));",
    },
]
