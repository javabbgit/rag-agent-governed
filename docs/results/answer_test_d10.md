# Answer eval, split test

- started 2026-11-19T14:36:16Z, finished 2026-11-19T14:42:47Z
- commit 127e7ec1021e68a3ab7fa084f101202cc67c82d9 (dirty: False)
- prompt sha256 caaa182b99ab01358709885dc78e17a1b5cc1b612ece34347039179bb6b8a249
- threshold 0.062085786500000004, rerank_n 10, context_k 5, repeats 3
- models ['anthropic/claude-haiku-5-5']; embeddings 62, chat calls 126, errors 0, cost 0.04374309999999998
- complete: True

## Gate

- low_score: 20 (T10, T11, T12, T14, T15, T16, T37, T42, T50, T51, T52, T53, T54, T55, T56, T57, T58, T59, T60, T61)
- passed: 42 (T01, T02, T03, T04, T05, T06, T07, T08, T09, T13, T17, T18, T19, T20, T21, T22, T23, T24, T25, T26, T27, T28, T29, T30, T31, T32, T33, T34, T35, T36, T38, T39, T40, T41, T43, T44, T45, T46, T47, T48, T49, T62)

## Gate-only rates

- correct_fallback: 0.917 (11/12)
- false_fallback: 0.180 (9/50)

## End to end (all repeats pooled)

- correct_fallback: 1.000 (36/36)
- false_fallback: 0.227 (34/150)
- model_declined: 0.016 (2/123)
- uncited: 0.041 (5/123)
- citation_invalid: 0.000 (0/123)
- empty_output: 0.000 (0/123)
- cited_gold: 0.991 (115/116)
- cited_nonrel_or_unjudged: 0.097 (24/247)
- answer_span_hit: 0.922 (107/116)
- ungrounded_answers: 0.095 (11/116)
- consistent: 0.919 (57/62)
- answer_span_hit_all_repeats: 0.660 (33/50)
- ungrounded_sentences: 20
- spread correct_fallback: 1.000 to 1.000
- spread false_fallback: 0.220 to 0.240
- spread cited_gold: 0.974 to 1.000
- spread answer_span_hit: 0.895 to 0.949

## Per repeat

- repeat 1: correct fallback 1.000 (12/12), false fallback 0.220 (11/50), cited gold 1.000 (39/39), span hit 0.949 (37/39)
- repeat 2: correct fallback 1.000 (12/12), false fallback 0.220 (11/50), cited gold 1.000 (39/39), span hit 0.923 (36/39)
- repeat 3: correct fallback 1.000 (12/12), false fallback 0.240 (12/50), cited gold 0.974 (37/38), span hit 0.895 (34/38)

## Latency (ms)

- embed: p50 63.44622500000696, p95 161.1627160000353
- retrieve: p50 1710.4041279999365, p95 2576.285450999876
- rerank: p50 None, p95 None
- generate: p50 2238.653136999801, p95 3145.835928999986
- total: p50 2951.0180140000557, p95 4939.96641800004

tokens: {'prompt': 238911, 'completion': 39704}
