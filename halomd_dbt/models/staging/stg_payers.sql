-- Grain: 1 fila por payer
select
    payer_id,
    payer_name,
    cast(prompt_pay_days as integer) as prompt_pay_days
from {{ source('raw', 'payers') }}
