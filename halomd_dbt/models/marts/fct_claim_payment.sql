-- Grain: 1 fila por claim. Saldo neto incluyendo parciales y reversos.
with pay as (
    select
        claim_id,
        sum(paid_amount)                                                    as net_paid,
        sum(case when line_type = 'REVERSAL' then paid_amount else 0 end)   as reversed_amount,
        sum(case when line_type = 'PAYMENT' then allowed_amount else 0 end) as allowed_amount,
        count(*)                                                            as remit_lines,
        min(case when line_type = 'PAYMENT' then payment_date end)          as first_payment_date
    from {{ ref('stg_remittances') }}
    group by claim_id
),
joined as (
    select
        c.claim_id,
        c.provider_id,
        c.payer_id,
        c.claim_status,
        cast(c.submitted_ts_utc as date) as submitted_date,
        c.billed_amount,
        c.qpa_amount,
        coalesce(p.allowed_amount, 0)  as allowed_amount,
        coalesce(p.net_paid, 0)        as net_paid,
        coalesce(p.reversed_amount, 0) as reversed_amount,
        p.first_payment_date,
        {{ dbt.datediff("cast(c.submitted_ts_utc as date)", "p.first_payment_date", "day") }} as days_to_first_payment,
        py.prompt_pay_days
    from {{ ref('stg_claims') }} c
    left join pay p                       on p.claim_id = c.claim_id
    left join {{ ref('stg_payers') }} py  on py.payer_id = c.payer_id
)
select
    *,
    case when first_payment_date is null then null
         when days_to_first_payment > prompt_pay_days then true
         else false end as is_late_payment
from joined
