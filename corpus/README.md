# Demo Mortgage Co. corpus

Demo Mortgage Co. is fictional. Every number in this corpus is fictional and does not represent
any real regulatory requirement.

The documents were AI-generated and then reviewed one by one by the human owner (see
[REVIEW.md](REVIEW.md)).

Generated with: <model and version>, <date>

License: MIT, like the rest of the repository.

## Layout

- `docs/`: the documents, one Markdown file per document, named `<ID>.md`
  (for example `POL-001.md`). TOML front matter sits between two `+++` lines.
- `facts.toml`: the single source of numeric facts. Current values are under `[section]`,
  superseded v1 values under `[v1.section]`.
- `manifest.toml`: the 40 planned documents (id, title, doc_type, status, batch, must_cover)
  and the out-of-scope topics with their banned terms.
- `REVIEW.md`: the human review log.

## Front matter

Every document starts with TOML front matter between two `+++` lines. All fields below are
required unless marked optional. `make corpus-check` fails on a missing field or a value outside
its allowed set, and on any `id`, `title`, `doc_type` or `status` that differs from
`manifest.toml`.

| Field | Type | Allowed values |
| --- | --- | --- |
| `id` | string | `POL-nnn`, `PRC-nnn`, `PRD-nnn`, `RATE-nnn` or `FAQ-nnn`, with an optional `-V1` suffix; must equal the file name without `.md` |
| `title` | string | must equal the H1 and the manifest title |
| `doc_type` | string | `policy`, `process`, `product`, `rate_sheet`, `faq` |
| `version` | string | non-empty, for example `"2.0"` |
| `status` | string | `current`, `superseded` |
| `effective_date` | TOML date | for example `2026-11-01` |
| `owner` | string | non-empty; a department, not a person |
| `audience` | array of strings | non-empty; each value is `staff`, `broker` or `borrower` |
| `tags` | array of strings | non-empty |
| `superseded_by` | string | required when `status = "superseded"`; a manifest id |
| `supersedes` | string | optional; a manifest id |

A superseded document starts its body, right after the H1, with the sentence
`Superseded on <date> by <ID> version <v>.`

## Chunk IDs

Each document is split into sections, and each section is one chunk. The text between the H1
and the first H2 is the section `summary`. Each H2 starts a section; H3 and deeper stay inside
their H2. The chunk ID is `<doc id>#<slug>`, where the slug is the lower-cased heading with each
run of characters outside `a-z0-9` replaced by `-` and leading and trailing `-` removed.
The summary chunk is `<doc id>#summary`. Example: `POL-003#qualifying-rate`.

## Checks

- `make corpus-check` validates the documents (use `ALLOW_MISSING=1` while the corpus is incomplete).
- `make corpus-chunks` also writes `reports/corpus/chunks.csv`.
- `make corpus-prescan` (gateway mode only; optional `DOC=<id>`) sends each chunk through the
  embedding path to see whether the gateway's PII rules fire.
