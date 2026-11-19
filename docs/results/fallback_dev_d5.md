# Fallback threshold: dev

report retrieval_dev_20261114T221930Z.json, fingerprint 8373a368ae0c6e06f7eca5b4f049a56bdfc9dba7d8c4a84251a03445732f368d, questions_sha256 0b8d89dfce9955197f08799c549640796b3aed5c7fc2e001c7cad63560049e57, rerank_n 10

## AUROC

- rerank_top1: 0.9667
- vector_top1: 0.9333

## Threshold

- dev Youden, rule youden-midpoint-ties-high: t = 0.062085786500000004
- interval (0.043409023, 0.08076255], J = 0.8667
- rates at t: correct fallback 8/8 = 1.0000 [1.0000, 1.0000]; false fallback 4/30 = 0.1333 [0.0333, 0.2667]
  - oos_far: 4/4 fall back (correct)
  - oos_near: 4/4 fall back (correct)
  - exact: 0/5 fall back (false)
  - paraphrase: 3/4 fall back (false)
  - confusable: 0/3 fall back (false)
  - superseded_trap: 0/3 fall back (false)
  - fine_grained: 0/4 fall back (false)
  - table: 0/3 fall back (false)
  - multi_doc: 1/8 fall back (false)
  - answerable passed without gold in top 10: 0
