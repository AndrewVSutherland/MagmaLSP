"""Harder Magma benchmark, inspired by *Solving Problems with Magma* (Bosma, Cannon, Playoust,
Steel). Multi-step problems using less-common intrinsics — where an LLM is more likely to invent
or mis-call intrinsics, so the LSP's lookup/check signal should matter more.

Each task asks for a single verifiable scalar; build_truth.py runs the reference to capture it.
"""

from __future__ import annotations

TASKS: list[dict] = [
    {
        "id": "amicable_count",
        "domain": "integers",
        "prompt": "Print how many integers n with 2 <= n <= 2000 are amicable (the sum of the "
        "proper divisors of n equals some m != n whose proper-divisor sum is n).",
        "reference": "s := func<n | DivisorSigma(1, n) - n>; c := 0; "
        "for n in [2..2000] do m := s(n); if m ne n and m ge 2 and s(m) eq n then c +:= 1; "
        "end if; end for; print c;",
    },
    {
        "id": "resultant",
        "domain": "polynomials",
        "prompt": "Print the resultant of the polynomials x^4 - 1 and x^3 + 2*x + 1 over Q.",
        "reference": "P<x> := PolynomialRing(Rationals()); "
        "print Resultant(x^4 - 1, x^3 + 2*x + 1);",
    },
    {
        "id": "factors_x15",
        "domain": "polynomials",
        "prompt": "Print the number of irreducible factors of x^15 - 1 over the rationals.",
        "reference": "P<x> := PolynomialRing(Rationals()); print #Factorization(x^15 - 1);",
    },
    {
        "id": "mult_order",
        "domain": "integers",
        "prompt": "Print the multiplicative order of 2 modulo the prime 1000000007.",
        "reference": "print Order(Integers(1000000007) ! 2);",
    },
    {
        "id": "bernoulli_digits",
        "domain": "integers",
        "prompt": "Print the number of decimal digits of the numerator of the Bernoulli number "
        "B_10000 (use the absolute value of the numerator).",
        "reference": "n := Numerator(BernoulliNumber(10000)); print #Sprint(Abs(n));",
    },
    {
        "id": "splitting_degree",
        "domain": "number fields",
        "prompt": "Print the degree over Q of the splitting field of x^3 - 2.",
        "reference": "P<x> := PolynomialRing(Rationals()); K := SplittingField(x^3 - 2); "
        "print Degree(K);",
    },
    {
        "id": "galois_order",
        "domain": "number fields",
        "prompt": "Print the order of the Galois group of the polynomial x^5 - 2 over Q.",
        "reference": "P<x> := PolynomialRing(Rationals()); G := GaloisGroup(x^5 - 2); print #G;",
    },
    {
        "id": "torsion_units",
        "domain": "number fields",
        "prompt": "Print the number of roots of unity in the number field Q[x]/(x^4 + x^3 + x^2 "
        "+ x + 1).",
        "reference": "P<x> := PolynomialRing(Rationals()); K := NumberField(x^4+x^3+x^2+x+1); "
        "T := TorsionUnitGroup(K); print #T;",
    },
    {
        "id": "s4_normal",
        "domain": "groups",
        "prompt": "Print the number of normal subgroups of the symmetric group S_4 (including the "
        "trivial subgroup and the whole group).",
        "reference": "print #NormalSubgroups(SymmetricGroup(4));",
    },
    {
        "id": "sylow2_s8",
        "domain": "groups",
        "prompt": "Print the order of a Sylow 2-subgroup of the symmetric group S_8.",
        "reference": "print #SylowSubgroup(SymmetricGroup(8), 2);",
    },
    {
        "id": "aut_d12",
        "domain": "groups",
        "prompt": "Print the order of the automorphism group of the dihedral group of order 12.",
        "reference": "G := DihedralGroup(6); print #AutomorphismGroup(G);",
    },
    {
        "id": "matrix_order_ff",
        "domain": "groups",
        "prompt": "Print the multiplicative order of the matrix [[1,1],[0,1]] in GL(2, GF(5)).",
        "reference": "G := GL(2, GF(5)); g := G ! [1,1,0,1]; print Order(g);",
    },
    {
        "id": "smith_largest",
        "domain": "linear algebra",
        "prompt": "Print the largest elementary divisor (last diagonal entry of the Smith normal "
        "form) of the integer matrix [[2,4,4],[-6,6,12],[10,-4,-16]].",
        "reference": "M := Matrix(Integers(), 3, 3, [2,4,4, -6,6,12, 10,-4,-16]); "
        "S := SmithForm(M); print S[3][3];",
    },
    {
        "id": "petersen_chromatic",
        "domain": "graphs",
        "prompt": "Print the chromatic number of the Petersen graph.",
        "reference": "Vl := Setseq(Subsets({1..5}, 2)); "
        "E := {{i,j} : i,j in [1..#Vl] | i lt j and IsDisjoint(Vl[i], Vl[j])}; "
        "print ChromaticNumber(Graph<#Vl | E>);",
    },
    {
        "id": "petersen_auto",
        "domain": "graphs",
        "prompt": "Print the order of the automorphism group of the Petersen graph.",
        "reference": "Vl := Setseq(Subsets({1..5}, 2)); "
        "E := {{i,j} : i,j in [1..#Vl] | i lt j and IsDisjoint(Vl[i], Vl[j])}; "
        "print #AutomorphismGroup(Graph<#Vl | E>);",
    },
    {
        "id": "e8_aut_order",
        "domain": "lattices",
        "prompt": "Print the order of the automorphism group of the E8 root lattice.",
        "reference": "L := Lattice(\"E\", 8); print #AutomorphismGroup(L);",
    },
    {
        "id": "e8_kissing",
        "domain": "lattices",
        "prompt": "Print the kissing number (number of minimal vectors) of the E8 root lattice.",
        "reference": "L := Lattice(\"E\", 8); print KissingNumber(L);",
    },
    {
        "id": "hamming_mindist",
        "domain": "codes",
        "prompt": "Print the minimum distance of the binary [7,4] Hamming code.",
        "reference": "print MinimumDistance(HammingCode(GF(2), 3));",
    },
    {
        "id": "golay_mindist",
        "domain": "codes",
        "prompt": "Print the minimum distance of the binary [23,12,7] Golay code.",
        "reference": "print MinimumDistance(GolayCode(GF(2), false));",
    },
    {
        "id": "variety_size",
        "domain": "commutative algebra",
        "prompt": "Print the number of solutions over the algebraic closure of Q of the system "
        "x + y + z = 0, x*y + y*z + z*x = 0, x*y*z = 1.",
        "reference": "R<x,y,z> := PolynomialRing(Rationals(), 3); "
        "I := ideal<R | x+y+z, x*y+y*z+z*x, x*y*z - 1>; "
        "print VarietySizeOverAlgebraicClosure(I);",
    },
]
