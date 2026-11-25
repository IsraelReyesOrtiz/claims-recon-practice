-- Grain: 1 fila por línea de remesa
select
    remit_line_id,
    claim_id,
    payer_id,
    cast(payment_date as date)              as payment_date,
    cast(allowed_amount as numeric(18, 2))  as allowed_amount,
    cast(patient_resp as numeric(18, 2))    as patient_resp,
    cast(paid_amount as numeric(18, 2))     as paid_amount,
    carc_code,
    line_type,
    eft_number
from {{ source('raw', 'remittances') }}
