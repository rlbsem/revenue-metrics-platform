-- Template: prepare_snowflake_access.py substitutes the two EXISTING public keys.
-- First-time installation only. Execute the entire file in an ACCOUNTADMIN worksheet.
-- All collision checks precede provisioning, in ONE block: an error stops the block.
-- Never skip checks, adopt existing resources, or use CREATE OR REPLACE to retry.
USE ROLE ACCOUNTADMIN;
EXECUTE IMMEDIATE $$
DECLARE
  qid VARCHAR;
  found INTEGER;
  wh OBJECT;
  rm OBJECT;
  administrator_required EXCEPTION (-20008, 'STOP: ACCOUNTADMIN must be the active role. No provisioning attempted.');
  database_conflict EXCEPTION (-20001, 'STOP: demo database name already exists. No provisioning attempted.');
  warehouse_conflict EXCEPTION (-20002, 'STOP: demo warehouse name already exists. No provisioning attempted.');
  monitor_conflict EXCEPTION (-20003, 'STOP: demo resource monitor name already exists. No provisioning attempted.');
  role_conflict EXCEPTION (-20004, 'STOP: a demo role name already exists. No provisioning attempted.');
  user_conflict EXCEPTION (-20005, 'STOP: a demo service user name already exists. No provisioning attempted.');
  warehouse_invalid EXCEPTION (-20006, 'STOP: warehouse postflight failed. Review newly created demo resources; do not rerun.');
  monitor_invalid EXCEPTION (-20007, 'STOP: monitor postflight failed. Review newly created demo resources; do not rerun.');
BEGIN
  IF (CURRENT_ROLE() <> 'ACCOUNTADMIN') THEN RAISE administrator_required; END IF;
  -- ACCOUNTADMIN sees account resources; exact comparisons avoid LIKE wildcard collisions.
  SHOW DATABASES LIKE 'REVENUE_METRICS_DEMO';
  qid := SQLID;
  SELECT COUNT(*) INTO :found FROM TABLE(RESULT_SCAN(:qid))
    WHERE UPPER("name") = 'REVENUE_METRICS_DEMO';
  IF (found <> 0) THEN RAISE database_conflict; END IF;

  SHOW WAREHOUSES LIKE 'RMP_WH';
  qid := SQLID;
  SELECT COUNT(*) INTO :found FROM TABLE(RESULT_SCAN(:qid))
    WHERE UPPER("name") = 'RMP_WH';
  IF (found <> 0) THEN RAISE warehouse_conflict; END IF;

  SHOW RESOURCE MONITORS LIKE 'RMP_FIXTURE_BUDGET';
  qid := SQLID;
  SELECT COUNT(*) INTO :found FROM TABLE(RESULT_SCAN(:qid))
    WHERE UPPER("name") = 'RMP_FIXTURE_BUDGET';
  IF (found <> 0) THEN RAISE monitor_conflict; END IF;

  SHOW ROLES LIKE 'RMP_%';
  qid := SQLID;
  SELECT COUNT(*) INTO :found FROM TABLE(RESULT_SCAN(:qid))
    WHERE UPPER("name") IN ('RMP_LOADER', 'RMP_BUILD', 'RMP_CONSUMER');
  IF (found <> 0) THEN RAISE role_conflict; END IF;

  SHOW USERS LIKE 'RMP_ACCEPTANCE_%';
  qid := SQLID;
  SELECT COUNT(*) INTO :found FROM TABLE(RESULT_SCAN(:qid))
    WHERE UPPER("name") IN ('RMP_ACCEPTANCE_RUNNER', 'RMP_ACCEPTANCE_READER');
  IF (found <> 0) THEN RAISE user_conflict; END IF;

  -- No IF NOT EXISTS: a concurrent collision also fails instead of adopting an object.
  -- ACCOUNTADMIN retains ownership; runtime identities receive only the grants below.
  CREATE RESOURCE MONITOR RMP_FIXTURE_BUDGET WITH CREDIT_QUOTA=1
    FREQUENCY=NEVER START_TIMESTAMP=IMMEDIATELY
    TRIGGERS ON 90 PERCENT DO SUSPEND ON 100 PERCENT DO SUSPEND_IMMEDIATE;
  CREATE WAREHOUSE RMP_WH WAREHOUSE_TYPE=STANDARD WAREHOUSE_SIZE=XSMALL
    AUTO_SUSPEND=60 AUTO_RESUME=TRUE INITIALLY_SUSPENDED=TRUE
    ENABLE_QUERY_ACCELERATION=FALSE
    STATEMENT_TIMEOUT_IN_SECONDS=300 STATEMENT_QUEUED_TIMEOUT_IN_SECONDS=60;
  ALTER WAREHOUSE RMP_WH SET RESOURCE_MONITOR=RMP_FIXTURE_BUDGET;

  -- Check observed settings BEFORE any table DML can resume the new warehouse.
  SHOW WAREHOUSES LIKE 'RMP_WH';
  qid := SQLID;
  SELECT OBJECT_CONSTRUCT(*) INTO :wh FROM TABLE(RESULT_SCAN(:qid)) WHERE "name"='RMP_WH';
  IF (NOT COALESCE(
      REPLACE(UPPER(wh:size::VARCHAR), '-', '')='XSMALL'
      AND wh:type::VARCHAR='STANDARD' AND wh:state::VARCHAR='SUSPENDED'
      AND wh:auto_suspend::INTEGER=60 AND wh:auto_resume::BOOLEAN
      AND wh:min_cluster_count::INTEGER=1 AND wh:max_cluster_count::INTEGER=1
      -- SHOW omits this column when the account has no query-acceleration feature.
      AND NOT COALESCE(wh:enable_query_acceleration::BOOLEAN, FALSE)
      AND wh:resource_monitor::VARCHAR='RMP_FIXTURE_BUDGET', FALSE)) THEN
    RAISE warehouse_invalid;
  END IF;

  SHOW RESOURCE MONITORS LIKE 'RMP_FIXTURE_BUDGET';
  qid := SQLID;
  SELECT OBJECT_CONSTRUCT(*) INTO :rm FROM TABLE(RESULT_SCAN(:qid))
    WHERE "name"='RMP_FIXTURE_BUDGET';
  -- SHOW may render trigger thresholds with a percent suffix; no extra thresholds accepted.
  IF (NOT COALESCE(
      rm:credit_quota::NUMBER(38,9)=1 AND UPPER(rm:frequency::VARCHAR)='NEVER'
      AND rm:level::VARCHAR='WAREHOUSE' AND rm:start_time IS NOT NULL
      AND rm:end_time IS NULL
      AND TRY_TO_NUMBER(REPLACE(rm:suspend_at::VARCHAR, '%', ''))=90
      AND TRY_TO_NUMBER(REPLACE(rm:suspend_immediately_at::VARCHAR, '%', ''))=100,
      FALSE)) THEN RAISE monitor_invalid; END IF;

  CREATE DATABASE REVENUE_METRICS_DEMO;
  CREATE SCHEMA REVENUE_METRICS_DEMO.RAW;
  CREATE SCHEMA REVENUE_METRICS_DEMO.CONSUMER;
  CREATE SCHEMA REVENUE_METRICS_DEMO.OPS;
  CREATE TABLE REVENUE_METRICS_DEMO.OPS.ACCEPTANCE_GATE (singleton INTEGER, holder VARCHAR);
  CREATE ROLE RMP_LOADER;
  CREATE ROLE RMP_BUILD;
  CREATE ROLE RMP_CONSUMER;
  GRANT ROLE RMP_LOADER TO ROLE SYSADMIN;
  GRANT ROLE RMP_BUILD TO ROLE SYSADMIN;
  GRANT ROLE RMP_CONSUMER TO ROLE SYSADMIN;
  GRANT USAGE ON WAREHOUSE RMP_WH TO ROLE RMP_LOADER;
  GRANT USAGE, OPERATE ON WAREHOUSE RMP_WH TO ROLE RMP_BUILD;
  -- OPERATE is needed for final suspension and already permits warehouse query history.
  -- No redundant MONITOR, no MODIFY, and no account-wide warehouse privilege.
  GRANT USAGE ON WAREHOUSE RMP_WH TO ROLE RMP_CONSUMER;
  GRANT USAGE ON DATABASE REVENUE_METRICS_DEMO TO ROLE RMP_LOADER;
  GRANT USAGE ON DATABASE REVENUE_METRICS_DEMO TO ROLE RMP_BUILD;
  GRANT USAGE ON DATABASE REVENUE_METRICS_DEMO TO ROLE RMP_CONSUMER;
  GRANT USAGE, CREATE TABLE ON SCHEMA REVENUE_METRICS_DEMO.RAW TO ROLE RMP_LOADER;
  GRANT USAGE ON SCHEMA REVENUE_METRICS_DEMO.RAW TO ROLE RMP_BUILD;
  GRANT SELECT ON ALL TABLES IN SCHEMA REVENUE_METRICS_DEMO.RAW TO ROLE RMP_BUILD;
  GRANT SELECT ON FUTURE TABLES IN SCHEMA REVENUE_METRICS_DEMO.RAW TO ROLE RMP_BUILD;
  GRANT CREATE SCHEMA ON DATABASE REVENUE_METRICS_DEMO TO ROLE RMP_BUILD;
  GRANT USAGE, CREATE TABLE, CREATE VIEW ON SCHEMA REVENUE_METRICS_DEMO.CONSUMER TO ROLE RMP_BUILD;
  GRANT USAGE ON SCHEMA REVENUE_METRICS_DEMO.CONSUMER TO ROLE RMP_CONSUMER;
  GRANT USAGE ON SCHEMA REVENUE_METRICS_DEMO.OPS TO ROLE RMP_BUILD;
  GRANT SELECT, UPDATE ON TABLE REVENUE_METRICS_DEMO.OPS.ACCEPTANCE_GATE TO ROLE RMP_BUILD;

  CREATE USER RMP_ACCEPTANCE_RUNNER TYPE=SERVICE RSA_PUBLIC_KEY='__RUNNER_PUBLIC_KEY__'
    DEFAULT_ROLE=RMP_BUILD DEFAULT_SECONDARY_ROLES=() DEFAULT_WAREHOUSE=RMP_WH;
  CREATE USER RMP_ACCEPTANCE_READER TYPE=SERVICE RSA_PUBLIC_KEY='__READER_PUBLIC_KEY__'
    DEFAULT_ROLE=RMP_CONSUMER DEFAULT_SECONDARY_ROLES=() DEFAULT_WAREHOUSE=RMP_WH;
  GRANT ROLE RMP_LOADER TO USER RMP_ACCEPTANCE_RUNNER;
  GRANT ROLE RMP_BUILD TO USER RMP_ACCEPTANCE_RUNNER;
  GRANT ROLE RMP_CONSUMER TO USER RMP_ACCEPTANCE_READER;

  -- Only this one-row initialization needs compute; use ONLY the monitored demo WH.
  -- It may incur the warehouse's minimum resume charge. No dbt/model run occurs here.
  USE WAREHOUSE RMP_WH;
  INSERT INTO REVENUE_METRICS_DEMO.OPS.ACCEPTANCE_GATE VALUES (1, 'idle');
  ALTER WAREHOUSE RMP_WH SUSPEND;
  RETURN OBJECT_CONSTRUCT('status', 'READY_FOR_FIXTURE',
    'warehouse_size', wh:size, 'auto_suspend_seconds', wh:auto_suspend,
    'monitor', wh:resource_monitor, 'credit_quota', rm:credit_quota,
    'frequency', rm:frequency, 'suspend_at', rm:suspend_at,
    'suspend_immediately_at', rm:suspend_immediately_at,
    'native_acceptance_executed', FALSE);
END;
$$;
-- Snowflake DDL auto-commits. On ANY error, stop and review; never blindly rerun.
-- Keys, ChatGPT OAuth, and unrelated resources are not altered by this script.
