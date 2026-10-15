# Snowflake setup and verification

**Current public evidence status: managed-warehouse execution pending.** The repository's saved acceptance report is local DuckDB evidence. Having a connected ChatGPT Snowflake app is not execution of this verifier: this Python/dbt path uses key-pair authentication and requires its own dedicated demo roles and warehouse. Do not change `executed_cloud` to true until the entire run succeeds and its evidence is reviewed.

## Account setup

1. Use a dedicated demo account/database with permission to create roles, a warehouse and schemas. Review and run [bootstrap.sql](../snowflake/bootstrap.sql) as the named administrator roles.
2. Register a key pair on an appropriate Snowflake user using your account's security process. Keep the private key outside the repository. Assign `RMP_LOADER`, `RMP_BUILD` and `RMP_CONSUMER` for the demo verifier. Separate service identities are preferable for a deployment; this demo verifies active-role boundaries with secondary roles disabled.
3. Set `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_PRIVATE_KEY_PATH`, and the optional private-key passphrase in the shell or secret store. [.env.example](../.env.example) names the variables; no automatic dotenv loading occurs.
4. Run `python scripts/verify_snowflake.py --require-cloud` from the repository root. It returns nonzero for missing credentials, connection failure, failing dbt models/tests, unexpected results or failed access checks.

The Python connector reads the key; dbt uses the same environment-backed key-path profile. The account must permit key-pair authentication for that user. [dbt Snowflake configuration](https://docs.getdbt.com/docs/core/connect-data-platform/snowflake-setup).

## Privilege boundaries

| Role | Allowed operations | Intentionally absent |
|---|---|---|
| RMP_LOADER | Create/replace and load synthetic RAW tables | Consumer publication grants |
| RMP_BUILD | Read RAW; create isolated build schemas; build/test; publish in CONSUMER | RAW mutation privileges |
| RMP_CONSUMER | Warehouse/database/schema usage and SELECT on CURRENT_METRICS | RAW access and publication writes |

This is ordinary Snowflake RBAC, not edition-dependent row-access/masking policy proof. Grants are documented in [bootstrap SQL](../snowflake/bootstrap.sql). The consumer verifier accepts only authorization errors for its negative probes, not arbitrary query failures.

## What the command does

The command replaces **only the synthetic RAW tables in the fixed REVENUE_METRICS_DEMO database**, then builds into fresh `BUILD_<id>` and `FULL_<id>` schemas. Treat that database as exclusively owned by this exercise. Keep old build schemas for investigation or remove them explicitly after review; the script does not recursively drop unrelated database objects.

### Cloud correction and exact-rebuild acceptance sequence

The cloud verifier now performs these distinct operations in order:

1. Load the original synthetic source batch **without** late opportunity revisions.
2. Run a baseline Snowflake/dbt full build and retain bounded opportunity-day evidence.
3. Insert the actual late-event CSV into RAW, and validate generated source economics.
4. Run dbt **incrementally** against the original build schema and confirm that a corrected opportunity changed.
5. Run a separate corrected **full build in another schema**.
6. Compare every model both ways with symmetric Snowflake `EXCEPT` over **complete rows grouped by their counts**. This detects missing, changed **and duplicated** rows, rather than relying on hashes, one-way set differences, or row counts alone.
7. Only after these checks and existing source/metric assertions pass, update the versioned consumer publication and verify that restricted consumers cannot read RAW or mutate the published release.

The verifier saves sanitized model-row counts, correction evidence, query IDs, and three measured dbt-build durations. These are acceptance measurements, **not** proof of cost or throughput until an actual run is recorded. Current adapter unit tests validate SQL construction without claiming cloud semantics. The actual `GROUP BY ALL` / `EXCEPT` statements and incremental pre-hook remain **unverified on Snowflake** until the first successful credentialed run.

**Isolation and budget limits:** RAW and CONSUMER are fixed demo schemas; do not run multiple command-line or externally initiated verifications concurrently. The protected GitHub workflow serializes its own dispatches, but cannot lock out unrelated manual Snowflake sessions. Use only the dedicated synthetic database, start with `--profile fixture`, check the account's actual warehouse size/credit balance, and approve the enterprise run separately. The script does not configure a hard dollar budget, cloud resource monitor or automated teardown. Suspend the demo warehouse when finished and remove isolated build schemas only after reviewing evidence. None of these steps is a basis for an automatic spending authorization.

Successful dbt tests and profile validation precede publication: enterprise scale/economic invariants by default, or hand-authored exact outputs with `--profile fixture`. The consumer payload is replaced in a transaction so all six metrics and their definitions share one version. Historical payloads remain in RELEASE_HISTORY. The read-only view is checked against dbt output. It records sanitized query IDs and permission outcomes in `.local/snowflake/verification.json`, and exports the same validated publication for Streamlit/analyst use.

Raw query logs remain local and are not in the ZIP. Exceptions are summarized by class rather than publishing connection details. Do not commit private keys, `.env`, account-specific profiles or diagnostic logs.

The supplied warehouse starts at XSMALL with auto-suspend; actual charges depend on account configuration and use. No cost/performance estimate is claimed. Suspend/drop the dedicated warehouse when finished if appropriate for your account. Query IDs are execution evidence, not a performance benchmark.

## CI

The manually triggered Snowflake workflow uses a `snowflake-demo` environment and secrets, requests fixture or enterprise explicitly (fixture by default), and serializes protected cloud runs. It never runs on ordinary pull requests. Configure the environment and credentials before dispatch. The local Windows/Linux workflow needs no cloud credentials. Neither workflow is claimed as hosted-successful before GitHub actually runs it.

## Generated sources

The default command generates the enterprise CSVs locally, then loads them through `RMP_LOADER` in bounded batches of 10,000 rows. Use `--source-dir .local/demo/sources` to reuse a generated dataset, or `--profile fixture` for exact cases. The optional stress profile is selectable but has no execution claim. The same loader/build/consumer roles, isolated build schema, dbt quality gates and negative permission probes apply.

Enterprise publications stream source fingerprints and bound account bridge examples; they do not fetch millions of source records into a single Python list or cloud VARIANT payload. The supplied adapter has been parsed offline, but offline parsing does not prove SQL execution or privileges. The [cloud status record](evidence/snowflake-status.json) is the authority for actual execution.
