{{ config(materialized='incremental', unique_key='opportunity_day_key', incremental_strategy='delete+insert', on_schema_change='fail', pre_hook="{{ clear_changed_opportunities() }}") }}
with latest as (select * from {{ ref('stg_opportunities') }}),
impacted as (
 select distinct opportunity_id from latest
 {% if is_incremental() %}
 where ingest_seq > (select coalesce(max(last_ingest_seq),0) from {{ this }})
 {% endif %}
), intervals as (
 select e.*,
   max(e.ingest_seq) over (partition by e.opportunity_id) as last_ingest_seq,
   lead(e.effective_date) over (partition by e.opportunity_id order by e.effective_date,e.event_id) as next_effective_date
 from latest e join impacted i on e.opportunity_id = i.opportunity_id
), states as (
 select e.*,d.date_day from intervals e
 join {{ ref('dim_date') }} d on e.effective_date <= d.date_day
  and (e.next_effective_date is null or d.date_day < e.next_effective_date)
 where d.date_day <= cast('{{ var("period_end") }}' as date)
)
select opportunity_id || ':' || cast(date_day as varchar) as opportunity_day_key,
 opportunity_id, account_id, created_date, date_day, stage, amount_cents, origin, last_ingest_seq
from states
