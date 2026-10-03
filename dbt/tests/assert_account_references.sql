with facts as (
 select account_id,event_date from {{ ref('fct_booking_events') }}
 union all select account_id,event_date from {{ ref('fct_invoice_lines') }}
 union all select account_id,event_date from {{ ref('fct_payment_allocations') }}
 union all select account_id,date_day from {{ ref('fct_opportunity_daily') }}
 union all select account_id,date_day from {{ ref('fct_subscription_period') }}
)
select f.account_id,f.event_date from facts f left join {{ ref('dim_account') }} a
 on f.account_id=a.account_id and f.event_date>=a.valid_from and f.event_date<a.valid_to
group by f.account_id,f.event_date having count(a.account_version_id)<>count(*) or count(a.account_version_id)=0
