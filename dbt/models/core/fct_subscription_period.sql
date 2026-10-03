select c.contract_id || ':' || cast(d.date_day as varchar) as contract_period_key,
 c.contract_id,c.account_id,d.date_day,
 case when c.start_date <= d.date_day and d.date_day < c.end_date
 {% if var('arr_policy') == '2.0' %}and c.past_due = 0{% endif %}
 then c.annual_cents else 0 end as arr_cents
from {{ ref('stg_subscriptions') }} c cross join {{ ref('dim_date') }} d
where d.is_month_end = 1
