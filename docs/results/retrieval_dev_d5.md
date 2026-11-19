# Retrieval eval: dev

started 2026-11-14T22:19:30.988Z, finished 2026-11-14T22:21:29.345Z, commit 0b13ddcd1c3c4dae502ddc3c53704e87d693460e, dirty False, questions_sha256 0b8d89dfce9955197f08799c549640796b3aed5c7fc2e001c7cad63560049e57

n_queries 38 (answerable 30, oos 8)

| mode | Hit@1 | Hit@3 | Hit@5 | MRR@10 [95% CI] | doc Hit@1 | superseded@1 |
|---|---|---|---|---|---|---|
| bm25 | 0.7000 | 0.8333 | 0.9000 | 0.7803 [0.6508, 0.8986] | 0.8667 | 0/30 |
| vector | 0.8000 | 0.9333 | 1.0000 | 0.8706 [0.7694, 0.9567] | 0.8667 | 1/30 |
| hybrid | 0.6667 | 0.9000 | 0.9667 | 0.7983 [0.6817, 0.9000] | 0.8667 | 0/30 |
| rerank | 0.8333 | 0.9667 | 0.9667 | 0.9000 [0.8167, 0.9667] | 0.9333 | 0/30 |

## Per category (MRR@10 / Hit@1)

| category | n | bm25 | vector | hybrid | rerank |
|---|---|---|---|---|---|
| exact | 5 | 0.9000 / 0.8000 | 0.9000 / 0.8000 | 0.9000 / 0.8000 | 0.9000 / 0.8000 |
| paraphrase | 4 | 0.2812 / 0.2500 | 0.8125 / 0.7500 | 0.4250 / 0.2500 | 0.6250 / 0.5000 |
| confusable | 3 | 1.0000 / 1.0000 | 0.5667 / 0.3333 | 0.8333 / 0.6667 | 1.0000 / 1.0000 |
| superseded_trap | 3 | 0.7500 / 0.6667 | 1.0000 / 1.0000 | 0.7500 / 0.6667 | 0.8333 / 0.6667 |
| fine_grained | 4 | 0.8750 / 0.7500 | 0.8333 / 0.7500 | 0.8750 / 0.7500 | 0.8750 / 0.7500 |
| table | 3 | 0.8333 / 0.6667 | 1.0000 / 1.0000 | 1.0000 / 1.0000 | 1.0000 / 1.0000 |
| multi_doc | 8 | 0.8167 / 0.7500 | 0.9167 / 0.8750 | 0.8125 / 0.6250 | 1.0000 / 1.0000 |

## Paired MRR@10 differences (mean, 95% CI)

- hybrid-bm25: 0.0181 [-0.0633, 0.1014]
- hybrid-vector: -0.0722 [-0.1889, 0.0250]
- rerank-hybrid: 0.1017 [0.0183, 0.1850]
- rerank-bm25: 0.1197 [0.0350, 0.2242]
- rerank-vector: 0.0294 [-0.0889, 0.1400]

## Paired Hit@1 differences (mean, 95% CI)

- rerank-hybrid: 0.1667 [0.0000, 0.3333]
- rerank-bm25: 0.1333 [0.0333, 0.2667]
- rerank-vector: 0.0333 [-0.1333, 0.2000]

## Rerank

- rerank_n 10, cand_recall 0.9667
- reranker /data/models--BAAI--bge-reranker-base/snapshots/2cfc18c9415c912f9d8155881c133215df768a70 sha None version 1.9.4
- latency per call: p50 2803 ms, p95 3504 ms (38 calls)
