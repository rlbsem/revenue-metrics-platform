{% macro clear_changed_opportunities() %}
  {% if is_incremental() %}
    delete from {{ this }} where opportunity_id in (
      select opportunity_id from {{ ref('stg_opportunities') }}
      where ingest_seq > (select coalesce(max(last_ingest_seq),0) from {{ this }})
    )
  {% else %}select 1{% endif %}
{% endmacro %}
