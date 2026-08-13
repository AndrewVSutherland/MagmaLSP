# Magma LLM eval (trap tasks, Haiku 4.5, multi-trial) — closed vs raw vs lsp

Cell = correct trials / total trials.

| task | domain | closed | raw | lsp |
|---|---|:---:|:---:|:---:|
| cusp_dim | modular forms | 3/3 | 3/3 | 3/3 |
| distinct_partitions | combinatorics | 3/3 | 3/3 | 2/3 |
| exp_floor | real analysis | 0/3 | 3/3 | 3/3 |
| field_disc | number fields | 0/3 | 2/3 | 3/3 |
| int_quotient | integers | 3/3 | 3/3 | 3/3 |
| largest_class | groups | 0/3 | 3/3 | 3/3 |
| leading_coeff | polynomials | 3/3 | 3/3 | 3/3 |
| minpoly_deg | linear algebra | 3/3 | 3/3 | 3/3 |
| omega_1e6 | integers | 1/3 | 3/3 | 3/3 |
| proper_div_sum | integers | 3/3 | 3/3 | 3/3 |
| qr_mod7 | finite fields | 3/3 | 3/3 | 3/3 |
| real_roots | polynomials | 2/3 | 3/3 | 3/3 |
| roots_mult | polynomials | 3/3 | 3/3 | 3/3 |
| subgroups_s4 | groups | 0/3 | 2/3 | 3/3 |

## Aggregate

- **closed**: pass@1 64% (27/42 runs), avg task success 64%, solved≥1 71% (10/14 tasks)
- **raw**: pass@1 95% (40/42 runs), avg task success 95%, solved≥1 100% (14/14 tasks)
- **lsp**: pass@1 98% (41/42 runs), avg task success 98%, solved≥1 100% (14/14 tasks)

## Pairwise outcome changes (by task success rate)

- **raw vs closed**: raw better on ['exp_floor', 'field_disc', 'largest_class', 'omega_1e6', 'real_roots', 'subgroups_s4'];
  raw worse on none
- **lsp vs closed**: lsp better on ['exp_floor', 'field_disc', 'largest_class', 'omega_1e6', 'real_roots', 'subgroups_s4'];
  lsp worse on ['distinct_partitions']
- **lsp vs raw**: lsp better on ['field_disc', 'subgroups_s4'];
  lsp worse on ['distinct_partitions']
