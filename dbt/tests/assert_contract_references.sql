-- Validate each financial source at its own grain; never join financial facts together.
select b.booking_event_id as record_id from {{ ref('fct_booking_events') }} b
left join {{ ref('stg_subscriptions') }} c on b.contract_id=c.contract_id and b.account_id=c.account_id
where c.contract_id is null
union all
select i.line_id from {{ ref('fct_invoice_lines') }} i
left join {{ ref('stg_subscriptions') }} c on i.contract_id=c.contract_id and i.account_id=c.account_id
where c.contract_id is null
