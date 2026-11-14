# Retrieval eval set

`questions.toml` holds the labeled questions; `test_runs.csv` logs every reported test run.
Tooling: `make evalset-check`, `make evalset-show`, `make eval-pool`, `make eval-pool-sheet`,
`make eval-pool-apply`, `make eval-retrieval`.
The checker and `show_question.py` read the corpus files, never the database.

## Format

`version = 1`, then one `[[q]]` table per question, dev first then test, each ordered by id.

| field | type | notes |
|---|---|---|
| id | string | see ID rules |
| split | `dev` or `test` | |
| kind | `answerable` or `oos` | |
| category | string | see categories |
| persona | `borrower`, `broker`, `staff` | |
| query | string | ASCII, ends with `?`, 5 to 30 words |
| answer | string | answerable: text found in a gold chunk; oos: `""` |
| gold | array of chunk_id | chunks that answer the question; empty for oos |
| related | array of chunk_id | useful but not sufficient; empty for oos |
| nonrel | array of chunk_id | judged not relevant |
| origin | string | `new`, or `smoke-Q01` to `smoke-Q12` (dev only) |
| reviewed | bool | set to true by the human reviewer |
| topic | string | oos only; a topic from the manifest |

## ID rules

- dev: D01-D38. D01-D12 are the smoke queries Q01-Q12 in order (exact query from
  `scripts/retrieval_smoke.py`); D13-D30 new answerable; D31-D38 oos.
- test: T01-T62. T01-T50 answerable; T51-T62 oos. Test origin is always `new`.
- Chunk truth is `corpus.iter_chunks(corpus/docs)` plus each doc's front-matter status.

## Categories and quotas

Answerable: exact, paraphrase, confusable, superseded_trap, fine_grained, table, multi_doc.
Oos: oos_far, oos_near.

- dev: 30 answerable (at least 3 per category), 8 oos (4 far, 4 near).
  Far topics: car loans, travel insurance, cryptocurrency, IT password resets.
  Near topics: reverse mortgages, blend-and-extend, cottage or recreational properties,
  co-signers and guarantors.
- test: 50 answerable: exact 8, paraphrase 10, confusable 8, superseded_trap 6, fine_grained 6,
  table 6, multi_doc 6; each persona at least 8. 12 oos (6 far, 6 near) on the other 12 topics.
- Each of the 20 manifest topics is used exactly once across both splits.
- `--final`: every current doc is a gold doc of at least one question (warning above 6).

## Labeling rules

- Query: no PII pattern, no `DMC-` loan number, no document id, no manifest banned term,
  no duplicate (case and whitespace folded, across both splits), not equal to a smoke query
  when in test. Warning when a test query has token Jaccard >= 0.7 with a dev query.
- Answerable: gold non-empty; gold, related and nonrel exist, are disjoint, and gold and related
  hold no superseded chunk; the answer is found in a gold chunk (case-insensitive, whitespace
  collapsed) and not in the query.
- paraphrase: no token shared between query and any gold heading. confusable: at least one gold
  chunk in PRD-001, 002, 003, 004, 006, 007 (gold from other docs is allowed). superseded_trap:
  at least one gold doc in POL-003, POL-009, RATE-001 (gold from other docs is allowed) and the
  answer is in no chunk of the -V1 doc of any such doc. fine_grained: exactly one gold
  chunk, in a doc with at least 5 H2 sections. table: the answer is in a `|` line of a gold
  chunk. multi_doc: gold chunks from at least 2 docs.
- oos: gold and related empty, answer empty, topic from `topics_far` (oos_far) or
  `topics_near` (oos_near).
- Pooling (`make eval-pool`) lists unjudged top-ranked chunks per answerable question; the human
  fills the judgment column and moves chunk ids into gold, related or nonrel.

## Judging a pool

1. `make eval-pool-sheet FILE=reports/eval/pool_<split>_<UTC>.csv` writes `<name>_judge.csv` and
   `<name>_judge.md` next to it. The CSV adds `answer` and `contains_answer` (yes/no, same matching as
   the checker) and is sorted yes first, then qid, best_rank; `judgment` is empty. The markdown shows each
   candidate chunk's full text. No network.
2. Fill `judgment` with `gold`, `related` or `nonrel` (any case; blank = skip). Excel is fine (BOM, CRLF).
3. `make eval-pool-apply FILE=<name>_judge.csv` adds each chunk id to that list of its question and
   rewrites `questions.toml` in the exact existing layout. It prints counts per judgment and per split.
   It refuses (exit 1, file byte-identical) on an unknown qid, a missing or superseded chunk, a chunk
   already in any list of that question, a bad judgment word, or a result that fails `make evalset-check`
   at the default level.

## Metrics

Over answerable questions, rank = 1-based position of the first gold chunk in the top 10
(related chunks do not count; rank 11 or beyond is a miss).

- Hit@k: share with rank <= k (k = 1, 3, 5). MRR@10: mean of 1/rank, 0 for a miss.
- 95% CI: percentile bootstrap of the mean, 2000 resamples, seed 20261111.
- doc Hit@1: top-1 chunk's doc is one of the gold docs. superseded@1: questions whose top-1
  chunk is superseded.
- Paired MRR@10 differences hybrid-bm25 and hybrid-vector use one set of resample indices per
  iteration for both modes.
- oos questions record the top-1 score per mode only.
- Reported to 4 decimals; nothing is rounded before printing.

## Freeze rule and test_runs.csv

Tune on dev, report on test. The test split is run only with a clean git tree (as `git status --porcelain` reports it:
untracked files count, git-ignored files such as `reports/` do not) and every question
`reviewed = true`, so a recorded run always has `git_dirty` false; never edit labels, questions or thresholds to improve a result.
Each test run appends one line per mode to `test_runs.csv` (never rewritten):
`utc,git_commit,git_dirty,questions_sha256,n_queries,mode,hit1,hit3,hit5,mrr10,mrr10_lo,mrr10_hi`.
`questions_sha256` hashes id, query, gold and related of the split, so a changed label is visible.
Commit `test_runs.csv`; `reports/eval/` is git-ignored.
