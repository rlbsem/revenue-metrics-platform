# Q1 2026 commercial review

All values and source systems are synthetic.

| Metric | Value | Definition |
|---|---:|---|
| cash_received | 11000.00000000 | 1.0 |
| cohort_win_rate | 0.20000000 | 1.0 |
| net_bookings | 18000.00000000 | 1.0 |
| net_invoiced | 16000.00000000 | 1.0 |
| open_pipeline | 4000.00000000 | 1.0 |
| period_end_arr | 18000.00000000 | 1.0 |

The 20% win rate is 1 win / 5 mature opportunities; one immature opportunity is excluded.

## Explain the gaps

| Account | Bookings | Net invoices | Cash | Bookings less invoices | Invoices less cash |
|---|---:|---:|---:|---:|---:|
| A1 | 10000.0 | 11000.0 | 8000.0 | -1000.0 | 3000.0 |
| A2 | 6000.0 | 4000.0 | 2000.0 | 2000.0 | 2000.0 |
| A4 | 2000.0 | 1000.0 | 1000.0 | 1000.0 | 0.0 |

The gaps are timing and commercial-definition differences. They are not forced to zero. A1 has a signed amendment and partial credit; A2/A4 have uninvoiced commitments. Allocated cash leaves a net $5,000 invoice gap. This sample has no opening balances; this difference is not a general accounts-receivable balance.

The deliberately incorrect invoice/payment join produces $23,000, instead of $16,000. The conservation test rejects the inflated mart.

The backdated CRM correction changes February 4 pipeline from $12,000 to $21,000. The final quarter-end pipeline remains $4,000. Incremental processing matches a clean rebuild.

ARR policy 2 excludes past-due contracts and changes quarter-end ARR from $18,000 to $12,000. That is a definition change, not a change to source facts.

Release: `6771841bd850e4ae5edd535c9f6ed033128e0b2161ada57b112834dc110ea718`. Read the [analyst export](analyst-export.csv), [modeling cases](modeling-cases.json), [definition change](definition-change.json) and [dbt results](dbt-results.json).
