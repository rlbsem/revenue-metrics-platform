with regions as (select distinct region from {{ ref('dim_account') }} union all select 'ALL'),
p as (select region,sum(pipeline_cents) as amount from {{ ref('mart_pipeline') }} where date_day=cast('{{ var("period_end") }}' as date) group by region),
f as (select region,sum(bookings_cents) as b,sum(invoiced_cents) as i,sum(cash_cents) as c from {{ ref('mart_flows') }} where event_date between cast('{{ var("period_start") }}' as date) and cast('{{ var("period_end") }}' as date) group by region),
a as (select region,sum(arr_cents) as amount from {{ ref('mart_arr') }} where date_day=cast('{{ var("period_end") }}' as date) group by region),
w as (select region,sum(wins) as wins,sum(mature_opportunities) as mature,sum(opportunities-mature_opportunities) as excluded from {{ ref('mart_cohorts') }} where cohort_month between cast('{{ var("period_start") }}' as date) and cast('{{ var("period_end") }}' as date) group by region),
by_region as (
 select r.region,coalesce(p.amount,0) as pipeline,coalesce(f.b,0) as bookings,coalesce(f.i,0) as invoiced,coalesce(f.c,0) as cash,coalesce(a.amount,0) as arr,
 coalesce(w.wins,0) as wins,coalesce(w.mature,0) as mature,coalesce(w.excluded,0) as excluded
 from regions r left join p on r.region=p.region left join f on r.region=f.region left join a on r.region=a.region left join w on r.region=w.region where r.region<>'ALL'
), combined as (select * from by_region union all select 'ALL',sum(pipeline),sum(bookings),sum(invoiced),sum(cash),sum(arr),sum(wins),sum(mature),sum(excluded) from by_region),
long_metrics as (
 select region,'open_pipeline' as metric_id,pipeline/100.0 as value,cast(null as decimal(18,2)) as numerator,cast(null as decimal(18,2)) as denominator,0 as excluded,'CAD' as unit,'1.0' as definition_version from combined
 union all select region,'net_bookings',bookings/100.0,null,null,0,'CAD','1.0' from combined
 union all select region,'net_invoiced',invoiced/100.0,null,null,0,'CAD','1.0' from combined
 union all select region,'cash_received',cash/100.0,null,null,0,'CAD','1.0' from combined
 union all select region,'period_end_arr',arr/100.0,null,null,0,'CAD','{{ var("arr_policy") }}' from combined
 union all select region,'cohort_win_rate',wins*1.0/nullif(mature,0),wins,mature,excluded,'ratio','1.0' from combined
)
select region,metric_id,cast(value as decimal(18,8)) as value,numerator,denominator,excluded,unit,definition_version,
 cast('{{ var("period_start") }}' as date) as period_start,cast('{{ var("period_end") }}' as date) as period_end
from long_metrics
