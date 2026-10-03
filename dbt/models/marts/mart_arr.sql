select f.date_day,f.account_id,a.region,a.owner,sum(f.arr_cents) as arr_cents
from {{ ref('fct_subscription_period') }} f join {{ ref('dim_account') }} a
 on f.account_id=a.account_id and f.date_day>=a.valid_from and f.date_day<a.valid_to
group by f.date_day,f.account_id,a.region,a.owner
