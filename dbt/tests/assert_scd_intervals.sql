select a.account_version_id from {{ ref('dim_account') }} a
where a.valid_from>=a.valid_to
union all
select a.account_version_id from {{ ref('dim_account') }} a join {{ ref('dim_account') }} b
 on a.account_id=b.account_id and a.account_version_id<b.account_version_id
 and a.valid_from<b.valid_to and b.valid_from<a.valid_to
