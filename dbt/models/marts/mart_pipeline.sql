{% if var('selective_pipeline', false) %}
  {% if target.type != 'duckdb' %}{{ exceptions.raise_compiler_error('Selective pipeline requires local DuckDB') }}{% endif %}
  {{ config(materialized='incremental', incremental_strategy='append', on_schema_change='fail', pre_hook="{{ clear_pipeline_accounts() }}") }}
{% endif %}
select f.date_day,f.account_id,a.region,a.owner,
 sum(case when f.stage='open' then f.amount_cents else 0 end) as pipeline_cents
from {{ ref('fct_opportunity_daily') }} f
join {{ ref('dim_account') }} a on f.account_id=a.account_id and f.date_day>=a.valid_from and f.date_day<a.valid_to
{% if var('selective_pipeline', false) and is_incremental() %}
where f.account_id in (select account_id from assurance_control.affected_accounts)
{% endif %}
group by f.date_day,f.account_id,a.region,a.owner
