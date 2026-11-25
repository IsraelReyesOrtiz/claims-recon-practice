-- Grain: 1 fila por claim_id (dedup del batch re-ejecutado: gana la carga más reciente)
with src as (
    select * from {{ source('raw', 'sf_claims_raw') }}
),
cleaned as (
    select
        claim_id,
        provider_id,
        payer_id,
        cast(date_of_service as date)              as date_of_service,
        cast(submitted_ts_utc as timestamp)        as submitted_ts_utc,
        trim(cast(cpt_code as varchar))            as cpt_code,
        upper(network_status)                      as network_status,
        claim_status,
        cast(billed_amount as numeric(18, 2))      as billed_amount,
        cast(qpa_amount as numeric(18, 2))         as qpa_amount,
        cast(_loaded_at as timestamp)              as loaded_at,
        row_number() over (partition by claim_id order by _loaded_at desc) as rn
    from src
)
select * exclude (rn) from cleaned where rn = 1
