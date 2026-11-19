# Retrieval eval: test

started 2026-11-14T22:56:34.868Z, finished 2026-11-14T22:59:36.332Z, commit 5c0faaaa23f082e737ff8acd259e604ce1fb097a, dirty False, questions_sha256 6136924ca0a5645efb69836b092ced0dc802b028f87a36bb9967009623bc51a7

n_queries 62 (answerable 50, oos 12)

| mode | Hit@1 | Hit@3 | Hit@5 | MRR@10 [95% CI] | doc Hit@1 | superseded@1 |
|---|---|---|---|---|---|---|
| bm25 | 0.7000 | 0.7400 | 0.8000 | 0.7409 [0.6257, 0.8509] | 0.8200 | 0/50 |
| vector | 0.6600 | 0.8600 | 0.9200 | 0.7607 [0.6600, 0.8507] | 0.7600 | 2/50 |
| hybrid | 0.6800 | 0.8400 | 0.9000 | 0.7677 [0.6706, 0.8620] | 0.8000 | 0/50 |
| rerank | 0.7200 | 0.8400 | 0.9400 | 0.7943 [0.6953, 0.8823] | 0.9200 | 1/50 |

## Per category (MRR@10 / Hit@1)

| category | n | bm25 | vector | hybrid | rerank |
|---|---|---|---|---|---|
| exact | 8 | 1.0000 / 1.0000 | 1.0000 / 1.0000 | 1.0000 / 1.0000 | 1.0000 / 1.0000 |
| paraphrase | 10 | 0.2350 / 0.1000 | 0.5250 / 0.4000 | 0.4144 / 0.2000 | 0.6283 / 0.5000 |
| confusable | 8 | 1.0000 / 1.0000 | 0.9000 / 0.8750 | 0.9167 / 0.8750 | 0.9167 / 0.8750 |
| superseded_trap | 6 | 0.5500 / 0.5000 | 0.6944 / 0.5000 | 0.6583 / 0.5000 | 0.6722 / 0.5000 |
| fine_grained | 6 | 0.8571 / 0.8333 | 0.8889 / 0.8333 | 0.8889 / 0.8333 | 0.8667 / 0.8333 |
| table | 6 | 0.8333 / 0.8333 | 0.5139 / 0.3333 | 0.6042 / 0.5000 | 0.6944 / 0.6667 |
| multi_doc | 6 | 0.8750 / 0.8333 | 0.8333 / 0.6667 | 1.0000 / 1.0000 | 0.7833 / 0.6667 |

## Paired MRR@10 differences (mean, 95% CI)

- hybrid-bm25: 0.0269 [-0.0434, 0.0964]
- hybrid-vector: 0.0071 [-0.0753, 0.0923]
- rerank-hybrid: 0.0266 [-0.0411, 0.0956]
- rerank-bm25: 0.0535 [-0.0393, 0.1426]
- rerank-vector: 0.0337 [-0.0403, 0.1153]

## Paired Hit@1 differences (mean, 95% CI)

- rerank-hybrid: 0.0400 [-0.0600, 0.1600]
- rerank-bm25: 0.0200 [-0.1000, 0.1400]
- rerank-vector: 0.0600 [-0.0400, 0.1600]

## Rerank

- rerank_n 10, cand_recall 0.9600
- reranker /data/models--BAAI--bge-reranker-base/snapshots/2cfc18c9415c912f9d8155881c133215df768a70 sha None version 1.9.4
- latency per call: p50 2742 ms, p95 3104 ms (62 calls)
