select event_id from {{ ref('stg_opportunities') }}
where stage not in ('open','won','lost') or amount_cents<0 or effective_date<created_date
union all
select opportunity_id from {{ ref('stg_opportunities') }} group by opportunity_id
having count(distinct account_id)>1 or count(distinct created_date)>1
