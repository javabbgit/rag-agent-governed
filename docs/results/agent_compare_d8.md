# Agent eval comparison 2026-11-17T18:26:17Z

split test, repeats 3, tasks 25

measure | A chat-cloud | B chat-cloud-strong
--- | --- | ---
model names | anthropic/claude-haiku-5-5 | anthropic/claude-sonnet-5-5
pass@1 [95% CI] | 0.907 [0.813, 0.987] | 0.947 [0.853, 1.000]
pass-all | 0.840 (21/25) | 0.920 (23/25)
policy checks | 480/480 | 480/480
mean cost per task | 0.004128 | 0.072232
latency p50 / p95 ms | 7229.8 / 15291.1 | 6910.4 / 14281.0

paired difference A - B of per-task k/N: -0.040 [-0.147, 0.080]

category | A pass@1 | A pass-all | B pass@1 | B pass-all
--- | --- | --- | --- | ---
single | 0.944 | 5/6 | 1.000 | 6/6
multi | 0.905 | 6/7 | 1.000 | 7/7
awaiting | 0.667 | 2/4 | 0.917 | 3/4
overreach | 1.000 | 3/3 | 1.000 | 3/3
policy_refusal | 1.000 | 2/2 | 0.500 | 1/2
missing_info | 1.000 | 3/3 | 1.000 | 3/3
