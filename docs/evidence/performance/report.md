# Controlled selective rebuild measurements

Local synthetic DuckDB/dbt measurements. No Snowflake execution or commercial cost-saving claim.

The original opportunity fact already replaces changed opportunities. The new opt-in controller compares complete raw multisets against its last successful source snapshot, rebuilds only opportunity descendants for eligible revisions, and replaces the pipeline's affected **account histories**. Other source/identity/policy changes conservatively trigger a full rebuild. Every invocation runs all 30 dbt gates.

## Exact-head baseline

Unchanged main `a39d603cc6d16de8f1d8430abf97ad3db4aa2cbe` passed 71 Python tests, 15 models, 30 dbt tests and the established enterprise verifier locally and in the [manually dispatched Windows/Linux workflow](https://github.com/rlbsem/revenue-metrics-platform/actions/runs/40141230095). The complete archived result is in [exact-main-verification.json](exact-main-verification.json).

## Why this bottleneck

The pilot's corrected build spent 3.837 seconds in `mart_pipeline`, 49.6% of model execution time. It reaggregated 12,470,664 daily opportunity rows into 5,395,893 account-day rows while only 600 of 195,927 opportunities had changed. The [pilot plan](pilot-pipeline-plan.txt) records the actual join/aggregation. This is downstream recomputation, not a new claim about the existing opportunity replacement.

## Enterprise

Not completed. See retained blocker or failed-run diagnostics; no performance claim.

## Stress

Not completed. See retained blocker or failed-run diagnostics; no performance claim.

## Cost and safety tradeoffs

Source detection scans exact raw multisets. The controller retains a successful raw snapshot plus a pending snapshot during execution; it adds disk usage and full-build work. It does not eliminate quality-gate scans, the opportunity staging window, source loading or independent reconciliation. Plans can show full physical scans even when fewer rows enter the join/aggregation; no physical partition-pruning guarantee is claimed.

Sampled process-tree RSS may double-count shared pages and miss short peaks; process CPU is sampled, temporary-file measurements miss files created/deleted between samples, and machine load/cache effects remain. The 2GB DuckDB setting is not a process memory cap. Absolute measurements, model timings, resource observations and database sizes are retained rather than extrapolated to Snowflake or production capacity.

The controller requires quiesced raw input and exclusive warehouse ownership. The advisory file serializes controller calls, not arbitrary ingestion writers. A stale lock requires explicit operator verification/removal; it is never silently stolen. This is not a new concurrent ingestion or physical-delete contract. A failed transformation/quality gate leaves the previous published pointer intact; failed pending state cannot authorize skipping corrections. Re-run after repair or rebuild fully. Publication remains the existing validated immutable payload and atomic pointer mechanism.

[Reproduction and contracts](../../performance.md) · [Predeclared conditions](protocol.json) · [Historical preservation hashes](preserved-files.json). Original fixture, scenario, metrics and Snowflake evidence are preserved; no new vendor or cloud run was performed.
