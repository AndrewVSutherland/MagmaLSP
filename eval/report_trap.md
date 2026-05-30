# Magma LLM eval (hard tasks, multi-trial) — closed vs raw vs lsp

Cell = correct trials / total trials.

| task | domain | closed | raw | lsp |
|---|---|:---:|:---:|:---:|
| cusp_dim | modular forms | 2/3 | 3/3 | 3/3 |
| distinct_partitions | combinatorics | 3/3 | 3/3 | 3/3 |
| exp_floor | real analysis | 3/3 | 3/3 | 3/3 |
| field_disc | number fields | 3/3 | 3/3 | 3/3 |
| int_quotient | integers | 3/3 | 3/3 | 3/3 |
| largest_class | groups | 0/3 | 3/3 | 3/3 |
| leading_coeff | polynomials | 3/3 | 3/3 | 3/3 |
| minpoly_deg | linear algebra | 3/3 | 3/3 | 3/3 |
| omega_1e6 | integers | 3/3 | 3/3 | 3/3 |
| proper_div_sum | integers | 3/3 | 3/3 | 3/3 |
| qr_mod7 | finite fields | 3/3 | 3/3 | 3/3 |
| real_roots | polynomials | 1/3 | 3/3 | 3/3 |
| roots_mult | polynomials | 3/3 | 3/3 | 3/3 |
| subgroups_s4 | groups | 1/3 | 3/3 | 3/3 |

## Aggregate

- **closed**: pass@1 81% (34/42 runs), avg task success 81%, solved≥1 93% (13/14 tasks)
- **raw**: pass@1 100% (42/42 runs), avg task success 100%, solved≥1 100% (14/14 tasks)
- **lsp**: pass@1 100% (42/42 runs), avg task success 100%, solved≥1 100% (14/14 tasks)

## Pairwise outcome changes (by task success rate)

- **raw vs closed**: raw better on ['cusp_dim', 'largest_class', 'real_roots', 'subgroups_s4'];
  raw worse on none
- **lsp vs closed**: lsp better on ['cusp_dim', 'largest_class', 'real_roots', 'subgroups_s4'];
  lsp worse on none
- **lsp vs raw**: lsp better on none;
  lsp worse on none
