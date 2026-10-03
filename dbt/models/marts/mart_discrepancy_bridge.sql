with totals as (
 select account_id,sum(bookings_cents)/100.0 as bookings,sum(invoiced_cents)/100.0 as invoiced,sum(cash_cents)/100.0 as cash
 from {{ ref('mart_flows') }} where event_date between cast('{{ var("period_start") }}' as date) and cast('{{ var("period_end") }}' as date) group by account_id
)
select t.*,a.account_name,bookings-invoiced as bookings_less_invoices,invoiced-cash as invoices_less_cash,
 case when bookings>invoiced then 'Contract value not yet invoiced in this period'
 when bookings<invoiced then 'Net invoicing exceeds amended bookings in this period'
 else 'Bookings and invoicing align for this period' end as explanation
from totals t join (select account_id,max(account_name) as account_name from {{ ref('dim_account') }} group by account_id) a
on t.account_id=a.account_id
