select f.date_day,f.account_id,a.region,a.owner,
 sum(case when f.stage='open' then f.amount_cents else 0 end) as pipeline_cents
from {{ ref('fct_opportunity_daily') }} f
join {{ ref('dim_account') }} a on f.account_id=a.account_id and f.date_day>=a.valid_from and f.date_day<a.valid_to
group by f.date_day,f.account_id,a.region,a.owner
