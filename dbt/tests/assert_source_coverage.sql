with required as (
 select 'account_history' as source_name union all select 'opportunity_events' union all select 'booking_events'
 union all select 'invoice_lines' union all select 'payment_allocations' union all select 'subscription_contracts'
)
select r.source_name from required r left join {{ source('raw','coverage') }} c on r.source_name=c.source_name
group by r.source_name having count(c.source_name)<>1 or count(c.complete_from)<>1 or count(c.complete_through)<>1
 or min(c.complete_from)>cast('{{ var("period_start") }}' as date)
 or max(c.complete_through)<cast('{{ var("period_end") }}' as date)
