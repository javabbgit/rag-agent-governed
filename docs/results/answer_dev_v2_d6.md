# Answer eval, split dev

- started 2026-11-15T15:07:37Z, finished 2026-11-15T15:11:22Z
- commit e6207ce873fa54552d892966df9ff0172009b209 (dirty: False)
- prompt sha256 caaa182b99ab01358709885dc78e17a1b5cc1b612ece34347039179bb6b8a249
- threshold 0.062085786500000004, rerank_n 10, context_k 5, repeats 3
- models ['anthropic/claude-haiku-5-5']; embeddings 38, chat calls 78, errors 0, cost 0.023393299999999992
- complete: True

## Gate

- low_score: 12 (D11, D24, D25, D27, D31, D32, D33, D34, D35, D36, D37, D38)
- passed: 26 (D01, D02, D03, D04, D05, D06, D07, D08, D09, D10, D12, D13, D14, D15, D16, D17, D18, D19, D20, D21, D22, D23, D26, D28, D29, D30)

## Gate-only rates

- correct_fallback: 1.000 (8/8)
- false_fallback: 0.133 (4/30)

## End to end (all repeats pooled)

- correct_fallback: 1.000 (24/24)
- false_fallback: 0.156 (14/90)
- model_declined: 0.000 (0/78)
- uncited: 0.026 (2/78)
- citation_invalid: 0.000 (0/78)
- empty_output: 0.000 (0/78)
- cited_gold: 1.000 (76/76)
- cited_nonrel_or_unjudged: 0.116 (22/190)
- answer_span_hit: 0.934 (71/76)
- ungrounded_answers: 0.066 (5/76)
- consistent: 0.947 (36/38)
- answer_span_hit_all_repeats: 0.767 (23/30)
- ungrounded_sentences: 6
- spread correct_fallback: 1.000 to 1.000
- spread false_fallback: 0.133 to 0.167
- spread cited_gold: 1.000 to 1.000
- spread answer_span_hit: 0.920 to 0.960

## Per repeat

- repeat 1: correct fallback 1.000 (8/8), false fallback 0.167 (5/30), cited gold 1.000 (25/25), span hit 0.920 (23/25)
- repeat 2: correct fallback 1.000 (8/8), false fallback 0.133 (4/30), cited gold 1.000 (26/26), span hit 0.923 (24/26)
- repeat 3: correct fallback 1.000 (8/8), false fallback 0.167 (5/30), cited gold 1.000 (25/25), span hit 0.960 (24/25)

## Latency (ms)

- embed: p50 58.327404000010574, p95 147.83010900009685
- retrieve: p50 1733.353825999984, p95 2712.8245899994
- rerank: p50 None, p95 None
- generate: p50 1388.5036699994089, p95 3853.4358440001597
- total: p50 2870.308388000012, p95 5419.227544999558

tokens: {'prompt': 143688, 'completion': 18049}
