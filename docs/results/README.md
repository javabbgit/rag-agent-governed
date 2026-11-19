# Results snapshot

These are the reports, review sheets and extracts behind every number in the top-level
[README](../../README.md). Dates and commits come from each file's own header, and "-" means the
file does not record that field. Stages: D5 retrieval and fallback, D6
answers, D8 agent, D9 injection, D10 release.

## Files

| file | stage | date (UTC) | git commit | model | backs in README |
|---|---|---|---|---|---|
| retrieval_dev_d5.md / .json | D5 | 2026-11-14 | 0b13ddc | rag-embed (gateway), bge-reranker-base | Limitation 7 |
| retrieval_test_d5.md / .json | D5 | 2026-11-14 | 5c0faaa | rag-embed (gateway), bge-reranker-base | Results (retrieval); Evaluation: retrieval, cited answers (T62); Limitations 6, 7 |
| rerank_sweep_dev_d5.md / .json | D5 | 2026-11-14 | ad51231 | rag-embed (gateway), bge-reranker-base | Evaluation: retrieval (choice of N); Limitation 6 |
| fallback_dev_d5.md / .json | D5 | 2026-11-14 (source report retrieval_dev_20261114T221930Z) | - | rerank scores, no chat model | Evaluation: fallback |
| fallback_test_d5.md / .json | D5 | 2026-11-14 (source report retrieval_test_20261114T225634Z) | - | rerank scores, no chat model | Results (fallback); Evaluation: fallback; Limitation 7 |
| answer_dev_v1_d6.md / .json | D6 | 2026-11-15 | 309679a | anthropic/claude-haiku-5-5 | Evaluation: cited answers (dev v1) |
| answer_dev_v2_d6.md / .json | D6 | 2026-11-15 | e6207ce | anthropic/claude-haiku-5-5 | Evaluation: cited answers (dev v2) |
| answer_review_d6.csv | D6 dev v2 | 2026-11-15 (source run) | e6207ce (source run) | anthropic/claude-haiku-5-5 | Evaluation: cited answers (dev v2); Limitations 8, 10 |
| agent_test_haiku_d8.md / .json | D8 | 2026-11-17 | e758042 | anthropic/claude-haiku-5-5 | Results (agent); Evaluation: agent |
| agent_test_sonnet_d8.md / .json | D8 | 2026-11-17 | 1b6cd83 | anthropic/claude-sonnet-5-5 | Evaluation: agent |
| agent_compare_d8.md | D8 | 2026-11-17 | - | both of the above | Evaluation: agent; Limitation 12 |
| agent_r5_sensitivity_d8.txt | D8 (added in D10) | - | - | Haiku and Sonnet D8 test reports | Evaluation: agent, R5; Limitation 13 |
| agent_review_haiku_d8.csv | D8 | - | - | anthropic/claude-haiku-5-5 | Evaluation: agent (manual unit check) |
| agent_review_sonnet_d8.csv | D8 | - | - | anthropic/claude-sonnet-5-5 | none (empty by design) |
| agent_failures_haiku_d8.csv | D8 (added in D10) | - | - | anthropic/claude-haiku-5-5 | Evaluation: agent (reviewed pass count) |
| agent_failures_sonnet_d8.csv | D8 (added in D10) | - | - | anthropic/claude-sonnet-5-5 | Evaluation: agent (reviewed pass count) |
| attack_matrix_d9.md / .json | D9 | - | 4025821 | none (scripted, no model calls) | Evaluation: injection (matrix) |
| attack_on_d9.md / .json | D9 | 2026-11-18 | 804cf10 | anthropic/claude-haiku-5-5 | Results (injection); Evaluation: injection; Limitation 14 |
| attack_off_d9.md / .json | D9 | 2026-11-18 | 4494bf9 | anthropic/claude-haiku-5-5 | Results (injection); Evaluation: injection; Limitation 14 |
| attack_compare_d9.md | D9 | 2026-11-18 | - | anthropic/claude-haiku-5-5 | Evaluation: injection; Limitation 14 |
| false_positive_rerun_d9.md / .json | D9 | 2026-11-19 | f9d3e83 | anthropic/claude-haiku-5-5 | Results (false positives) |
| denials_d9.csv | D9 (rebuilt in D10) | - | - | rows from the Haiku D8, Sonnet D8 and D9 rerun reports | Results (false positives); Limitation 13 |
| answer_test_d10.md / .json | D10 | 2026-11-19 | 127e7ec | anthropic/claude-haiku-5-5 | Results (fallback, cited answers); Evaluation: cited answers |
| answer_test_d10_r5.md | D10 | 2026-11-19 (source run) | 127e7ec (source run) | anthropic/claude-haiku-5-5 | Evaluation: R5; Limitation 9 |
| answer_review_d10.csv | D10 | - | - | anthropic/claude-haiku-5-5 | Results (cited answers); Limitations 8, 10 |
| reranker_check_d10.txt | D10 | - | - | bge-reranker-base, TEI 1.9.4 | Limitations 1, 2 |
| r5_audit_extract.psv | D10 | rows from 2026-11-15, 2026-11-17, 2026-11-19 | - | gateway audit rows for D6 dev, D8 test, D10 test | Evaluation: R5; Limitations 9, 13 |

## Run record: D10 answer test

The D10 answer test run writes no ledger row, so its record is kept here. All values are copied
from [answer_test_d10.json](answer_test_d10.json).

| field | value |
|---|---|
| utc (started_utc) | 2026-11-19T14:36:16Z |
| commit | 127e7ec1021e68a3ab7fa084f101202cc67c82d9 |
| git_dirty | false |
| split | test |
| repeats | 3 |
| prompt_sha256 | caaa182b99ab01358709885dc78e17a1b5cc1b612ece34347039179bb6b8a249 |
| threshold | 0.062085786500000004 |
| n_embed | 62 |
| n_chat_calls | 126 |
| n_errors | 0 |
| cost_usd | 0.04374309999999998 |
| complete | true |
| report file | answer_test_d10.json |
