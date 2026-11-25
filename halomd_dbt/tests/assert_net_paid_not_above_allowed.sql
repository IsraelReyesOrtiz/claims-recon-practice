-- Regla de negocio: el payer nunca debe pagar más de lo que reconoció como allowed.
-- Falla (devuelve filas) si net_paid > allowed_amount + 1 centavo.
select claim_id, allowed_amount, net_paid
from {{ ref('fct_claim_payment') }}
where net_paid > allowed_amount + 0.01
