-- Pagos sin claim. Warn: huérfanos plantados + pagos de claims que el COPY INTO rechazó (investígalo)
{{ config(severity='warn') }}
select r.remit_line_id, r.claim_id
from {{ ref('stg_remittances') }} r
left join {{ ref('stg_claims') }} c using (claim_id)
where c.claim_id is null
