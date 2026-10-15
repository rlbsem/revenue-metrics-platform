-- Owner executes after the fixture. Do not grant account-wide monitoring to the
-- consumer just to obtain metering. Replace only the two UTC timestamps with
-- started_at/finished_at from the sanitized verifier record.
USE ROLE ACCOUNTADMIN;
SELECT START_TIME, END_TIME, CREDITS_USED_COMPUTE, CREDITS_USED_CLOUD_SERVICES,
       CREDITS_USED
FROM TABLE(REVENUE_METRICS_DEMO.INFORMATION_SCHEMA.WAREHOUSE_METERING_HISTORY(
  DATE_RANGE_START => TO_TIMESTAMP_LTZ('<started_at>'),
  DATE_RANGE_END => TO_TIMESTAMP_LTZ('<finished_at>'), WAREHOUSE_NAME => 'RMP_WH'))
ORDER BY START_TIME;
-- Hour buckets include all activity on this dedicated warehouse in those hours,
-- not exact per-query billing. Newly completed activity can be absent initially.
-- An empty result is pending, never zero cost. Storage/retention and contractual
-- credit price are separate. Retain only these columns, not account details.
