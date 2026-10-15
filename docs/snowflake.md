# Snowflake managed-warehouse acceptance

**Cloud acceptance is pending runtime credentials.** Local regression results are not
Snowflake results. On 2026-10-14 the connected ChatGPT SQL tool returned agent error
399504, `Access denied for trial accounts`, for `SELECT 1`. No SQL result was returned.
That proves the connector path is blocked; it does not establish that standard SQL
or key-pair access to this trial is blocked. No account upgrade is required by this
repository. ChatGPT OAuth is independent of Python/dbt authentication.

[Executed local review and source hashes](evidence/snowflake-local-review.json) and
[observed execution blocker](evidence/snowflake-execution-blocker.json) are separate
from native cloud acceptance. The local review records the executed full-suite results.

## Minimum one-time owner handoff

Use Python Connector/dbt-snowflake key-pair authentication. No new OAuth integration,
external model, paid subscription or application hosting is needed. The runner uses
RMP_LOADER/RMP_BUILD. A **different user and key pair** has only RMP_CONSUMER. The
verifier refuses shared identities/keys and unexpected reader role grants, disables
secondary roles, and tests stronger-role switching, raw reads and publication writes.

With Python 3.12 and the locked dependencies installed:

```text
python scripts/prepare_snowflake_access.py --directory <absolute-private-directory-outside-repository>
```

This creates two encrypted RSA keys, private `runtime.json`, and `owner-setup.sql`.
It executes no SQL. Windows folder ACLs restrict access to the creating identity;
POSIX uses owner-only permissions. Passphrases are in the private config: protect
the entire directory. Never upload it or paste its contents into chat.

To update the handoff after review **without generating new keys or changing JSON**:

```text
python scripts/prepare_snowflake_access.py --directory <existing-private-directory> --refresh-owner-sql
```

In Snowsight, create a SQL worksheet, select **ACCOUNTADMIN**, and paste the complete
generated `owner-setup.sql`. Execute the whole file, including the single
`EXECUTE IMMEDIATE $$ ... $$` block. Do not execute selected internal lines or run
the public template directly: the generated private file contains your public keys.

Before any CREATE, the block checks for the exact demo database, warehouse, monitor,
three role names and two service-user names. **Any existing target name stops setup**,
even if it looks compatible. It never adopts, replaces, drops, resizes or rotates keys
on an existing resource. All checks and provisioning share one block so an exception
cannot fall through to a later provisioning statement. A concurrent creation also
fails because CREATE has no IF NOT EXISTS. Do not run another setup concurrently.

The block creates only the dedicated demo objects, retaining administrative ownership.
It checks observed SHOW metadata for standard XSMALL, one cluster, 60-second
auto-suspend, auto-resume, initially suspended state, disabled query acceleration,
and attachment to RMP_FIXTURE_BUDGET. It also checks that monitor's one-credit quota,
NEVER reset frequency, warehouse-level assignment and 90% suspend / 100% immediate
suspend thresholds. A failed postflight raises an error rather than reporting readiness.

Expect a final object with `status: READY_FOR_FIXTURE` and the observed settings.
This confirms setup only, not native acceptance. On **any error**, stop and report
the failing statement/error; do not bypass checks or blindly rerun. DDL auto-commits,
so a failure after provisioning begins can leave newly created objects for review.

After success, set only `SNOWFLAKE_ACCOUNT` in the adjacent private JSON to the
organization/account identifier (not a Snowsight URL), then notify the verifier operator.
Preserve the existing keys and passphrases. No ChatGPT OAuth configuration changes occur.

RMP_BUILD gets **USAGE and OPERATE** on RMP_WH. USAGE permits queries/auto-resume;
OPERATE permits explicit final suspension and already provides warehouse query-history
visibility. MONITOR is redundant and omitted. No runtime role receives warehouse
MODIFY, OWNERSHIP, MANAGE WAREHOUSES or account-wide monitoring privileges.

The SQL includes a one-credit non-resetting fixture resource monitor, 60-second
auto-suspend and query timeouts. Review that bounded allocation before execution.
A credit monitor is not an exact dollar cap and does not cover storage or all cloud
services. The verifier never increases quotas or resizes/provisions resources.
The setup itself briefly resumes only that monitored warehouse to seed the one-row
concurrency gate, then explicitly suspends it; the minimum resume charge can apply.
If setup fails during that write, its 60-second auto-suspend remains the fallback.

```text
python scripts/verify_snowflake.py --require-cloud --profile fixture --config-file <private-directory>/runtime.json --workspace .local/cloud-fixture
```

Environment-based execution requires `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`,
`SNOWFLAKE_PRIVATE_KEY_PATH`, `SNOWFLAKE_CONSUMER_USER`, and
`SNOWFLAKE_CONSUMER_PRIVATE_KEY_PATH`. Each identity also supports its corresponding
`PRIVATE_KEY_PASSPHRASE`. Keep all account/user/key values out of public evidence.

## Native acceptance sequence

1. Observe actual warehouse settings; refuse a larger/multi-cluster, accelerated,
   unmonitored or non-suspending warehouse.
2. Hold an administrator-owned singleton gate in a dedicated DML transaction.
   DDL uses other connections. Supported verifiers serialize their shared
   RAW/CONSUMER access; direct administrator/manual SQL can bypass this protocol
   and must remain quiesced.
3. Load the exact original sources without corrections. CSV headers and row widths
   must match the declared contract (opportunity events have nine fields).
4. Build all 15 models and run all existing dbt tests; retain per-node results.
5. Append the real late-event batch and run native incremental repair. Require an
   observable changed daily fact, not merely a successful command.
6. Independently full-build another schema. Compare every complete-row multiset
   both ways using grouped values/counts and symmetric EXCEPT. Hash-only signatures
   and one-way set differences are insufficient.
7. Apply the unchanged fixture oracle or enterprise source-economic assertions.
   Check candidate metrics with the independent reader before promotion.
8. Prove reader denials, rollback after an injected publication failure, and retention
   of the preceding release after an actual failed source-coverage dbt test. Restore
   coverage and re-run the quality gate.
9. Promote using DML only. Initialize the stable reader view/grant before the
   transaction. Repeat promotion and require one history row; check final reader values.
10. Save sanitized results/query history and suspend the dedicated warehouse before
    releasing the gate, including on execution failure.

These fault probes cover specified pre-commit and quality-gate boundaries, not
universal crash atomicity. A lost commit acknowledgment is uncertain: inspect
CURRENT_RELEASE/RELEASE_HISTORY before retrying. Failed acceptance does not mean
no SQL or committed effect occurred. Native gate contention/process-death and
interrupted incremental recovery need credentialed experiments before being claimed.
Local SQL/fake-cursor tests do not prove Snowflake compatibility or operations.

## Evidence and consumption

The private workspace retains `verification.json`, three `*-results.json` build
records, `query-history.json`, dbt logs/targets and the validated publication for
Streamlit/CSV. Publish only reviewed sanitized projections. Raw dbt manifests,
profiles and logs can include account metadata and are not uploaded by cloud CI.

Query history retains IDs/types/status, elapsed time, scan and spill measurements,
without SQL text, user names or account identifiers. A unique run query tag is
inherited by dbt. Reporting visibility/limits must be checked; this does not establish
complete account-wide audit history.

`snowflake/consumption.sql` provides an owner-run dedicated-warehouse export using
real UTC start/end times. Hour buckets can contain other work and arrive after
reporting latency. Empty output means pending, never zero cost. Separate actual
credits from elapsed time, retention/storage and contracted credit price. A successful
SQL run can have metering pending; the complete acceptance package still needs review.

## Enterprise approval and stopping point

Review the executed fixture, actual warehouse settings, trial balance, metering and
generated row counts first. Estimate the larger run from observed behavior with a
conservative allowance; do not infer credits from time alone. Request owner approval
before enterprise execution or any quota/resource increase. After explicit approval,
set `RMP_ENTERPRISE_APPROVED=yes` and select enterprise. The CLI defaults to fixture
and refuses enterprise cloud execution without this guard. Stress remains optional,
unexecuted, and requires its own `RMP_STRESS_APPROVED=yes` authorization.

Keep BUILD_/FULL_ schemas for investigation until reviewed. The owner may then remove
only recorded run schemas and obsolete candidate grants. No automatic database or
unrelated-object deletion occurs; retained data can incur storage cost.

## GitHub verification

Ordinary Windows/Linux PR checks are credential-free. The manual cloud workflow uses
the protected `snowflake-demo` environment, pinned actions, minimal permissions and
serialized dispatches. Configure the five required settings above as secrets, using
`SNOWFLAKE_PRIVATE_KEY_PEM` and `SNOWFLAKE_CONSUMER_PRIVATE_KEY_PEM` for key contents
instead of local paths; include encrypted-key passphrases. Temporary files are removed
even on failure. Keep secrets out of pull-request workflows and logs.

Start with fixture. Enterprise dispatch additionally requires `enterprise_approved=true`
after explicit owner approval. Cloud CI uploads only allowlisted sanitized JSON,
including failure metadata. Hosted local CI success is not Snowflake execution.
Keep the PR draft while native acceptance is pending.

Primary references: [Python authentication](https://docs.snowflake.com/en/developer-guide/python-connector/python-connector-connect),
[key-pair registration](https://docs.snowflake.com/en/user-guide/key-pair-auth),
[transactions](https://docs.snowflake.com/en/sql-reference/transactions),
[resource monitor limits](https://docs.snowflake.com/en/user-guide/resource-monitors),
[warehouse privileges](https://docs.snowflake.com/en/sql-reference/sql/alter-warehouse#access-control-requirements),
[SHOW warehouse metadata](https://docs.snowflake.com/en/sql-reference/sql/show-warehouses),
[SHOW resource monitors](https://docs.snowflake.com/en/sql-reference/sql/show-resource-monitors),
[metering access and buckets](https://docs.snowflake.com/en/sql-reference/functions/warehouse_metering_history).
