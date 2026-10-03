with invoices as (
 select invoice_id,account_id,sum(amount_cents) as net_cents,
 min(event_date) as issued_on,count(distinct contract_id) as contracts
 from {{ ref('fct_invoice_lines') }} group by invoice_id,account_id
), payments as (
 select invoice_id,account_id,sum(amount_cents) as paid_cents,min(event_date) as paid_on
 from {{ ref('fct_payment_allocations') }} group by invoice_id,account_id
)
select i.invoice_id from invoices i left join payments p
on i.invoice_id=p.invoice_id and i.account_id=p.account_id
where i.net_cents<0 or i.contracts<>1 or p.paid_cents>i.net_cents or p.paid_on<i.issued_on
