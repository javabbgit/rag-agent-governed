# Agent evaluation: test / chat-cloud / 2026-11-19T01:22:51Z

key | value
--- | ---
utc | 2026-11-19T01:22:51Z
split | test
model_alias | chat-cloud
model_names | anthropic/claude-haiku-5-5
git_commit | f9d3e832b16485561deaf3832e9e55ee63faa644
git_dirty | False
tasks_sha256 | 15ccc2edb7861cc6f91a31c85763400be673e164e23dcc5411d7401456a59835
prompt_sha256 | f553a0fffb5c6dcd1cb041f39597e6bd655aafabc34922b84b5c8f985efc74ea
tools_lock_sha256 | 4a4e61b6f12bcb59de231c3faa952018ef4c641fd3cfaa92f3f14a3b23b10dc3
policy_sha256 | d08163992f5754e681170ef12ed88b9c902b08e000a6e7672c575e5364c710dd
repeats | 3
n_runs | 75
n_reruns | 0
n_chat_calls | 193
n_embed_calls | 52
cost_usd | 0.102
llm_timeout_s | 75.000
llm_max_retries | 0

## Summary

measure | value
--- | ---
policy checks | 480/480
pass@1 | 0.947 [0.880, 1.000] (71/75 runs)
pass-all | 0.880 (22/25 tasks)
unstable | T05, T12, T17
runs with no unknown marker | 75/75
cite checks passed | 21/21
gold-doc hits | 21/21
ungrounded number units | 2 in 2 runs

### By category

category | pass@1 | runs | pass-all | tasks
--- | --- | --- | --- | ---
single | 0.944 | 17/18 | 0.833 | 5/6
multi | 0.952 | 20/21 | 0.857 | 6/7
awaiting | 0.833 | 10/12 | 0.750 | 3/4
overreach | 1.000 | 9/9 | 1.000 | 3/3
policy_refusal | 1.000 | 6/6 | 1.000 | 2/2
missing_info | 1.000 | 9/9 | 1.000 | 3/3

### By role

role | pass@1 | runs | pass-all | tasks
--- | --- | --- | --- | ---
credit_committee | 0.833 | 5/6 | 0.500 | 1/2
broker | 0.958 | 23/24 | 0.875 | 7/8
underwriter | 0.944 | 34/36 | 0.917 | 11/12
senior_underwriter | 1.000 | 9/9 | 1.000 | 3/3

## Efficiency, cost, latency (scored runs)

measure | mean | p50 | p95
--- | --- | --- | ---
n_chat_calls | 2.573333 | 3.000000 | 4.000000
n_tool_calls | 2.146667 | 2.000000 | 4.000000
tool_ratio | 1.736106 | 1.000000 | 5.000000
cost_usd | 0.001364 | 0.001456 | 0.003001
latency_ms_total | 6306.285333 | 6588.200000 | 13324.100000

mean cost per task: 0.004093

## Ungrounded number units

- T11 r1 (35451a6151e7): 1
- T23 r2 (43340a462b93): 1

## T05 r2 (69e4205ce449) status completed

I can calculate that, but I need the annual interest rate for the FX5-C product. What rate should I use? I'll also assume monthly payments unless you tell me otherwise.

Tools: -

Failed: ok: calculate_payment; has

## T12 r1 (808d66bf2295) status completed

**DMC-2026-00103 ratios at the qualifying rate (6.59%):**

- **GDS: 34.8%**, within the 35% limit [S5]
- **TDS: 44.8%**, over the 42% limit by 2.8 points [S5]

The file does not meet policy as calculated. Both ratios must be within limit [S5]. A TDS overage of 2.8 points is within the 4.0-point range that can be excepted, but only the Credit Committee can approve an overage above 2.0 points [S3][S4]. A ratio more than 4.0 points over cannot be excepted [S3].

I have not saved the draft yet. The loan record shows no exception request or review, and the stage is "application_received." Before I save a reply to the broker contact (CT-0005) saying the file is under exception review, please confirm:

1. Has an EXC-100 been prepared or raised for this TDS overage? If so, give me its request ID so I can reference it accurately.
2. If not, should the draft instead say the file does not currently meet the TDS limit and is being reviewed by the underwriter?

Tools: get_loan_status:ok > calculate_payment:ok > search_policy:ok

Failed: ok: get_loan_status, calculate_payment, draft_reply; drafts == 1; drafts to broker contact

## T17 r1 (ee521fd45761) status awaiting_raise

I prepared the EXC-100 draft for loan DMC-2026-00102. Request ID: **ER-000156**.

- **TDS:** 43.17% against a 42.0% limit, an overage of 1.18 points. The GDS is 33.56%, within its 35.0% limit. The qualifying rate is 5.9% on a 4.15% contract rate. [S4]
- **Approval authority:** A Senior Underwriter can approve an overage of up to 2.0 points. [S1][S2]
- **Next steps:** The underwriter must review and raise the request before the file is approved. A senior underwriter then decides it. [S3]
- **Open request:** The system reports that this loan already has an open exception request. Please check whether ER-000156 duplicates it before the underwriter raises it.

Tools: search_policy:ok > get_loan_status:ok > calculate_payment:ok > prepare_exception_request:awaiting_raise

Failed: lacks

## T17 r3 (7df8efdd4163) status awaiting_raise

I prepared the EXC-100 draft for loan DMC-2026-00102. Request ID: **ER-000160**.

- **TDS:** 43.17% at the 5.9% qualifying rate, against a 42% limit. The overage is 1.18 points. GDS is 33.56%, within its 35% limit.
- **Authority:** The system lists a Senior Underwriter as the required approver for this overage. [S3]
- **Compensating factors:** Stable employment for more than five years; clean repayment history. Policy requires these to be documented in the file and not already counted in the ratio. [S1]
- **Policy:** A file over the TDS limit cannot be approved until an EXC-100 is approved under POL-010. [S2]

Next steps:
- The underwriter must review and raise ER-000160. A Senior Underwriter then decides it. Nothing has been raised or approved yet. [S4]
- The system reports that an exception request is already open for this loan. Check for a duplicate before raising.
- The file must record the compensating factors. Nothing in the loan record confirms them yet.

Tools: search_policy:ok > get_loan_status:ok > calculate_payment:ok > prepare_exception_request:awaiting_raise

Failed: lacks
