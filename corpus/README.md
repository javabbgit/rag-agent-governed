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

## Cross-document conventions

Rules carried forward from the written batches. Later documents must stay consistent with them.
Updated after each batch (currently B1 to B3).

- **Owners are departments, by document family.** Credit Risk: POL-001, POL-003, POL-004,
  POL-005, POL-007, POL-010. Underwriting: POL-002, POL-006, POL-008. Compliance: POL-011.
  Mortgage Operations: POL-012, PRC-001, PRC-006, PRC-007. Underwriting also owns PRC-002 and
  PRC-003. Credit Risk also owns PRC-004 and PRC-005. Compliance also owns PRC-008 (the Complaints
  Office is a unit within Compliance). Customer Care owns PRC-009. Product: POL-009 and all PRD
  and RATE documents.
- **Exception limits live only in POL-010 and PRC-005.** Other documents say an exception is
  needed and cite POL-010; they do not repeat the point limits. In POL-010 a Senior Underwriter
  approves a GDS/TDS overage of up to 2.0 points, the Credit Committee up to 4.0 points, and
  nothing above 4.0 points can be excepted. Where both ratios are over, the larger overage decides.
  Senior Underwriter also approves income-document and rate-hold-extension exceptions; the
  Credit Committee approves LTV exceptions. An Underwriter prepares and recommends but approves
  no exception. Decisions are due within 2 business days of a complete EXC-100.
- **Items that can be excepted:** GDS/TDS, LTV, income documents, rate hold extension. Never
  exceptable: privacy rules, fraud flags, property eligibility, a borrowed down payment, the
  qualifying rate, the minimum down payment and the maximum amortization.
- **Straight renewal:** an existing Demo Mortgage Co. mortgage renewed at maturity with the same
  borrowers, for no more than the outstanding balance, with no property change. It is not
  requalified (POL-004). Added money, a borrower change or a property change makes it a new
  application, tested at the qualifying rate.
- **LTV** is the loan amount divided by the lower of purchase price and appraised value. For
  HEL-1 the loan amount is the full credit limit. Product caps: RNT-4 75%, SEA-5 75%, HEL-1
  combined 65%; insurance does not lift a product cap. Under 20% down needs mortgage default
  insurance. Maximum amortization is 25 years insured, 30 uninsured, and a renewal may not extend it.
- **Qualifying rate** is a testing rate only. HEL-1 is qualified on the full credit limit at its
  qualifying rate. Borrowers are never quoted the qualifying rate as their mortgage rate.
- **Default insurance premium:** the borrower pays it and may ask for it to be added to the loan
  (POL-007). No premium rates or percentages are stated anywhere in the corpus.
- **Exceptions (PRC-004, PRC-005 match POL-010 exactly):** form EXC-100 has five fields (loan
  number, exception type, requested value, justification, compensating factors); decided within
  2 business days of a complete request; pending until a human approves; no verbal approval; the
  approval lapses if the loan amount, property or borrower changes; a refused exception is not
  re-raised to another approver. Only the Credit Committee may waive the rate hold extension fee.
- **Loan amount authority (PRC-004):** Underwriter up to 750,000, Senior Underwriter up to
  1,500,000, Credit Committee above. For HEL-1 the amount is the full credit limit. Authority is
  never delegated downward. The higher of the credit and exception authorities decides.
- **Intake and conditions:** applications arrive only through the broker portal or a branch;
  acknowledgement within 1 business day; conditions are due 10 business days before closing and
  are signed off by one role each (Underwriter, or Mortgage Operations for home insurance, payouts
  and title). Funds are released by someone other than the person who approved the file.
- **Renewals (PRC-007):** notice 120 days before maturity, sent to the borrower; options FX5-C,
  FX3-C, VR5-C, FX1-O or payout; no instruction at maturity renews into FX1-O at the rate sheet in
  force that day; no penalty at maturity.
- **Complaints (PRC-008):** acknowledged within 2 business days, final response within 30
  calendar days; path is front line, Complaints Office, external complaints body.
- **Appraisal:** a full appraisal on every file, ordered by Mortgage Operations.
- **Heating** is counted at 100 per month in GDS in every version (POL-003 and POL-003-V1).
- **Closed products** with a prepayment privilege and penalty: FX5-C, FX3-C, SEA-5 and RNT-4
  (FX5-C, SEA-5 and RNT-4 are 5-year closed fixed, FX3-C is 3-year closed fixed) and VR5-C
  (5-year closed variable). FX1-O is open with no penalty. HEL-1 has its own terms in PRD-005.
  Privileges reset on each anniversary of closing and again after a renewal.
- **Payments:** fixed rates compound semi-annually, variable rates monthly. Frequencies are
  monthly, semi-monthly, bi-weekly, weekly, accelerated bi-weekly and accelerated weekly. VR5-C
  payments are recalculated when DMC Prime changes.
- **Privacy:** staff refer to customers by loan number only in chat tools; the privacy rules
  are in POL-011 and the role access rights in PRC-004.
- **Style:** never write "this policy", "this document" or "this procedure" in a body section;
  name the document by ID. Worked calculations go in "Example:" sections, and every must_cover
  number must also appear in a normal section. In examples, other debts are only "a personal
  loan" or "a credit card".

## Checks

- `make corpus-check` validates the documents (use `ALLOW_MISSING=1` while the corpus is incomplete).
- `make corpus-chunks` also writes `reports/corpus/chunks.csv`.
- `make corpus-prescan` (gateway mode only; optional `DOC=<id>`) sends each chunk through the
  embedding path to see whether the gateway's PII rules fire.
