with source_totals as (
 select sum(amount_cents) as b from {{ ref('fct_booking_events') }}
), published as (select sum(bookings_cents) as b,sum(invoiced_cents) as i,sum(cash_cents) as c from {{ ref('mart_flows') }})
select 'flow mismatch' as problem from published,source_totals
where published.b<>source_totals.b
 or published.i<>(select sum(amount_cents) from {{ ref('fct_invoice_lines') }})
 or published.c<>(select sum(amount_cents) from {{ ref('fct_payment_allocations') }})
