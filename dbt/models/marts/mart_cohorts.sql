with opportunities as (
 select opportunity_id,account_id,min(created_date) as created_date,
 min(case when stage='won' then effective_date end) as won_date
 from {{ ref('stg_opportunities') }} group by opportunity_id,account_id
), qualified as (
 select o.*,d.month_start as cohort_month,a.region,
 case when {{ add_days('o.created_date',30) }} <= cast('{{ var("period_end") }}' as date) then 1 else 0 end as mature,
 case when won_date <= {{ add_days('o.created_date',30) }} then 1 else 0 end as won_within_window
 from opportunities o join {{ ref('dim_date') }} d on o.created_date=d.date_day
 join {{ ref('dim_account') }} a on o.account_id=a.account_id and o.created_date>=a.valid_from and o.created_date<a.valid_to
)
select cohort_month,region,count(*) as opportunities,sum(mature) as mature_opportunities,
 sum(case when mature=1 then won_within_window else 0 end) as wins,
 cast(sum(case when mature=1 then won_within_window else 0 end) * 1.0 / nullif(sum(mature),0) as decimal(18,8)) as win_rate
from qualified group by cohort_month,region
