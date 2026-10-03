# Commercial model decisions

The project starts with economic events, not an event-processing framework. Sales commitments, invoice lines, cash allocations and recurring-contract balances have different grains and time semantics. No single raw join can safely make them one measure.

## Model graph

Two staging models select current CRM event revisions and calculate eligible annual contract fees. Two dimensions describe account history and reporting dates. Five facts preserve opportunities by day, signed bookings, invoice/credit lines, payment allocations and subscription balances. Six marts provide pipeline, independently aggregated flows, ARR, cohorts, six metric rows and the discrepancy bridge: fifteen models total.

All model grain descriptions are in [dbt schema documentation](../dbt/models/schema.yml). The generated manifest summary is [lineage evidence](evidence/dbt-lineage.json).

## History and incremental processing

Account history is an input contract of complete SCD2 intervals; dbt does not pretend to recover old ownership from current records. Tests reject overlapping intervals and facts without an applicable account. Financial events use account attributes at event date; pipeline/ARR use snapshot date; cohorts use creation date. Current-owner aggregation is deliberately not offered as an interchangeable historical dimension.

CRM events have stable event IDs and monotonically increasing received batch sequences. The staging view selects the latest received revision for each event before applying effective dates. The incremental opportunity fact replaces the full affected opportunity history, including removal of obsolete day keys when an effective date moves forward. Downstream marts rebuild. This is single-writer, complete-source-history processing; it does not support arbitrary source deletion, concurrent ingestion, or a report-window change without a full refresh.

The late fixture was received after the initial quarter review and backdates O2's creation/open amount. It restates February 4 pipeline from 12,000 to 21,000 CAD without changing quarter-end pipeline. Release source fingerprints distinguish the two received histories. This is not a general bitemporal query engine.

## Shared metrics and consumers

`mart_metric_values` implements fixed SQL metrics over independently aggregated models. Region rollups sum eligible win counts and denominators, not subgroup percentages. Balances are selected at period end, never summed over dates. [The catalog](../metrics/catalog.json) documents definitions but does not execute expressions. MetricFlow is intentionally omitted: the bounded SQL model already supplies the required shared layer without a new framework or an unvalidated service claim.

The app and analyst exporter share a reader of immutable validated model results. A content-derived release ID binds source fingerprint, model fingerprint, definitions, period, metrics, bridge and coverage. An expected-release argument detects stale consumer requests; content tampering fails integrity checks. Publication uses an atomic local pointer only after dbt and profile-specific validation pass (independent exact fixture expectations or enterprise source/economic checks). The prior release stays readable on a transformation or quality failure.

This is a small release boundary supporting commercial consumption. It is not a general orchestration or control-plane product. There is no claim of signed artifacts, multi-writer coordination, adversarial filesystem defense or a production serving SLA.

## Warehouse boundary

DuckDB runs the local model graph. Snowflake uses the same SQL with a small adapter-specific date-add macro. Its verifier creates an isolated build schema and runs actual dbt before publishing one validated JSON payload transactionally. A consumer view exposes metrics from that payload. The role verifies raw reads and release mutations are denied with secondary roles disabled. A demo user with all roles can deliberately switch roles; real deployment requires separately provisioned identities and account policy.

Snowflake execution, RBAC enforcement, query behavior and performance remain pending until the configured verifier succeeds. The code is not presented as a measured cloud deployment.

## Enterprise execution without a new architecture

The same fifteen dbt models serve all profiles. The daily opportunity model derives non-overlapping effective-date intervals before expanding to reporting days. This avoids sorting a joined row for every historical event on every later day; event-ID tie breaking and full affected-history replacement are preserved. Local loading uses DuckDB bulk CSV COPY rather than Python row-by-row inserts.

Generated sources include one account descriptor per account (segment and initial sales team) and one offering/opportunity descriptor per contract. These descriptive sidecars support a bounded subscription-portfolio breakdown; historical ownership remains exclusively in the SCD2 dimension. They are not a new metric layer. The six governed commercial cards retain the catalog's region dimension.

The enterprise publication contains all metric/cohort rows, coverage, portfolio aggregates and the first 20 account bridges. It hashes sorted source rows in bounded batches instead of embedding the raw dataset. Complete facts and account bridges remain in the local warehouse. Dataset manifests contain source file hashes, configuration and row counts, with no absolute paths. See [profile economics](profiles.md).

## Established-company source timeline

Acquisition history, annual service terms and fee-effective intervals are separate source concepts. Generated amendments supply adjacent contract fee segments to the existing subscription models; no dbt model or metric definition changed. Older installed contracts contribute period-end ARR regardless of Q1 bookings. The same quarterly flow marts handle current invoices and collections on selected opening invoices.

Generated `commercial_events` and `booking_attributes` provide auditable authority for booking components. Source checks run during both local and Snowflake loading: signed totals reconcile to components, booking authority is won rather than lost, invoice charges match effective contract fees, and contract intervals do not overlap. Cancellation authority is the prior won agreement being reversed. These source descriptors support the existing model graph; they do not introduce another orchestration or metric framework.
