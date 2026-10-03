select event_id,ingest_seq from {{ source('raw','opportunity_events') }} group by event_id,ingest_seq having count(*)<>1
