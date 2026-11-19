# Fallback threshold: test

report retrieval_test_20261114T225634Z.json, fingerprint 24486b95d21ff2be2e87cff3c01edd23fe5865ae14e70297df16ef94ae6e1c27, questions_sha256 6136924ca0a5645efb69836b092ced0dc802b028f87a36bb9967009623bc51a7, rerank_n 10

## AUROC

- rerank_top1: 0.9233
- vector_top1: 0.8800

## Threshold

- committed dev threshold: t = 0.062085786500000004
- rates at t: correct fallback 11/12 = 0.9167 [0.7500, 1.0000]; false fallback 9/50 = 0.1800 [0.0800, 0.2800]
  - oos_far: 6/6 fall back (correct)
  - oos_near: 5/6 fall back (correct)
  - exact: 0/8 fall back (false)
  - paraphrase: 6/10 fall back (false)
  - confusable: 0/8 fall back (false)
  - superseded_trap: 0/6 fall back (false)
  - fine_grained: 1/6 fall back (false)
  - table: 1/6 fall back (false)
  - multi_doc: 1/6 fall back (false)
  - answerable passed without gold in top 10: 0

## Test-optimal Youden t = 0.0714 (reference only, not used)

- interval (0.0695, 0.0733], J = 0.8200
- rates at reference t: correct fallback 12/12 = 1.0000 [1.0000, 1.0000]; false fallback 9/50 = 0.1800 [0.0800, 0.2800]
  - oos_far: 6/6 fall back (correct)
  - oos_near: 6/6 fall back (correct)
  - exact: 0/8 fall back (false)
  - paraphrase: 6/10 fall back (false)
  - confusable: 0/8 fall back (false)
  - superseded_trap: 0/6 fall back (false)
  - fine_grained: 1/6 fall back (false)
  - table: 1/6 fall back (false)
  - multi_doc: 1/6 fall back (false)
  - answerable passed without gold in top 10: 0
