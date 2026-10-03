# Source profiles and local execution

All records are synthetic. The enterprise profile is a commercial scenario around CAD750M ARR, not a production scale certification. The same fifteen dbt models and six metric definitions process each profile.

| Profile | Source | Purpose |
|---|---|---|
| fixture | Unchanged hand-authored `data/*.csv` | Exact independent expectations and adversarial modeling cases |
| enterprise (default) | Seed 20261003; 60,000 accounts | Public review, coherent large commercial sources and measured local builds |
| stress (optional) | Same rules; 180,000 accounts | Multi-million-row source workload; not executed in delivered evidence |

The [enterprise configuration](../profiles/enterprise.json), [stress configuration](../profiles/stress.json) and [generator](../src/revenue_platform/generator.py) are committed. Generated CSVs, databases and detailed dbt outputs belong under ignored `.local/`. The ZIP contains the [dataset manifest](evidence/dataset-manifest.json), bounded summaries and small fixture files only. The generator refuses to overwrite a destination.

## Explicit commercial rules

**Installed ARR ≠ quarterly bookings ≠ quarterly invoicing ≠ quarterly cash.** Period-end ARR includes eligible contracts acquired in earlier years. A Q1 booking is required only for a commercial event signed or executed during Q1.

- The installed base accumulated from 2020–2025. Approximately 2% of the ending 60,000 core customers sign during Q1 2026. Annual service anniversaries span all twelve months and days 1–28, so roughly one quarter of existing accounts renew during Q1. Contract acquisition date and the current annual service term are distinct source fields. Core accounts remain active at March 31; approximately 2% of existing accounts (the selected SMB add-on cohort) also discontinue a separate older add-on during Q1, without losing their core subscription.
- Segment mix remains 60% SMB, 30% mid-market and 10% enterprise. Base monthly prices are CAD300, CAD1,500 and CAD4,000. Core/Growth/Suite multiply these by 0.85/1/1.20, with seeded 80–120% negotiated variation. An independent pricing random stream preserves ending enterprise scale while timelines change. No ARR total or quarterly metric is assigned by the generator.
- Billing is monthly for 70% of core contracts and annual for 30%. Monthly invoices occur on the account's billing anniversary; annual invoices occur at the annual service-term start. Three monthly charge components or twelve annual service-period components sum to the fee exactly in cents. Billing detail is not additional customers or subscriptions.
- Current-quarter prospects are independent of installed-base acquisitions. Each account has two expansion/retention proposals with creation dates distributed through Q1. The configured open/won/lost weights are 30/32/38; terminal outcomes take 10–45 days and are visible only when the close falls within the extract. The 30-day mature-cohort metric follows the unchanged dbt definition and is not set to a target rate. Historical acquisition opportunities retain their real historical dates. Renewal opportunities originate 100 days before the renewal, so Q1 renewals come from the prior-quarter pipeline.
- Eligible won monthly-contract proposals become signed seat expansions, offering upgrades or retention contractions at the next billing anniversary. One executed adjustment is modeled per core contract per quarter; other wins can await execution or term-end negotiation. Lost proposals never authorize a booking. A won retention proposal represents acceptance of reduced terms, not new sales growth.
- A fee change creates adjacent, non-overlapping contract fee segments using the existing subscription model. Earlier month-end ARR retains the earlier fee. Final pricing represents the ending installed base; the prior fee is 90% of final for an expansion/upgrade or 110% for a contraction. Amendments book only the signed change for the remaining annual service term using actual days. An adjustment at renewal is included in the renewed annual fee instead of being booked twice.
- New business and renewals book the annual signed commitment. Cancellations reverse the unserved remainder of a previously won add-on agreement. The `commercial_events` source identifies the contract, authoritative opportunity, dates, old/new fees and signed total. `booking_attributes` links each of twelve service-period commitment components to that event. Component counts are disclosed separately from signed commercial-event counts; twelve components are not twelve wins. Negative cancellations cite the historical won agreement being reversed, not a fictitious new win.
- Monthly December invoices and annual invoices from the current service term are retained with original dates for opening receivables and late collections. These are selected historical source records, not a complete multi-year financial ledger. Quarterly marts still filter financial events to Q1. Thus an established subscription can produce invoices and cash without any Q1 booking, and cash can settle a pre-quarter invoice.
- Every fifth account receives a 5% service credit. A cancelled add-on also receives a credit for unused days in its final monthly billing period. Settlement has two allocations with varied delays; one quarter of accounts settle 75% of the net invoice. Allocations after March 31 are excluded. Payments never precede invoices or exceed net invoicing. The invoice-versus-cash flow difference is not an accounts-receivable balance.
- Five regions, twelve teams and 240 owner identifiers remain. Every seventh account changes owner and region February 15. SCD history starts before acquisitions; historical reporting uses effective intervals, not the static initial sales-team descriptor.
- Every 200th account's current prospects receive backdated creation/open revisions with a revised potential amount. All revisions carry a consistent corrected creation date. Verification builds batch 1, then applies batch 2 incrementally; the normal demo loads both batches before publication.
- The reporting calendar and six core coverage assertions remain Q1 2026, in CAD. Earlier supporting rows do not claim complete pre-quarter coverage. Coverage assertions are supplied evidence, not independent proof of upstream completeness.

See [actual acquisition years, renewal months and signed activity](evidence/business-timeline.json) and [source economic checks](evidence/source-economics.json). No firm-wide new-year launch or synchronized renewal is assumed.

The canonical fixture separately retains trials, one-time contracts, precise SCD boundaries and the ARR policy change. Its monetary values are not scaled or regenerated.

## Reproduction and interpretation

```text
revenue demo --profile enterprise --workspace .local/demo
revenue demo --profile fixture --workspace .local/fixture
revenue generate --profile stress --output .local/stress-sources
revenue demo --profile stress --workspace .local/stress-demo
```

The last command is optional and was **not executed** for this delivery. Source generation is streamed; local ingestion uses bulk COPY. The daily opportunity model expands effective intervals into daily snapshots, so model row counts exceed source counts. Generating stress CSVs alone is not evidence of stress dbt throughput.

See [actual enterprise timings and environment](evidence/enterprise-verification.json) and [model timings](evidence/dbt-results.json). Timings are one observed run on a shared workstation, including process startup where applicable; they are not a controlled benchmark. DuckDB uses two worker threads and a 2GB limit during dbt, with one dbt model scheduled at a time. Incremental timing includes rebuilding downstream and other non-incremental models, not just processing changed opportunities. Source files and repeated/full builds may use several GB of local disk.

Source reproducibility compares every generated CSV's SHA-256 across two full generations. Full-refresh comparison checks model row counts and row-hash aggregates, then exact published content equality. Fixture tests separately compare complete ordered model rows. Local source fingerprints cover actual loaded rows, including correction batches; file manifests describe the generated CSV bytes before loading. These are different, explicitly scoped fingerprints.

The subscription-portfolio table rolls up the existing governed ARR fact by descriptive segment/offering. Its filters apply only to that table. They do not imply a product-grained pipeline attribution model or rewrite the six cards' cataloged regional grain.
