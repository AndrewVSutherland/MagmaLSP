# Magma LLM eval — closed-book vs LSP

| task | domain | closed | lsp | expected |
|---|---|:---:|:---:|---|
| cyclotomic_degree | number theory | ✓ | ✓ | `40` |
| ec_conductor | elliptic curves | ✓ | ✓ | `32` |
| ec_points_ff | elliptic curves | ✓ | ✓ | `105` |
| ec_rank | elliptic curves | ✓ | ✓ | `1` |
| ff_factor | finite fields | ✓ | ✓ | `2` |
| grp_gl_order | groups | ✓ | ✓ | `48` |
| grp_sym_classes | groups | ✓ | ✓ | `22` |
| lattice_min | lattices | err | ✓ | `2` |
| matrix_det | linear algebra | ✓ | ✓ | `4` |
| minpoly_degree | number fields | ✓ | ✓ | `4` |
| modform_dim | modular forms | ✓ | ✓ | `1` |
| nf_class_number | number fields | ✓ | ✓ | `3` |
| nf_discriminant | number fields | ✓ | ✓ | `-23` |
| nth_prime | number theory | ✓ | ✓ | `7919` |
| partitions | combinatorics | ✓ | ✓ | `190569292` |

## Overall

- **closed**: 14/15 correct (93%), 14/15 ran without error
- **lsp**: 15/15 correct (100%), 15/15 ran without error
- **LSP delta: +1 tasks** correct vs closed-book

## By domain (correct/n)

- combinatorics: closed 1/1, lsp 1/1
- elliptic curves: closed 3/3, lsp 3/3
- finite fields: closed 1/1, lsp 1/1
- groups: closed 2/2, lsp 2/2
- lattices: closed 0/1, lsp 1/1
- linear algebra: closed 1/1, lsp 1/1
- modular forms: closed 1/1, lsp 1/1
- number fields: closed 3/3, lsp 3/3
- number theory: closed 2/2, lsp 2/2

## Outcome changes

- LSP fixed (closed ✗ → lsp ✓): ['lattice_min']
- LSP regressed (closed ✓ → lsp ✗): none

## Closed-book failures (error detail)

- `lattice_min`: Bad argument types (Argument types given: MonStgElt, RngIntElt)
