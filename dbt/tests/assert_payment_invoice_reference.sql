select p.allocation_id from {{ ref('fct_payment_allocations') }} p
left join (select distinct invoice_id,account_id from {{ ref('fct_invoice_lines') }}) i
on p.invoice_id=i.invoice_id and p.account_id=i.account_id
where i.invoice_id is null or p.amount_cents<0
