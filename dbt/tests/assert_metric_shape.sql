select metric_id,region from {{ ref('mart_metric_values') }} group by metric_id,region having count(*)<>1
union all
select metric_id,region from {{ ref('mart_metric_values') }}
where (metric_id='cohort_win_rate' and (value<0 or value>1 or (denominator=0 and value is not null)))
 or (metric_id<>'cohort_win_rate' and value is null)
