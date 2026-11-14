# Fallback threshold

When the best retrieved chunk scores below a threshold, the agent should fall back ("I cannot
answer this from the documents") instead of answering. This directory holds the frozen
threshold and the ledger of test runs. The evaluator is offline: `make eval-fallback` reads one
retrieval report and sends no request.

## Signal

`rerank_top1`: the cross-encoder score (0 to 1) of the top-1 chunk in mode `rerank`.
`vector_top1` (cosine similarity of the top-1 vector hit) is reported for comparison only and is
never used for the threshold. Fallback means `score < t`.

- correct fallback rate: share of out-of-scope questions that fall back (higher is better)
- false fallback rate: share of answerable questions that fall back (lower is better)

## Rule

`youden-midpoint-ties-high` (`metrics.youden_threshold`): candidate intervals from the sorted
unique dev scores `s1..sk` of both groups: `(0, s1], (s1, s2], ..., (sk, 1]`. For each interval
`J = correct - false`. The maximum `J` wins, ties go to the interval with the higher bounds, and
`t` is the interval midpoint. Every `t` inside an interval gives the same rates.

## Freeze order

1. Pick the rerank candidate size on dev (`make eval-rerank-sweep`); set `RERANK_CANDIDATES`.
2. `make eval-retrieval SPLIT=dev` (modes include `rerank`).
3. `make eval-fallback SPLIT=dev REPORT=reports/eval/retrieval_dev_<UTC>.json WRITE_THRESHOLD=1`
   writes `threshold.toml` once. A threshold that is already set is never overwritten.
4. Review and commit `threshold.toml`.
5. On a clean tree: `make eval-retrieval SPLIT=test`, then
   `make eval-fallback SPLIT=test REPORT=reports/eval/retrieval_test_<UTC>.json`.

The test run is refused (exit 2) unless the report is a test report with `git_dirty` false,
`threshold.toml` exists at the report's `git_commit`, and the report's `rerank_n` equals the
threshold's. It applies the committed `t`; the test-optimal Youden `t` is printed as "reference
only, not used".

## Ledger

`test_runs.csv` gets one line per test run: `utc, report_file, report_fingerprint,
questions_sha256, threshold, n_oos, n_oos_fallback, n_answerable, n_answerable_fallback,
correct_rate, false_rate`. Never edit or delete a line.
