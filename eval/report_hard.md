# Magma LLM eval (hard tasks, multi-trial) — closed-book vs LSP

Cell = correct trials / total trials.

| task | domain | closed | lsp |
|---|---|:---:|:---:|
| amicable_count | integers | 3/3 | 3/3 |
| aut_d12 | groups | 3/3 | 3/3 |
| bernoulli_digits | integers | 3/3 | 3/3 |
| e8_aut_order | lattices | 3/3 | 2/3 |
| e8_kissing | lattices | 0/3 | 3/3 |
| factors_x15 | polynomials | 3/3 | 3/3 |
| galois_order | number fields | 3/3 | 3/3 |
| golay_mindist | codes | 0/3 | 3/3 |
| hamming_mindist | codes | 3/3 | 3/3 |
| matrix_order_ff | groups | 3/3 | 3/3 |
| mult_order | integers | 3/3 | 3/3 |
| petersen_auto | graphs | 1/3 | 3/3 |
| petersen_chromatic | graphs | 0/3 | 3/3 |
| resultant | polynomials | 3/3 | 3/3 |
| s4_normal | groups | 3/3 | 3/3 |
| smith_largest | linear algebra | 3/3 | 3/3 |
| splitting_degree | number fields | 3/3 | 3/3 |
| sylow2_s8 | groups | 3/3 | 3/3 |
| torsion_units | number fields | 0/3 | 3/3 |
| variety_size | commutative algebra | 2/3 | 3/3 |

## Aggregate

- **closed**: pass@1 75% (45/60 runs), avg task success 75%, solved≥1 80% (16/20 tasks)
- **lsp**: pass@1 98% (59/60 runs), avg task success 98%, solved≥1 100% (20/20 tasks)

## Where the LSP changed the success rate

- lsp > closed: ['e8_kissing', 'golay_mindist', 'petersen_auto', 'petersen_chromatic', 'torsion_units', 'variety_size']
- lsp < closed: ['e8_aut_order']
