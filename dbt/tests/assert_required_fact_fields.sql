select booking_event_id as record_id from {{ ref('fct_booking_events') }}
where account_id is null or event_date is null or amount_cents is null or contract_id is null
union all
select line_id from {{ ref('fct_invoice_lines') }}
where account_id is null or event_date is null or amount_cents is null or invoice_id is null
union all
select allocation_id from {{ ref('fct_payment_allocations') }}
where account_id is null or event_date is null or amount_cents is null or invoice_id is null
union all
select contract_id from {{ ref('stg_subscriptions') }}
where account_id is null or start_date is null or end_date is null or fee_cents is null
 or billing_months is null or contract_type is null or is_trial is null or past_due is null
union all
select event_id from {{ ref('stg_opportunities') }}
where account_id is null or created_date is null or effective_date is null or stage is null
 or opportunity_id is null or amount_cents is null or ingest_seq is null
union all
select account_version_id from {{ ref('dim_account') }}
where account_id is null or region is null or owner is null or valid_from is null or valid_to is null
