# D2 corpus spec - input tables for task D2-A

Human-authored input for corpus/facts.toml and corpus/manifest.toml.
All numbers are fictional on purpose and do not represent any real regulatory requirement.
A dash in the v1 column means there is no v1 value.

## Table 1 - Facts

| Key | Current | v1 | Note |
| --- | --- | --- | --- |
| ratios.gds_max_pct | 35 | 33 | GDS limit |
| ratios.tds_max_pct | 42 | 40 | TDS limit |
| ratios.heating_monthly_cad | 100 | 100 | heating cost counted in GDS |
| ratios.condo_fee_share_pct | 50 | 50 | share of condo fees counted in GDS |
| qualifying.buffer_pts | 1.75 | - | qualifying rate = contract rate + buffer |
| qualifying.floor_pct | 5.40 | - | qualifying rate floor; the greater of the two applies |
| down_payment.tier1_pct | 6 | - | on the first 400,000 of the price |
| down_payment.tier1_cap_cad | 400000 | - | |
| down_payment.tier2_pct | 12 | - | on the portion above 400,000 |
| down_payment.high_price_threshold_cad | 1200000 | - | at this price or more, minimum 20% down |
| down_payment.high_price_min_pct | 20 | - | |
| down_payment.savings_history_days | 90 | - | own savings need a 90-day history |
| ltv.conventional_max_pct | 80 | - | above this, default insurance is required |
| ltv.rental_max_pct | 75 | - | RNT-4 |
| ltv.sea5_max_pct | 75 | - | SEA-5 |
| ltv.heloc_combined_max_pct | 65 | - | HEL-1 combined LTV |
| amortization.insured_max_years | 25 | - | |
| amortization.uninsured_max_years | 30 | - | |
| income.employment_letter_max_age_days | 30 | - | |
| income.rental_inclusion_pct | 50 | - | share of gross rents counted as income |
| income.self_employed_min_years | 2 | - | 2-year average |
| prepayment.lump_sum_pct | 18 | 15 | per anniversary year, on original principal |
| prepayment.payment_increase_pct | 18 | 15 | |
| prepayment.penalty_months_interest | 3 | 3 | fixed rate: greater of this or IRD |
| exceptions.senior_uw_max_pts | 2.0 | - | GDS/TDS points over the limit |
| exceptions.committee_max_pts | 4.0 | - | |
| exceptions.sla_business_days | 2 | - | |
| authority.underwriter_max_cad | 750000 | - | |
| authority.senior_uw_max_cad | 1500000 | - | above this: Credit Committee |
| rates.effective_date | 2026-11-01 | 2026-08-01 | rate sheet effective date |
| rates.prime_pct | 4.70 | 4.95 | DMC Prime |
| rates.FX5C_pct | 4.69 | 4.89 | 5-year fixed closed |
| rates.FX3C_pct | 4.84 | 5.04 | 3-year fixed closed |
| rates.VR5C_spread_pts | -0.55 | -0.45 | 5-year variable = prime + spread |
| rates.FX1O_pct | 6.99 | 7.19 | 1-year fixed open |
| rates.HEL1_spread_pts | 0.75 | 0.75 | HELOC = prime + spread |
| rates.RNT4_pct | 5.09 | 5.29 | rental property |
| rates.SEA5_pct | 5.19 | 5.39 | self-employed |
| rate_hold.purchase_days | 120 | - | |
| rate_hold.refinance_days | 60 | - | |
| fees.discharge_cad | 295 | - | |
| fees.nsf_cad | 45 | - | |
| fees.payment_change_cad | 25 | - | |
| fees.rate_hold_extension_cad | 150 | - | |
| process.intake_ack_business_days | 1 | - | |
| process.conditions_due_business_days | 10 | - | before closing |
| process.renewal_notice_days | 120 | - | before maturity |
| complaints.ack_business_days | 2 | - | |
| complaints.final_response_days | 30 | - | calendar days |
| payment.fixed_compounding | semi-annual | - | |
| payment.variable_compounding | monthly | - | |
| loan_number.format | DMC-YYYY-NNNNN | - | example DMC-2026-00123 |

## Table 2 - Document list

| ID | Title | Type | Must cover |
| --- | --- | --- | --- |
| POL-001 | Credit Policy Overview | policy | policy scope; document hierarchy (policy > process > FAQ); exceptions go through POL-010; owners are departments |
| POL-002 | Income Verification | policy | salaried: recent pay stub + employment letter dated within 30 days; variable income: 2-year average; rental income: 50% counted |
| POL-003 | Debt Service Ratio Limits (GDS/TDS) | policy | GDS max 35%, TDS max 42%; GDS includes heating 100/month and 50% of condo fees; ratios use the qualifying rate (POL-004) |
| POL-004 | Qualifying Rate | policy | greater of contract rate + 1.75 pts or 5.40%; applies to all products incl. HEL-1; straight renewals are not requalified |
| POL-005 | Down Payment Requirements and Sources | policy | 6% of first 400,000 + 12% of remainder; 20% at price 1,200,000 or more; own savings need 90-day history; gifts need a signed gift letter; borrowed down payment not accepted |
| POL-006 | Property Eligibility | policy | eligible: 1-4 unit residential homes and condos in Canada (non-owner-occupied only under RNT-4); ineligible: vacant land, mobile homes without permanent foundation; full appraisal required |
| POL-007 | Loan-to-Value and Default Insurance | policy | conventional max LTV 80%; above 80% requires mortgage default insurance; max amortization 25 years insured, 30 uninsured |
| POL-008 | Self-Employed Borrowers | policy | minimum 2 years in business; 2-year average net income; notices of assessment for 2 years; SEA-5 option |
| POL-009 | Prepayment Privileges and Penalties | policy | 18% lump sum per anniversary year on original principal; payment increase up to 18%; fixed penalty = greater of 3 months' interest or IRD; variable = 3 months' interest; FX1-O no penalty; unused privileges do not carry forward |
| POL-010 | Exceptions to Credit Policy | policy | exceptable items (GDS/TDS, LTV, income documents, rate hold extension); Senior Underwriter up to +2.0 pts, Credit Committee up to +4.0 pts; compensating factors; never exceptable: privacy rules, fraud flags |
| POL-011 | Customer Data Privacy and Handling | policy | never type SIN, card or bank account numbers into chat tools; refer to customers by loan number only; least-privilege access by role |
| POL-012 | Payment Calculation Method | policy | amortization formula; fixed compounded semi-annually, variable monthly; frequencies incl. accelerated bi-weekly and weekly; round payments to the cent |
| PRC-001 | Application Intake | process | channels (broker portal, branch); minimum data for a complete application; loan number format DMC-YYYY-NNNNN; acknowledgement within 1 business day |
| PRC-002 | Underwriting Workflow | process | 8 stages from file assignment to decision, one H2 per stage; outcomes: approve, approve with conditions, decline, refer to exception |
| PRC-003 | Conditions and Document Checklist | process | standard conditions; conditions due 10 business days before closing; who signs off each condition type |
| PRC-004 | Approval Authority Matrix | process | roles: Customer Service Representative, Loan Officer, Underwriter, Senior Underwriter, Credit Committee; what each may view and approve; Underwriter up to 750,000, Senior Underwriter up to 1,500,000 |
| PRC-005 | Exception Request Procedure | process | form EXC-100 fields: loan number, exception type, requested value, justification, compensating factors; routing by authority; 2-business-day SLA; a request stays pending until a human approves; no verbal approvals |
| PRC-006 | Funding and Closing | process | closing instructions to the borrower's lawyer; final conditions check; funds release steps; post-closing file audit |
| PRC-007 | Renewals and Maturity | process | renewal notice 120 days before maturity; renewal options; straight renewal without requalification; no instruction at maturity renews into FX1-O |
| PRC-008 | Complaint Handling and Escalation | process | acknowledge within 2 business days; final response within 30 days; path: front line, Complaints Office, external complaints body |
| PRC-009 | Customer Communication Standards | process | never promise approval or a rate beyond a rate hold; cite the policy ID; plain language; every draft reviewed by a human before sending |
| PRD-001 | 5-Year Fixed Closed (FX5-C) | product | product code FX5-C; 5-year term; prepayment per POL-009; penalty greater of 3 months' interest or IRD; payment frequencies |
| PRD-002 | 3-Year Fixed Closed (FX3-C) | product | product code FX3-C; 3-year term; same privileges as FX5-C; who it suits |
| PRD-003 | 5-Year Variable Closed (VR5-C) | product | DMC Prime minus 0.55; payments adjust when prime changes; may convert to a fixed term at least as long as the remaining term; penalty 3 months' interest |
| PRD-004 | 1-Year Fixed Open (FX1-O) | product | prepay any amount at any time without penalty; higher rate than closed terms; default term at maturity (PRC-007) |
| PRD-005 | Home Equity Line of Credit (HEL-1) | product | DMC Prime plus 0.75; combined LTV max 65%; interest-only minimum payment; qualified at the qualifying rate |
| PRD-006 | Rental Property Mortgage (RNT-4) | product | non-owner-occupied 1-4 units; max LTV 75%; 50% of gross rents counted as income |
| PRD-007 | Self-Employed Advantage Mortgage (SEA-5) | product | 5-year fixed for self-employed borrowers; 2 years in business; max LTV 75% |
| RATE-001 | Rate Sheet (effective 2026-11-01) | rate_sheet | table of all product rates and DMC Prime; qualifying rate per product; a rate hold locks a rate, otherwise rates may change |
| RATE-002 | Fee Schedule | rate_sheet | discharge 295; NSF 45; payment change 25; rate hold extension 150; fees not listed are not charged |
| RATE-003 | Rate Holds and Rate Locks | rate_sheet | purchases 120 days, refinances 60 days; extension needs an exception (POL-010) plus the extension fee; a rate hold is not an approval |
| FAQ-001 | Borrower FAQ: Making Your Payments | faq | lay language: changing payment date or frequency; what happens after a missed payment; fees 45 and 25 |
| FAQ-002 | Borrower FAQ: Paying Off Your Mortgage Faster | faq | lay-language version of POL-009 ("pay extra", "pay off early", "penalty") |
| FAQ-003 | Borrower FAQ: Renewing Your Mortgage | faq | lay-language version of PRC-007 |
| FAQ-004 | Broker FAQ: Submissions and Conditions | faq | broker view of PRC-001 and PRC-003 |
| FAQ-005 | Borrower FAQ: Property Tax and Home Insurance | faq | optional property tax account; proof of home insurance required at closing and at each renewal |
| FAQ-006 | Staff FAQ: Using the Policy Assistant | faq | answers come with sources; says no supporting document was found when unsure; cannot submit or approve without a human; never paste SIN or account numbers |
| POL-003-V1 | Debt Service Ratio Limits (GDS/TDS) | policy, superseded | GDS 33%, TDS 40%; superseded on 2026-09-01 by POL-003 version 2.0 |
| POL-009-V1 | Prepayment Privileges and Penalties | policy, superseded | 15% lump sum, 15% payment increase; superseded on 2026-06-01 by POL-009 version 2.0 |
| RATE-001-V1 | Rate Sheet (effective 2026-08-01) | rate_sheet, superseded | v1 rates; superseded on 2026-11-01 by RATE-001 version 2.0 |

## Table 3 - Out-of-scope topics

These topics must never appear in the corpus, not even to say they are not offered.
Banned terms are matched case-insensitively at a word start.

| Group | Topics | Banned terms |
| --- | --- | --- |
| far | car loans; student loans; travel insurance; RRSP or investment advice; cryptocurrency; life insurance claims; property tax appeals; employee vacation policy; IT password resets; commercial real estate | car loan, auto loan, student loan, travel insurance, RRSP, crypto, bitcoin, life insurance, tax appeal, vacation, password, commercial |
| near | reverse mortgages; bridge financing; mortgage portability; blend-and-extend; construction draw mortgages; cottage or recreational properties; co-signers and guarantors; foreign buyer rules; payment deferral or skip-a-payment; mortgage assumption by a buyer | reverse mortgage, bridge financing, bridge loan, portab, blend, construction, cottage, recreational, co-signer, cosigner, guarantor, foreign buyer, deferral, skip-a-payment, assumable, assumption of the mortgage |
