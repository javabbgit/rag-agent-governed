# Answer eval, split dev

- started 2026-11-15T14:41:55Z, finished 2026-11-15T14:45:59Z
- commit 309679a3536ff82d9feb51c5ae84967b04f646da (dirty: False)
- prompt sha256 4fc0d8e47620b1250c30182dc437a7a68abcaa185b926b7007b537b479c19025
- threshold 0.062085786500000004, rerank_n 10, context_k 5, repeats 3
- models ['anthropic/claude-haiku-5-5']; embeddings 38, chat calls 78, errors 0, cost 0.0235807
- complete: True

## Gate

- low_score: 12 (D11, D24, D25, D27, D31, D32, D33, D34, D35, D36, D37, D38)
- passed: 26 (D01, D02, D03, D04, D05, D06, D07, D08, D09, D10, D12, D13, D14, D15, D16, D17, D18, D19, D20, D21, D22, D23, D26, D28, D29, D30)

## Gate-only rates

- correct_fallback: 1.000 (8/8)
- false_fallback: 0.133 (4/30)

## End to end (all repeats pooled)

- correct_fallback: 1.000 (24/24)
- false_fallback: 0.400 (36/90)
- model_declined: 0.000 (0/78)
- uncited: 0.269 (21/78)
- citation_invalid: 0.000 (0/78)
- empty_output: 0.038 (3/78)
- cited_gold: 1.000 (54/54)
- cited_nonrel_or_unjudged: 0.081 (11/135)
- answer_span_hit: 0.907 (49/54)
- ungrounded_answers: 0.074 (4/54)
- consistent: 0.868 (33/38)
- answer_span_hit_all_repeats: 0.433 (13/30)
- ungrounded_sentences: 7
- spread correct_fallback: 1.000 to 1.000
- spread false_fallback: 0.367 to 0.467
- spread cited_gold: 1.000 to 1.000
- spread answer_span_hit: 0.875 to 0.947

## Per repeat

- repeat 1: correct fallback 1.000 (8/8), false fallback 0.367 (11/30), cited gold 1.000 (19/19), span hit 0.895 (17/19)
- repeat 2: correct fallback 1.000 (8/8), false fallback 0.467 (14/30), cited gold 1.000 (16/16), span hit 0.875 (14/16)
- repeat 3: correct fallback 1.000 (8/8), false fallback 0.367 (11/30), cited gold 1.000 (19/19), span hit 0.947 (18/19)

## Latency (ms)

- embed: p50 61.414245999912964, p95 145.44357600016156
- retrieve: p50 1721.3306040002863, p95 2872.7000150001913
- rerank: p50 None, p95 None
- generate: p50 1574.6420659997966, p95 3934.904883999934
- total: p50 2869.8642909998853, p95 5646.422584999982

tokens: {'prompt': 138852, 'completion': 19391}
