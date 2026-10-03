# Scenario-to-test map

Run `python scripts/verify.py` for dependency checks, lint, integration tests, actual dbt builds, deterministic replay and generated evidence. The test suite performs database mutations and runs the relevant dbt tests; it does not merely assert that SQL strings contain keywords.

| Required scenario | Executed test / evidence |
|---|---|
| Invoice/payment join fanout | `test_fanout_is_detected_by_dbt`; 23,000 wrong versus 16,000 correct |
| SCD2 region/owner change | `test_historical_owner_boundary_and_current_owner_difference`; exact boundary dates |
| Booking amendment | `test_booking_amendment_and_partial_credit_remain_separate` |
| Partial invoice credit | Same test: -1,000 credit retains its event semantics |
| Late/backdated CRM correction | `test_backdated_correction_incremental_full_equivalence`; February 4 restatement |
| Incremental/full-build equivalence | Same test compares all fifteen model rowsets |
| Ratio aggregation error | `test_ratio_uses_counts_not_average_rates` |
| Immature cohorts | `test_immature_cohort_is_not_a_loss`; null rate and excluded count |
| Non-additive snapshots | `test_pipeline_is_not_additive_over_time` |
| ARR eligibility | `test_arr_eligibility_and_end_exclusive` |
| Missing source coverage | Parametrized missing row and stale date; dbt rejects both |
| Unmatched account | Parametrized orphan booking fact; dbt rejects it |
| Definition version change | `test_metric_definition_change_is_new_release` |
| Consumer mismatch / stale result | `test_consumer_parity_and_streamlit`, `test_stale_consumer_and_tampered_payload_rejected` |
| Failed transformation or promotion | `test_failed_build_or_quality_gate_preserves_release`; missing source table and missing coverage |

Additional checks cover overlapping SCD intervals, invalid contract terms, payment-to-invoice reference, obsolete keys after forward effective-date correction, catalog completeness and release path traversal. [Expected values](../data/expected.json) are independently authored arithmetic, not SQL-generated expectations.

The Streamlit AppTest executes the application, verifies all six displayed values and changes region to exercise an immature-only rate. It does not establish browser compatibility across every platform. Local verification is Windows/Python 3.12; the included Linux/Windows CI matrix must run on GitHub before hosted success can be claimed.

The source coverage contract is trusted upstream evidence. Tests verify that required coverage exists and spans the configured quarter; they cannot prove an external system delivered all records. Likewise, source SCD intervals are supplied history, not history inferred from a current CRM export.

## Two evidence layers

The original files in `data/` remain unchanged. The [fixture evidence](evidence/fixture/commercial-review.md) retains the exact 18,000 / 16,000 / 11,000 commercial bridge, fanout proof and ARR policy change. No generated expectation replaces these hand-authored amounts.

`tests/test_generation.py` adds repeated byte hashes, seed sensitivity, source booking/contract and invoice/term relationships, descriptor references, actual generated dbt builds and non-no-op late corrections. Negative mutations prove that missing contract references and over-allocation fail dbt checks. The original SCD2, source coverage, fact conservation and reference tests also execute over the full enterprise dataset.

`python scripts/verify.py` runs both layers. Enterprise verification regenerates all source files twice, compares SHA-256 manifests, measures actual loading and builds, and compares every model after incremental corrections versus full refresh using row count, XOR and sum of row hashes. Those signatures are probabilistic multiset checks, not a claim of bytewise rowset proof; the fixture still compares exact full rowsets. Published metrics, source fingerprints and bounded review content must match exactly. [Executed results and timings](evidence/enterprise-verification.json).

`python scripts/verify_fixture.py` is a quicker fixture/generator-test run. `python scripts/verify_enterprise.py` runs the full-sized layer. Packaging requires the combined verifier's passed record. No stress profile execution is claimed.

## Established-company regression checks

Generated-source tests now assert multi-year acquisitions, historic active contracts, fee-segment boundaries, quoted contract fees, annual/new commitments, day-prorated amendments/cancellations and booking-component conservation. A deliberately substituted lost opportunity must fail the booking-authority check. The same source checks run across the complete enterprise workload and before Snowflake builds when credentials are provided. The public enterprise guard also checks retained ARR scale, a staggered Q1 renewal share, substantial installed accounts without Q1 bookings, and a broad plausible mature-cohort range. These are regression guards, not generator target outputs.

All original fixture CSVs and expected amounts remain unchanged. The existing dbt graph, metric contracts and thirty dbt tests are preserved.
