select *, case when contract_type = 'recurring' and is_trial = 0
  then cast(fee_cents * 12.0 / nullif(billing_months,0) as decimal(18,2)) else 0 end as annual_cents
from {{ source('raw','subscription_contracts') }}
