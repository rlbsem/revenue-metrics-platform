select * from {{ source('raw','opportunity_events') }}
qualify row_number() over (partition by event_id order by ingest_seq desc) = 1
