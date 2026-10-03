select contract_id from {{ ref('stg_subscriptions') }}
where billing_months<=0 or fee_cents<0 or start_date>=end_date or is_trial not in (0,1) or past_due not in (0,1)
