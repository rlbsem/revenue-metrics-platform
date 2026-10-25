# Controlled selective rebuild measurements

**Decision: retain the original default build.** The candidate reduces pipeline aggregation work but does not meet the predeclared end-to-end performance threshold. Its exact change detection and retained snapshots cost more than the aggregation saves. It remains an explicitly opt-in experimental path for inspection, not a recommended production optimization. No financial or native Snowflake performance claim follows from these measurements.

Local synthetic DuckDB/dbt measurements. No Snowflake execution or commercial cost-saving claim.

The original opportunity fact already replaces changed opportunities. The new opt-in controller compares complete raw multisets against its last successful source snapshot, rebuilds only opportunity descendants for eligible revisions, and replaces the pipeline's affected **account histories**. Other source/identity/policy changes conservatively trigger a full rebuild. Every invocation runs all 30 dbt gates.

## Exact-head baseline

Unchanged main `a39d603cc6d16de8f1d8430abf97ad3db4aa2cbe` passed 71 Python tests, 15 models, 30 dbt tests and the established enterprise verifier locally and in the [manually dispatched Windows/Linux workflow](https://github.com/rlbsem/revenue-metrics-platform/actions/runs/40141230095). The complete archived result is in [exact-main-verification.json](exact-main-verification.json).

## Why this bottleneck

The pilot's corrected build spent 3.837 seconds in `mart_pipeline`, 49.6% of model execution time. It reaggregated 12,470,664 daily opportunity rows into 5,395,893 account-day rows while only 600 of 195,927 opportunities had changed. The [pilot plan](pilot-pipeline-plan.txt) records the actual join/aggregation. This is downstream recomputation, not a new claim about the existing opportunity replacement.

## Enterprise

Executed source `d26b4300338aa0b78f5434b3fca5ae45ab62033a`. {'dbt-core': '1.9.8', 'dbt-duckdb': '1.9.6', 'duckdb': '1.3.2', 'psutil': '7.0.0'}; 3.12.14; Windows-11-10.0.26200-SP0. One DuckDB thread, one dbt thread and 2GB engine memory limit in both arms. Fresh databases, alternating pair order, no concurrent local test load. OS caches were not flushed; these are filesystem-warm paired observations, not cold-cache benchmarks.

| Pair | Original incremental s | Selective total s | Original no-op s | Selective no-op s |
|---|---:|---:|---:|---:|
| 1 | 12.284 | 19.205 | 11.673 | 16.106 |
| 2 | 12.447 | 18.905 | 12.834 | 16.747 |
| 3 | 12.770 | 20.034 | 12.427 | 17.501 |

| Model | Original median s | Selective median s (zero = not rebuilt) |
|---|---:|---:|
| fct_opportunity_daily | 0.895 | 0.948 |
| mart_pipeline | 3.428 | 0.385 |
| fct_invoice_lines | 0.550 | 0.000 |
| mart_flows | 0.438 | 0.000 |

Median corrected rebuild: **12.447s â†’ 19.205s (54.3% increase)**. The predeclared 15% threshold was not met. Median initial builds: original 20.742s, selective 26.997s. Independent corrected full refresh: 20.864s. Selective totals include exact change detection, source snapshot copying, quality gates and checkpointing; source generation/loading, full rowset comparison and consumer hashing are separately timed in the raw records.

All 15 model column contracts and complete multisets matched both original incremental and an independent clean full rebuild in every repetition. All rows are ordered by verified model grains and losslessly exported as typed Parquet (HUGEINT uses exact integer text); exact bytes or every Arrow value are compared. Independent connections preserve each view’s own source binding. Unsupported complex export types fail closed. No probabilistic signatures establish this result. Validated consumer payloads were identical. All amounts retain the existing synthetic USD contract.

[Raw repetitions](enterprise/pair-1.json) Â· [Protocol/source hashes](enterprise/execution-protocol.json) Â· [Generation/load evidence](enterprise/generation.json) Â· [Full plan](enterprise/baseline-plan.txt) Â· [Affected-account plan](enterprise/optimized-plan.txt)

## Stress

Executed source `b13a011ae08dd11abb05cdea03b956ce6bac83ca`. {'dbt-core': '1.9.8', 'dbt-duckdb': '1.9.6', 'duckdb': '1.3.2', 'psutil': '7.0.0'}; 3.12.14; Windows-11-10.0.26200-SP0. One DuckDB thread, one dbt thread and 2GB engine memory limit in both arms. Fresh databases, alternating pair order, no concurrent local test load. OS caches were not flushed; these are filesystem-warm paired observations, not cold-cache benchmarks.

| Pair | Original incremental s | Selective total s | Original no-op s | Selective no-op s |
|---|---:|---:|---:|---:|
| 1 | 48.864 | 57.634 | 41.277 | 46.977 |
| 2 | 42.323 | 56.005 | 44.386 | 47.784 |
| 3 | 43.923 | 58.806 | 41.868 | 47.647 |

| Model | Original median s | Selective median s (zero = not rebuilt) |
|---|---:|---:|
| fct_opportunity_daily | 2.747 | 2.592 |
| mart_pipeline | 16.960 | 1.284 |
| fct_invoice_lines | 1.704 | 0.000 |
| mart_flows | 1.235 | 0.000 |

Median corrected rebuild: **43.923s â†’ 57.634s (31.2% increase)**. The predeclared 15% threshold was not met. Median initial builds: original 68.690s, selective 87.578s. Independent corrected full refresh: 68.156s. Selective totals include exact change detection, source snapshot copying, quality gates and checkpointing; source generation/loading, full rowset comparison and consumer hashing are separately timed in the raw records.

All 15 model column contracts and complete multisets matched both original incremental and an independent clean full rebuild in every repetition. All rows are ordered by verified model grains and losslessly exported as typed Parquet (HUGEINT uses exact integer text); exact bytes or every Arrow value are compared. Independent connections preserve each view’s own source binding. Unsupported complex export types fail closed. No probabilistic signatures establish this result. Validated consumer payloads were identical. All amounts retain the existing synthetic USD contract.

[Raw repetitions](stress/pair-1.json) Â· [Protocol/source hashes](stress/execution-protocol.json) Â· [Generation/load evidence](stress/generation.json) Â· [Full plan](stress/baseline-plan.txt) Â· [Affected-account plan](stress/optimized-plan.txt)

## Cost and safety tradeoffs

Source detection scans exact raw multisets. The controller retains a successful raw snapshot plus a pending snapshot during execution; it adds disk usage and full-build work. It does not eliminate quality-gate scans, the opportunity staging window, source loading or independent reconciliation. Plans can show full physical scans even when fewer rows enter the join/aggregation; no physical partition-pruning guarantee is claimed.

Sampled process-tree RSS may double-count shared pages and miss short peaks; process CPU is sampled, temporary-file measurements miss files created/deleted between samples, and machine load/cache effects remain. The 2GB DuckDB setting is not a process memory cap. Absolute measurements, model timings, resource observations and database sizes are retained rather than extrapolated to Snowflake or production capacity.

The controller requires quiesced raw input and exclusive warehouse ownership. The advisory file serializes controller calls, not arbitrary ingestion writers. A stale lock requires explicit operator verification/removal; it is never silently stolen. This is not a new concurrent ingestion or physical-delete contract. A failed transformation/quality gate leaves the previous published pointer intact; failed pending state cannot authorize skipping corrections. Re-run after repair or rebuild fully. Publication remains the existing validated immutable payload and atomic pointer mechanism.

[Reproduction and contracts](../../performance.md) Â· [Predeclared conditions](protocol.json) Â· [Historical preservation hashes](preserved-files.json). Original fixture, scenario, metrics and Snowflake evidence are preserved; no new vendor or cloud run was performed.
## Resource, verification and larger-batch observations

Wall times include child startup and up to one 0.2s polling interval. Process CPU/RSS are sampled; RSS sums can double-count shared pages. Loading uses the unchanged loader's engine defaults (recorded separately); the paired transformations use one thread and 2GB in both arms. No exclusive host CPU isolation is claimed. Exact comparisons run in the parent and are separately timed; their CPU/RSS peaks were not sampled.

### Enterprise

| Pair | Original CPU s | Candidate CPU s | Original sampled RSS MB | Candidate sampled RSS MB | Original file MB | Candidate file MB | Exact checks s | Consumer check s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 11.859 | 18.797 | 1505.5 | 1367.7 | 397.4 | 503.6 | 102.057 | 13.493 |
| 2 | 12.078 | 18.266 | 1489.1 | 1395.9 | 377.5 | 513.8 | 105.448 | 13.980 |
| 3 | 12.344 | 19.391 | 1424.4 | 1347.3 | 375.9 | 513.8 | 113.275 | 13.767 |

Source correction: 600 / 195,927 opportunities; 300 affected accounts; 89,423 fact rows belong to those account histories. Plans are diagnostic executions separate from timing. Sampled temporary storage can miss brief files; warehouse size is allocated file size, including fragmentation and retained state, not logical bytes or cloud cost.

Larger batch: **10,000 open-prospect opportunity revisions**. Original incremental 13.412s; candidate 21.301s; independent full 22.347s. All 15 complete model rowsets matched both comparisons. These appended amount corrections are a separate adversarial case, not a replacement for canonical published enterprise economics.

### Stress

| Pair | Original CPU s | Candidate CPU s | Original sampled RSS MB | Candidate sampled RSS MB | Original file MB | Candidate file MB | Exact checks s | Consumer check s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 47.453 | 55.656 | 2979.8 | 3041.7 | 1134.8 | 1515.5 | 792.071 | 40.066 |
| 2 | 41.703 | 54.703 | 2978.2 | 3045.9 | 1138.5 | 1408.5 | 818.697 | 40.779 |
| 3 | 43.266 | 57.578 | 2983.4 | 3043.1 | 1302.6 | 1518.1 | 867.016 | 41.349 |

Source correction: 1,800 / 587,461 opportunities; 900 affected accounts; 270,638 fact rows belong to those account histories. Plans are diagnostic executions separate from timing. Sampled temporary storage can miss brief files; warehouse size is allocated file size, including fragmentation and retained state, not logical bytes or cloud cost.
