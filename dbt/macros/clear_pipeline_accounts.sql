{% macro clear_pipeline_accounts() %}
  {% if is_incremental() %}
    delete from {{ this }} where account_id in (
      select account_id from assurance_control.affected_accounts
    )
  {% else %}select 1{% endif %}
{% endmacro %}
