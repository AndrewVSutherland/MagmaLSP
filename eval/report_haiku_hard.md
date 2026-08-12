# Magma LLM eval (hard tasks, Haiku 4.5, multi-trial) — closed vs raw vs lsp

Cell = correct trials / total trials.

| task | domain | closed | raw | lsp |
|---|---|:---:|:---:|:---:|
| amicable_count | integers | 2/3 | 3/3 | 3/3 |
| aut_d12 | groups | 3/3 | 3/3 | 3/3 |
| bernoulli_digits | integers | 3/3 | 3/3 | 3/3 |
| e8_aut_order | lattices | 2/3 | 3/3 | 2/3 |
| e8_kissing | lattices | 2/3 | 3/3 | 2/3 |
| factors_x15 | polynomials | 2/3 | 3/3 | 3/3 |
| galois_order | number fields | 3/3 | 3/3 | 3/3 |
| golay_mindist | codes | 0/3 | 3/3 | 3/3 |
| hamming_mindist | codes | 2/3 | 3/3 | 3/3 |
| matrix_order_ff | groups | 3/3 | 3/3 | 3/3 |
| mult_order | integers | 2/3 | 3/3 | 3/3 |
| petersen_auto | graphs | 2/3 | 3/3 | 3/3 |
| petersen_chromatic | graphs | 1/3 | 3/3 | 2/3 |
| resultant | polynomials | 3/3 | 3/3 | 3/3 |
| s4_normal | groups | 2/3 | 3/3 | 3/3 |
| smith_largest | linear algebra | 2/3 | 3/3 | 3/3 |
| splitting_degree | number fields | 3/3 | 3/3 | 3/3 |
| sylow2_s8 | groups | 3/3 | 3/3 | 3/3 |
| torsion_units | number fields | 1/3 | 3/3 | 3/3 |
| variety_size | commutative algebra | 1/3 | 3/3 | 3/3 |

## Aggregate

- **closed**: pass@1 70% (42/60 runs), avg task success 70%, solved≥1 95% (19/20 tasks)
- **raw**: pass@1 100% (60/60 runs), avg task success 100%, solved≥1 100% (20/20 tasks)
- **lsp**: pass@1 95% (57/60 runs), avg task success 95%, solved≥1 100% (20/20 tasks)

## Pairwise outcome changes (by task success rate)

- **raw vs closed**: raw better on ['amicable_count', 'e8_aut_order', 'e8_kissing', 'factors_x15', 'golay_mindist', 'hamming_mindist', 'mult_order', 'petersen_auto', 'petersen_chromatic', 's4_normal', 'smith_largest', 'torsion_units', 'variety_size'];
  raw worse on none
- **lsp vs closed**: lsp better on ['amicable_count', 'factors_x15', 'golay_mindist', 'hamming_mindist', 'mult_order', 'petersen_auto', 'petersen_chromatic', 's4_normal', 'smith_largest', 'torsion_units', 'variety_size'];
  lsp worse on none
- **lsp vs raw**: lsp better on none;
  lsp worse on ['e8_aut_order', 'e8_kissing', 'petersen_chromatic']
