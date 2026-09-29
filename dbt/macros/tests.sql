{# Every OpenAQ hour starts at :30 UTC (hours are IST-aligned). Returns offending rows. #}
{% test ist_aligned_hour(model, column_name) %}
    select {{ column_name }}
    from {{ model }}
    where minute({{ column_name }}) <> 30 or second({{ column_name }}) <> 0
{% endtest %}

{# FIRMS: after SP-over-NRT preference, no (day, sensor) may still have both products. #}
{% test single_firms_product_per_day(model) %}
    select acq_date, sensor
    from {{ model }}
    group by acq_date, sensor
    having count(distinct product) > 1
{% endtest %}
