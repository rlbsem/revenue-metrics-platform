{% macro add_days(column, days) -%}
  {% if target.type == 'snowflake' %}dateadd(day, {{ days }}, {{ column }})
  {% else %}cast({{ column }} + interval '{{ days }} days' as date){% endif %}
{%- endmacro %}
