-- Union and independently aggregate each grain. Never multiply invoices by payments.
with components as (
 select account_id,event_date,sum(amount_cents) as bookings_cents,0 as invoiced_cents,0 as cash_cents
 from {{ ref('fct_booking_events') }} group by account_id,event_date
 union all
 select account_id,event_date,0,sum(amount_cents),0 from {{ ref('fct_invoice_lines') }} group by account_id,event_date
 union all
 select account_id,event_date,0,0,sum(amount_cents) from {{ ref('fct_payment_allocations') }} group by account_id,event_date
)
select c.account_id,c.event_date,a.region,a.owner,
 sum(bookings_cents) as bookings_cents,sum(invoiced_cents) as invoiced_cents,sum(cash_cents) as cash_cents
from components c join {{ ref('dim_account') }} a
 on c.account_id=a.account_id and c.event_date>=a.valid_from and c.event_date<a.valid_to
group by c.account_id,c.event_date,a.region,a.owner
