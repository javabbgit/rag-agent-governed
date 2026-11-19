# Rerank candidate-size sweep: dev

started 2026-11-14T21:50:57.380Z, finished 2026-11-14T22:10:03.950Z, commit ad51231830bdeef7b95ff763feb37d1e0a736434, dirty False, questions_sha256 0b8d89dfce9955197f08799c549640796b3aed5c7fc2e001c7cad63560049e57

reranker /data/models--BAAI--bge-reranker-base/snapshots/2cfc18c9415c912f9d8155881c133215df768a70 sha None version 1.9.4; n_answerable 30

| N | cand_recall | Hit@1 | Hit@3 | Hit@5 | MRR@10 [95% CI] | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|
| 10 | 0.9667 | 0.8333 | 0.9667 | 0.9667 | 0.9000 [0.8167, 0.9667] | 2761 | 3205 |
| 20 | 1.0000 | 0.8667 | 1.0000 | 1.0000 | 0.9333 [0.8667, 0.9833] | 5354 | 6291 |
| 30 | 1.0000 | 0.8667 | 1.0000 | 1.0000 | 0.9333 [0.8667, 0.9833] | 8166 | 8949 |
| 50 | 1.0000 | 0.8667 | 1.0000 | 1.0000 | 0.9278 [0.8611, 0.9833] | 13681 | 15127 |

selected N = 10
