-- Source supplies complete effective-time intervals; do not infer historical owners from today.
select * from {{ source('raw','account_history') }}
