# Metric semantics and the quarterly review

The machine-readable [catalog](../metrics/catalog.json) contains each owner, definition, grain, time basis, filters, dimensions, null behavior, additivity, version and caveats. [SQL](../dbt/models/marts/mart_metric_values.sql) owns formulas. The app formats the returned values only.

## Why the amounts differ

- Pipeline is open potential business at March 31, not the quarter's sum of daily opportunities.
- Bookings are signed event amounts: A1 has 12,000 initial commitment and a -2,000 amendment; A2 has 6,000; A4 has 2,000.
- A1 invoices total 12,000 before a -1,000 credit. A2 invoices 4,000 and A4 invoices 1,000. Net invoicing is therefore 16,000.
- Included cash allocations total 11,000. There are no opening balances or unallocated receipts in this scenario. In general, period invoicing less period cash is not an accounts-receivable balance.
- ARR annualizes eligible recurring contract fees, independent of booking amendment amounts and invoice timing. S1 contributes 12,000 and S2 contributes 6,000 at March end. Trial, one-time, cancelled and expired contracts are excluded.
- ARR policy 1 includes past-due contracts. Policy 2 excludes them, lowering ARR to 12,000. Neither version is labeled universally correct; policy ownership and version are explicit.

The fixture's booking amendment is a commercial commitment adjustment, not a recurring-fee change. That is why it does not automatically change ARR. Financial amounts are in integer cents; the public metric view emits dollar values. No foreign exchange or tax is modeled.

## Cohort interpretation

A win means the first observed won event within 30 days of creation, inclusive. The denominator includes only opportunities whose entire 30-day observation window is within source-covered reporting history. The sample has five mature opportunities and one win; the March opportunity is immature and excluded. Its cohort-specific rate is null, not zero.

The January/February cohort rates must not be averaged. Rollups use summed wins divided by summed mature opportunities. This is a descriptive operational measure, not causal campaign attribution. Origin labels exist in the source but the six-metric consumer deliberately permits only historical region and ALL.

## Policy changes

Use `arr_policy: '2.0'` in dbt to execute the second definition; the verifier demonstrates this separately while retaining the v1 public example. Released metric rows carry their own definition version. A changed version changes the release identifier, making stale consumer comparisons explicit. A new policy requires updated catalog metadata, tests, expected cases and a new release; it cannot be approved by editing a dashboard label alone.
