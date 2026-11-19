# D10 answer test: gateway R5 redactions and sensitivity analysis

Source run: `answer_test_d10.json` (started 2026-11-19T14:36:16Z, commit 127e7ec, split test,
3 generations per question, model anthropic/claude-haiku-5-5).

## What the gateway audit shows

In the audit window of this run, the gateway logged 126 chat-cloud calls and 62 rag-embed calls.
Three chat-cloud calls were redacted by rule R5 (one redaction each). There were no R10 blocks.

| call | question | generation | detected entity | level | answer status |
| --- | --- | --- | --- | --- | --- |
| 73a657f7-7703-4fc8-ad1a-e4b686f5195b | T29 | 1 | PERSON: 1 | general | answered |
| 652db706-c061-4898-909c-1aa7297b0c99 | T29 | 2 | PERSON: 1 | general | answered |
| d6e84bac-c385-40a3-a671-1c77bf06449a | T29 | 3 | PERSON: 1 | general | answered |

All three redactions fall on T29 ("What is the current rate on a home equity line of credit?"),
one in each of its three generations. The question and the five context chunks are identical in
all three requests, so the redaction comes from fixed prompt text, not from anything the model
wrote. The corpus and the test questions contain no person names (corpus pre-scan: 0 hits), so
this is a false positive.

Classification: PERSON false positive on fixed prompt text. The gateway stores counts only, by
design, so the exact redacted span cannot be recovered. The most likely source is a source header
line (document id, title and section on one line), the same pattern seen in D6 for D05.

Effect on the answers: none observed. All three T29 generations were answered, cited the gold
chunk PRD-005#hel-1-rate-and-qualifying-rate, and gave the correct rate (5.45%).

## Sensitivity analysis

Following the project's rule, redacted runs are kept and counted. The table repeats the main
metrics with the three T29 generations removed.

| metric | all runs | without the 3 redacted runs |
| --- | --- | --- |
| correct fallback (out-of-corpus) | 36/36 | 36/36 |
| false fallback (answerable) | 34/150 | 34/147 |
| uncited (gate passed) | 5/123 | 5/120 |
| model declined (gate passed) | 2/123 | 2/120 |
| citation invalid | 0/123 | 0/120 |
| empty output | 0/123 | 0/120 |
| cited gold (answered) | 115/116 | 112/113 |
| cited nonrel or unjudged (citations) | 24/247 | 23/241 |
| answer span hit (answered) | 107/116 | 104/113 |
| answers with ungrounded numbers (warning only) | 11/116 | 10/113 |

Conclusion: removing the redacted runs does not change any finding.
