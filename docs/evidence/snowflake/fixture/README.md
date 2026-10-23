# Historical Snowflake fixture acceptance

Executed on October 14, 2026 (America/Toronto); exact UTC timestamps are retained in [verification.json](verification.json). This is actual dbt-snowflake execution against a managed warehouse, using the original synthetic fixture. It is not enterprise-scale cloud performance evidence.

**Historical run passed:** three native builds, all 15 model comparisons, the exact fixture oracle, restricted consumer access and publication safety. The run retained 461 query IDs. The final warehouse state was SUSPENDED.

| Native build | Models | Passing dbt tests | Elapsed seconds |
|---|---:|---:|---:|
| [initial_full_build](initial_full_build-results.json) | 15 | 30 | 24.271 |
| [corrected_incremental_build](corrected_incremental_build-results.json) | 15 | 30 | 25.179 |
| [independent_corrected_full_build](independent_corrected_full_build-results.json) | 15 | 30 | 24.341 |

The real late-event batch changed the sampled daily fact. The independent corrected full build used a separate schema. Comparisons use complete rows grouped with duplicate frequency, in both directions, and require matching column contracts.

| Model | Incremental rows | Independent full rows | Different groups, both ways |
|---|---:|---:|---:|
| dim_account | 5 | 5 | 0 / 0 |
| dim_date | 90 | 90 | 0 / 0 |
| fct_booking_events | 4 | 4 | 0 / 0 |
| fct_invoice_lines | 5 | 5 | 0 / 0 |
| fct_opportunity_daily | 362 | 362 | 0 / 0 |
| fct_payment_allocations | 5 | 5 | 0 / 0 |
| fct_subscription_period | 18 | 18 | 0 / 0 |
| mart_arr | 12 | 12 | 0 / 0 |
| mart_cohorts | 4 | 4 | 0 / 0 |
| mart_discrepancy_bridge | 3 | 3 | 0 / 0 |
| mart_flows | 14 | 14 | 0 / 0 |
| mart_metric_values | 24 | 24 | 0 / 0 |
| mart_pipeline | 295 | 295 | 0 / 0 |
| stg_opportunities | 11 | 11 | 0 / 0 |
| stg_subscriptions | 6 | 6 | 0 / 0 |

[Machine-readable model comparisons](model-comparisons.json).

Consumer checks used a separate service identity and RSA key. Raw-table reads, publication writes, switching to RMP_BUILD and switching to RMP_LOADER were denied. The reader saw the validated candidate and final current metrics.

Publication checks executed a deliberate pre-commit failure and verified rollback, executed a genuinely failing source-coverage dbt test while retaining the previous release, restored coverage and passed the gate, then repeated promotion with exactly one history row. [Rejected gate](rejected-coverage-results.json); [restored gate](restored-coverage-results.json).

At the time of the original run, offline CSV and Streamlit checks passed all six cards, regional selection and publication parity. The old financial publication is no longer distributed as current USD evidence. Native query IDs and model-comparison records remain available, but this archived test is not a claim that the revised USD unit contract executed in Snowflake.

The live runtime observed standard X-Small, 60-second auto-suspend and one cluster. Resource-monitor metadata was not visible to the restricted role. Its one-credit NEVER-reset monitor and 90/100 suspension thresholds were confirmed by the owner after successful administrator postflight. That authority is recorded separately from observed metadata. No credential rotation, repeated setup, quota increase or warehouse resizing occurred.

[Query-history snapshot](query-history.json) retains IDs, statuses, timing, scan measurements and reported cloud-service attribution, without SQL text or account/user identifiers. It includes the collector itself as RUNNING with a negative elapsed field reported by Snowflake; that incomplete row is retained but excluded from consumption aggregates. Spill measurements are unavailable through this table function and are not reported as zero.

[Resource consumption and metering probe](resource-consumption.json) records actual measured query activity and the restricted-role metering limitation. Query cloud-service attribution is not warehouse compute credits or final billed consumption. No exact cost or zero-cost claim is made.

Enterprise/stress cloud execution, native process-death/lock-contention experiments and production capacity remain unverified. The existing enterprise scenario is unchanged and remains locally verified. The PR does not claim those broader results.

To reproduce the native run with already registered private credentials and an actually confirmed administrator postflight:

```text
python scripts/verify_snowflake.py --require-cloud --profile fixture --owner-confirmed-fixture-monitor --config-file <private-directory>/runtime.json --workspace .local/new-native-fixture
python scripts/export_snowflake_evidence.py --workspace .local/new-native-fixture
```

The exporter refuses a failed/stale run, checks implementation hashes and model fingerprint, validates the native publication and exercises offline consumers before copying only allowlisted evidence. Raw dbt logs, profiles, manifests, databases and credentials are excluded.
