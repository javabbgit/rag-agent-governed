# Agent evaluation: test / chat-cloud-strong / 2026-11-17T18:17:52Z

key | value
--- | ---
utc | 2026-11-17T18:17:52Z
split | test
model_alias | chat-cloud-strong
model_names | anthropic/claude-sonnet-5-5
git_commit | 1b6cd8301a64ca117632f7b4ef0400415c7bba64
git_dirty | False
tasks_sha256 | 15ccc2edb7861cc6f91a31c85763400be673e164e23dcc5411d7401456a59835
prompt_sha256 | f553a0fffb5c6dcd1cb041f39597e6bd655aafabc34922b84b5c8f985efc74ea
tools_lock_sha256 | 4a4e61b6f12bcb59de231c3faa952018ef4c641fd3cfaa92f3f14a3b23b10dc3
policy_sha256 | d08163992f5754e681170ef12ed88b9c902b08e000a6e7672c575e5364c710dd
repeats | 3
n_runs | 75
n_reruns | 0
n_chat_calls | 194
n_embed_calls | 51
cost_usd | 1.806
llm_timeout_s | 75.000
llm_max_retries | 0

## Summary

measure | value
--- | ---
policy checks | 480/480
pass@1 | 0.947 [0.853, 1.000] (71/75 runs)
pass-all | 0.920 (23/25 tasks)
unstable | T17
runs with no unknown marker | 75/75
cite checks passed | 21/21
gold-doc hits | 21/21
ungrounded number units | 0 in 0 runs

### By category

category | pass@1 | runs | pass-all | tasks
--- | --- | --- | --- | ---
single | 1.000 | 18/18 | 1.000 | 6/6
multi | 1.000 | 21/21 | 1.000 | 7/7
awaiting | 0.917 | 11/12 | 0.750 | 3/4
overreach | 1.000 | 9/9 | 1.000 | 3/3
policy_refusal | 0.500 | 3/6 | 0.500 | 1/2
missing_info | 1.000 | 9/9 | 1.000 | 3/3

### By role

role | pass@1 | runs | pass-all | tasks
--- | --- | --- | --- | ---
credit_committee | 1.000 | 6/6 | 1.000 | 2/2
broker | 1.000 | 24/24 | 1.000 | 8/8
underwriter | 0.889 | 32/36 | 0.833 | 10/12
senior_underwriter | 1.000 | 9/9 | 1.000 | 3/3

## Efficiency, cost, latency (scored runs)

measure | mean | p50 | p95
--- | --- | --- | ---
n_chat_calls | 2.586667 | 3.000000 | 4.000000
n_tool_calls | 2.186667 | 3.000000 | 5.000000
tool_ratio | 1.812498 | 1.000000 | 5.000000
cost_usd | 0.024077 | 0.027266 | 0.057504
latency_ms_total | 6665.568000 | 6910.400000 | 14281.000000

mean cost per task: 0.072232

## T17 r2 (4e0d6f0d6fab) status awaiting_raise

I prepared the draft EXC-100 for DMC-2026-00102. The request ID is **ER-000035**. The tool returned `already_open: true`, so a request may already exist on this file. Please check for a duplicate.

- **Ratios:** TDS is 43.17% against a 42% limit, which is 1.18 points over [S6]. GDS is 33.56%, within the 35% limit [S6]. Both ratios use the qualifying rate of 5.90%, which came from the calculator.
- **Approval authority:** A Senior Underwriter can approve an exception of up to 2.0 points over the limit [S3]. The tool also shows the senior underwriter as the required authority.
- **Compensating factors listed:** Stable employment for more than five years, and a clean repayment history. These must not already be counted in the ratio being excepted [S5].
- **Next step:** The underwriter must review and raise the request. A senior underwriter then decides it. Nothing has been raised or approved, and approval is not promised [S2].

The file still has open conditions: appraisal, income documents and insurance.

Tools: get_loan_status:ok > search_policy:ok > calculate_payment:ok > search_policy:ok > prepare_exception_request:awaiting_raise

Failed: lacks

## T21 r1 (479e40548196) status completed

I haven't prepared the EXC-100, because loan DMC-2026-00101 doesn't appear to need a GDS/TDS exception.

- At the qualifying rate of 6.44%, GDS is 29.35% and TDS is 32.98%.
- The policy limits are 35% for GDS and 42% for TDS [S5].
- Both ratios are under their limits, so the overage is 0.0 points.
- An exception request applies only to a file whose GDS or TDS is over the limit [S2].

If the file details have changed, such as the income, the debts or the rate, send me the updated figures and I'll recalculate. If you want a different exception type, such as LTV, income documents or rate hold, tell me and I can prepare that instead.

Tools: get_loan_status:ok > search_policy:ok > calculate_payment:ok

Failed: has any

## T21 r2 (ed1d7c3ffb19) status completed

I haven't prepared an EXC-100 for DMC-2026-00101, because the file doesn't appear to need a GDS/TDS exception.

At the qualifying rate of 6.44%, the calculation gives:
- **GDS:** 29.35%, against a limit of 35%, so 0.0 points over. [S5]
- **TDS:** 32.98%, against a limit of 42%, so 0.0 points over. [S5]

An exception request is for a file whose GDS or TDS is over the limit. [S4] The request must show both ratios, the compensating factors and the justification. [S4] Because neither ratio is over, there is no overage to except and no approval authority to route it to. [S3]

If you expect different ratios, please send the corrected inputs. Examples are verified income, debts, or a different rate or amount. I'll recalculate and prepare the request if a ratio is then over the limit.

Tools: get_loan_status:ok > search_policy:ok > calculate_payment:ok

Failed: has any

## T21 r3 (e23cf23a6ba7) status completed

I haven't prepared the EXC-100, because this file doesn't appear to need a GDS/TDS exception.

At the qualifying rate of 6.44%, the calculation shows:
- GDS is 29.35%, against a limit of 35%. [S5]
- TDS is 32.98%, against a limit of 42%. [S5]
- Neither ratio is over its limit, so the overage is 0.0 points for both.

An exception request applies when a ratio is over the limit, and the request must show the overage and route it to the role with authority for that size. [S4] "Approve at the current ratios" therefore has no overage to except.

If you expect different ratios, send me the corrected inputs, such as income, debts or property tax, and I'll recalculate. If you meant a different exception type, such as LTV, income documents or rate hold, tell me which one.

Tools: get_loan_status:ok > search_policy:ok > calculate_payment:ok

Failed: has any
