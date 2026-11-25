# HaloMD — Plan de práctica (vie 20 → jue 26 nov 2026)

**Horas:** vie 2 h · sáb 4 h · dom 4 h · lun 2 h · mar 2 h · mié 2 h · jue 2 h = **18 h**

## 0. Kit (ya generado)

```
halomd_practice_v2/
├─ generate_dataset_v2.py      # genera ./data/*.csv + _answer_key.json
├─ halomd_dbt/                 # proyecto dbt (dbt-duckdb local; Snowflake comentado en profiles.yml)
├─ .github/workflows/dbt_ci.yml
├─ azure-pipelines.yml
└─ solutions/recon.py          # solución de referencia del día 3 — no la abras antes
```

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install pandas numpy requests dbt-duckdb pytest
python generate_dataset_v2.py --claims 30000 --seed 7
```

| Tabla | Filas | Grano | Qué trae a propósito |
|---|---|---|---|
| `source_claims` | 30,000 | 1 por claim (system of record) | ICD-10 malformado en ~0.7% |
| `sf_claims_raw` | 30,433 | 1 por claim **por carga** | filas rechazadas, 6 batches duplicados, llegadas tardías, espacios/minúsculas |
| `portal_claims` | 29,127 | 1 por fila mostrada | sin dedup, excluye VOID, fecha en hora de Chicago, corte 31-oct |
| `remittances` | 24,597 | 1 por línea 835 | parciales, negaciones, reversos negativos, huérfanos |
| `bank_deposits` | 2,235 | 1 por EFT | 4% no cuadra, 1.5% falta |
| `idr_disputes` | 2,975 | 1 por disputa | algunas iniciadas fuera de plazo |
| `payers` | 8 | 1 por payer | `prompt_pay_days` vs. comportamiento real |

> ⚠️ `_answer_key.json` contiene las causas raíz plantadas. No lo abras hasta el miércoles 25.

---

## Día 1 — Vie 20 (2 h) · Dominio de salud

**Objetivo:** explicar en 2 minutos, sin notas, el viaje de un claim desde que se envía hasta la disputa.

### Flujo

```
① Servicio (DOS) → ② Claim 837 al payer → ③ Adjudicación del payer
→ ④ Remesa 835/ERA (pago, ajuste o negación + códigos CARC) → ⑤ Depósito EFT en el banco
→ ⑥ Si es OON y se pagó poco: Open negotiation (30 días hábiles)
→ ⑦ IDR (iniciar ≤ 4 días hábiles) → ⑧ Determinación → ⑨ Pago del award (~30 días naturales)
```

### Glosario en lenguaje sencillo

| Término | Qué es | Columna |
|---|---|---|
| Claim | La "factura" del proveedor a la aseguradora | `claim_id` |
| Provider / Payer | Quien atiende / quien paga | `provider_id`, `payer_id` |
| DOS | Fecha en que se atendió al paciente | `date_of_service` |
| CPT / ICD-10 | Qué se hizo / por qué (diagnóstico) | `cpt_code`, `icd10_primary` |
| INN / OON | Con contrato / sin contrato con la aseguradora | `network_status` |
| Adjudicación | La aseguradora revisa el claim y decide cuánto paga | — |
| Billed / Allowed / Paid | Lo que se cobró / lo que el payer reconoce / lo que realmente depositó | `*_amount` |
| Patient responsibility | La parte del paciente (deducible, copago) | `patient_resp` |
| 837 / 835 (ERA) | El archivo de envío / el archivo de respuesta con el pago | — |
| CARC | Código del motivo de un ajuste o negación (CO-45 = supera la tarifa; CO-197 = falta autorización) | `carc_code` |
| Denial | Negación: pago $0 con un motivo | `line_type='DENIAL'` |
| Partial payment | El pago llega en varias partes | `PAYMENT_2ND` |
| Reversal / takeback | El payer se retracta de un pago (monto negativo) | `REVERSAL` |
| EFT | Una transferencia bancaria que agrupa muchos claims | `eft_number` |
| Prompt pay | Plazo en que el payer debería pagar | `prompt_pay_days` |
| QPA | La tarifa de referencia del payer (≈ mediana in-network) | `qpa_amount` |
| IDR | Arbitraje federal de la No Surprises Act; el árbitro elige la oferta del proveedor o la del payer | `idr_disputes` |
| VOID | Claim anulado: existe en la fuente, pero no cuenta para el negocio | `claim_status` |
| PHI | Datos de salud que identifican a una persona. Nunca uses datos reales | — |

**Ejercicio**
1. Genera el dataset y abre `source_claims`, `remittances` y `portal_claims`.
2. Sigue 3 claims de punta a punta: uno PAID sencillo, uno con `PAYMENT_2ND` y uno con `REVERSAL`. Anota billed → allowed → patient_resp → net_paid.
3. Dibuja el flujo (papel o Whiteboard) y escribe junto a cada paso qué tabla lo representa.

```python
import pandas as pd
r = pd.read_csv("data/remittances.csv")
ids = r.loc[r.line_type.isin(["PAYMENT_2ND", "REVERSAL"]), "claim_id"].head(2).tolist()
print(r[r.claim_id.isin(ids)].sort_values(["claim_id", "payment_date"]).to_string())
```

**✅ Terminado cuando:** puedes explicar en voz alta por qué `net_paid ≠ allowed_amount` en esos 3 claims y en qué paso nace una disputa.

---

## Día 2 — Sáb 21 (4 h) · SQL de conciliación en Snowflake

**Objetivo:** dominar duplicados, reversos y pagos tardíos, y declarar el **grain** de cada resultado.

**Regla de oro del grain:** antes de cada join, escribe "1 fila = ___". Si los dos lados no tienen el mismo grain, primero agrega y después unes.

### 2.1 Grain y duplicados (45 min)

```sql
-- ¿Cuántas filas hay por claim? Si el máximo es > 1, no es grain de claim
SELECT COUNT(*) AS filas, COUNT(DISTINCT claim_id) AS claims,
       COUNT(*) - COUNT(DISTINCT claim_id) AS filas_extra
FROM raw.sf_claims_raw;

-- ¿Qué batches generaron los duplicados?
SELECT _batch_id, COUNT(*) AS filas, COUNT(DISTINCT claim_id) AS claims
FROM raw.sf_claims_raw
GROUP BY 1 HAVING COUNT(*) > COUNT(DISTINCT claim_id)
ORDER BY 1;

-- Dedup: gana la carga más reciente
SELECT *
FROM raw.sf_claims_raw
QUALIFY ROW_NUMBER() OVER (PARTITION BY claim_id ORDER BY _loaded_at DESC) = 1;
```

### 2.2 Reversos y parciales: saldo neto por claim (1 h)

```sql
WITH pay AS (                                   -- grain: 1 fila por claim
  SELECT claim_id,
         SUM(paid_amount)                                    AS net_paid,
         SUM(IFF(line_type = 'REVERSAL', paid_amount, 0))    AS reversed,
         COUNT_IF(line_type = 'PAYMENT_2ND')                 AS partial_lines,
         MIN(IFF(line_type = 'PAYMENT', payment_date, NULL)) AS first_payment_date
  FROM raw.remittances
  GROUP BY claim_id
)
SELECT c.claim_id, c.billed_amount, p.net_paid, p.reversed, p.partial_lines,
       CASE WHEN p.claim_id IS NULL   THEN 'NO_REMIT'
            WHEN p.net_paid <= 0      THEN 'DENIED_OR_REVERSED'
            WHEN p.partial_lines > 0  THEN 'PARTIAL'
            ELSE 'PAID' END AS payment_state
FROM claims_dedup c
LEFT JOIN pay p ON p.claim_id = c.claim_id;
```

**Trampa:** si unes claims con remesas **antes** de agregar, `billed_amount` se duplica por cada línea de remesa.
Ejercicio: hazlo mal a propósito, mide cuánto se infla `SUM(billed_amount)` y después corrígelo.

### 2.3 Pagos tardíos (45 min)

```sql
SELECT c.payer_id, py.prompt_pay_days,
       COUNT(*)                                                         AS paid_claims,
       AVG(DATEDIFF('day', c.submitted_ts_utc::date, p.first_payment_date)) AS avg_days,
       AVG(IFF(DATEDIFF('day', c.submitted_ts_utc::date, p.first_payment_date)
               > py.prompt_pay_days, 1, 0)) * 100                        AS pct_late
FROM claims_dedup c
JOIN pay p ON p.claim_id = c.claim_id
JOIN raw.payers py ON py.payer_id = c.payer_id
WHERE p.first_payment_date IS NOT NULL
GROUP BY 1, 2
ORDER BY pct_late DESC;
```

### 2.4 Remesa ↔ banco y huérfanos (1 h)

```sql
WITH expected AS (
  SELECT eft_number, payer_id, SUM(paid_amount) AS expected_amt
  FROM raw.remittances GROUP BY 1, 2)
SELECT COALESCE(e.eft_number, b.eft_number) AS eft_number,
       e.expected_amt, b.deposit_amount,
       ROUND(b.deposit_amount - e.expected_amt, 2) AS variance,
       CASE WHEN b.eft_number IS NULL THEN 'MISSING_DEPOSIT'
            WHEN e.eft_number IS NULL THEN 'UNEXPECTED_DEPOSIT'
            WHEN ABS(b.deposit_amount - e.expected_amt) <= 0.01 THEN 'MATCHED'
            ELSE 'VARIANCE' END AS recon_status
FROM expected e
FULL OUTER JOIN raw.bank_deposits b
  ON b.eft_number = e.eft_number AND b.payer_id = e.payer_id;

-- Huérfanos: pagos sin claim. Pregunta: ¿cuántos vienen de claims que Snowflake rechazó?
SELECT r.*
FROM raw.remittances r
LEFT JOIN claims_dedup c ON c.claim_id = r.claim_id
WHERE c.claim_id IS NULL;
```

### 2.5 (30 min) Window functions extra
- `LAG(payment_date) OVER (PARTITION BY claim_id ORDER BY payment_date)` → días entre el pago y su reverso.
- `SUM(net_paid) OVER (PARTITION BY payer_id ORDER BY month)` → cobranza acumulada.

**✅ Terminado cuando:** tienes 5 queries guardadas y cada una lleva un comentario `-- grain: …`; sabes cuántas filas duplicadas hay, cuántos reversos, el % de pagos tardíos por payer y cuántos EFT tienen VARIANCE.

---

## Día 3 — Dom 22 (4 h) · SQL Server + execution plans (2 h) · Python/pandas + API (2 h)

### 3A. SQL Server (2 h)

**Setup sin instalar SQL Server en Windows (Docker):**
```bash
docker run -e "ACCEPT_EULA=Y" -e "MSSQL_SA_PASSWORD=Str0ng!Passw0rd" -p 1433:1433 -d mcr.microsoft.com/mssql/server:2022-latest
```
Importa `sf_claims_raw.csv` con el Import Flat File de SSMS o Azure Data Studio, o con `BULK INSERT`.

**Dedup (SQL Server no tiene QUALIFY):**
```sql
WITH ranked AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY claim_id ORDER BY _loaded_at DESC) AS rn
  FROM dbo.sf_claims_raw)
SELECT * INTO dbo.claims_dedup FROM ranked WHERE rn = 1;
```

**Execution plans (lo que te van a preguntar):**
```sql
SET STATISTICS IO, TIME ON;     -- y activa "Include Actual Execution Plan" (Ctrl+M)

-- ❌ No SARGable: la función sobre la columna impide usar el índice
SELECT COUNT(*) FROM dbo.sf_claims_raw WHERE YEAR(submitted_ts_utc) = 2026 AND MONTH(submitted_ts_utc) = 10;

-- ✅ SARGable: rango sobre la columna "desnuda"
SELECT COUNT(*) FROM dbo.sf_claims_raw
WHERE submitted_ts_utc >= '2026-10-01' AND submitted_ts_utc < '2026-11-01';

CREATE INDEX ix_claims_submitted ON dbo.sf_claims_raw (submitted_ts_utc);
CREATE INDEX ix_claims_dedup ON dbo.sf_claims_raw (claim_id, _loaded_at DESC)
  INCLUDE (claim_status, billed_amount);   -- evita el Key Lookup y el Sort del ROW_NUMBER
```

| Qué ves en el plan | Qué significa | Arreglo típico |
|---|---|---|
| Table/Clustered Index **Scan** | Lee toda la tabla | Índice + predicado SARGable |
| **Index Seek** | Va directo a las filas | ✅ |
| **Key Lookup** | Encontró la fila en el índice, pero tiene que volver a la tabla por columnas que faltan | `INCLUDE (...)` |
| **Sort** caro | Ordena en memoria (ROW_NUMBER, ORDER BY) | Índice con ese mismo orden |
| Estimated ≠ Actual rows | Estadísticas viejas | `UPDATE STATISTICS` |
| Hash Match en un join grande | Normal en volumen; vigila los spills a tempdb | Memoria / filtrar antes |

**Equivalente en Snowflake:** no hay índices. Revisa el **Query Profile**: *Partitions scanned vs total* (pruning), *Bytes spilled* y *Exploding joins*. Las palancas son filtrar por columnas de fecha, clustering keys (solo en tablas grandes) y agregar antes del join.

**Zona horaria (causa del día 6):**
```sql
-- Snowflake
SELECT DATE_TRUNC('month', CONVERT_TIMEZONE('UTC', 'America/Chicago', submitted_ts_utc)) AS mes_local, COUNT(*) ...
-- SQL Server
SELECT EOMONTH((submitted_ts_utc AT TIME ZONE 'UTC') AT TIME ZONE 'Central Standard Time') AS mes_local, COUNT(*) ...
```

### 3B. Python/pandas + API (2 h)

**Ejercicio:** escribe `my_recon.py` que compare `source_claims` → `sf_claims_raw` → `portal_claims` y clasifique cada claim en MATCHED, NOT_LOADED, LATE_ARRIVING, VOID_EXCLUDED o UNEXPLAINED.

Esqueleto:
```python
import pandas as pd
CUTOFF = pd.Timestamp("2026-10-31 23:59:59")
src = pd.read_csv("data/source_claims.csv", parse_dates=["submitted_ts_utc"])
sf  = pd.read_csv("data/sf_claims_raw.csv", parse_dates=["submitted_ts_utc", "_loaded_at"])
portal = pd.read_csv("data/portal_claims.csv")

# 1) Alinea la "foto": mismo corte en ambos lados
src = src[src.submitted_ts_utc <= CUTOFF]
sf  = sf[sf.submitted_ts_utc <= CUTOFF]
# 2) Dedup en Snowflake (gana la carga más reciente)
sf_d = sf.sort_values("_loaded_at").drop_duplicates("claim_id", keep="last")
# 3) Outer merge con indicator=True y clasifica  ← tu turno
```

**Tests con pytest** (`test_recon.py`): crea 4 filas a mano (una por causa) y verifica que tu función las clasifica bien.

**API: enriquece con el NPI Registry de CMS (datos públicos, no PHI):**
```python
import time, requests, pandas as pd

URL = "https://npiregistry.cms.hhs.gov/api/"

def get_with_retry(params, retries=4):
    for i in range(retries):
        r = requests.get(URL, params=params, timeout=30)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503):
            time.sleep(2 ** i)            # backoff exponencial: 1, 2, 4, 8 s
            continue
        r.raise_for_status()
    raise RuntimeError("API no respondió")

def fetch_providers(state="TX", taxonomy="Emergency Medicine", page=200, max_rows=600):
    rows, skip = [], 0
    while skip < max_rows:
        data = get_with_retry({"version": "2.1", "state": state, "taxonomy_description": taxonomy,
                               "limit": page, "skip": skip})
        batch = data.get("results", [])
        rows += batch
        if len(batch) < page:
            break
        skip += page
    return pd.json_normalize(rows)[["number", "basic.organization_name", "basic.status"]]

print(fetch_providers().head())
```
> ⚠️ Valida que la API responda y revisa sus límites de uso. Los NPIs del dataset son sintéticos, así que no van a coincidir con los reales. El ejercicio es practicar paginación, reintentos y aplanar el JSON.

**✅ Terminado cuando:** (a) ves Seek en lugar de Scan y puedes explicar el Key Lookup; (b) `my_recon.py` da un match rate y un desglose por causa; (c) `pytest` sale en verde; (d) la API te devuelve un DataFrame.

---

## Día 4 — Lun 23 (2 h) · dbt: sources, staging, tests, docs

**Objetivo:** que `dbt build` pase con tests y documentación generada.

El proyecto `halomd_dbt/` ya trae:

| Archivo | Qué hace |
|---|---|
| `models/staging/_sources.yml` | Declara las fuentes RAW con su descripción y tests |
| `stg_claims.sql` | Limpia (TRIM, UPPER) y deduplica → grain 1 por claim |
| `stg_remittances.sql` | Tipifica las remesas |
| `marts/fct_claim_payment.sql` | Saldo neto por claim (con parciales y reversos) |
| `tests/assert_no_orphan_remittances.sql` | Test singular con `severity: warn` |

```bash
cd halomd_dbt
dbt build --profiles-dir .
dbt docs generate --profiles-dir . && dbt docs serve --profiles-dir .
```

Resultado esperado hoy: **PASS=18 WARN=1**. El warn son 193 pagos huérfanos (con seed 7).

**Tu ejercicio (agrega):**
1. `stg_payers` + la columna `is_late_payment` en `fct_claim_payment` (`days_to_first_payment > prompt_pay_days`).
2. El test `relationships` de `stg_claims.payer_id` → `stg_payers`.
3. Un test singular `assert_net_paid_not_above_allowed.sql`.
4. `description` en cada columna de `fct_claim_payment` (en inglés).
5. Investiga el warn: ¿cuántos de los 193 huérfanos son claims rechazados por la carga y cuántos son huérfanos reales?

```sql
-- tests/assert_net_paid_not_above_allowed.sql
select claim_id
from {{ ref('fct_claim_payment') }} f
join (select claim_id, sum(allowed_amount) as allowed
      from {{ ref('stg_remittances') }} where line_type = 'PAYMENT' group by 1) a using (claim_id)
where f.net_paid > a.allowed + 0.01
```

**✅ Terminado cuando:** `dbt build` pasa sin errores, `dbt docs` muestra el lineage source → stg → fct, y puedes explicar los 193 huérfanos (161 por claims rechazados en la carga + 32 huérfanos reales).

---

## Día 5 — Mar 24 (2 h) · CI/CD: dbt build en cada pull request

**En sencillo:** CI es una máquina que, cada vez que abres un PR, instala todo, genera los datos y corre `dbt build`. Si un test falla, el PR queda en rojo y no se puede hacer merge.

**Ruta recomendada: GitHub Actions + DuckDB** (gratis, sin secretos). El archivo ya existe: `.github/workflows/dbt_ci.yml`.

```bash
git init && git add . && git commit -m "init halomd practice"
gh repo create halomd-practice --private --source=. --push
git checkout -b feature/late-payment-flag
# haz el cambio del día 4, commit, push
gh pr create --fill
```

**Ejercicio:**
1. Abre un PR que pase (check verde ✅).
2. Rompe algo a propósito, por ejemplo agrega `'PENDING'` a los `accepted_values` o quita la dedup en `stg_claims`, y confirma que el check falla ❌.
3. Protege `main`: Settings → Branches → *Require status checks to pass*.
4. Lee `azure-pipelines.yml` (la versión para Snowflake) y explica las 3 diferencias: el Variable Group con secretos, `env:` para mapear el secreto y un schema por PR (`pr_<id>`).

| Concepto | GitHub Actions | Azure DevOps |
|---|---|---|
| Disparador de PR | `on: pull_request` | `pr: branches` |
| Secretos | Settings → Secrets | Library → Variable groups |
| Runner | `runs-on: ubuntu-latest` | `pool: vmImage` |
| Artifacts | `upload-artifact` | `PublishPipelineArtifact` |

**Siguiente nivel (solo léelo):** slim CI con `dbt build --select state:modified+ --defer --state prod-artifacts`. Solo construye lo que cambió y usa producción para todo lo demás.

**✅ Terminado cuando:** tienes un PR verde y uno rojo por un test, y la rama `main` está protegida.

---

## Día 6 — Mié 25 (2 h) · Escenario de entrevista: portal vs. fuente

**Contexto:** el cliente dice: *"El portal muestra un número de claims distinto al de nuestro sistema."*

**Paso 1 — Confirmar y cuantificar (20 min)**
```sql
SELECT 'source' AS capa, COUNT(DISTINCT claim_id) AS claims FROM raw.source_claims WHERE submitted_ts_utc <= '2026-10-31 23:59:59'
UNION ALL SELECT 'snowflake_rows',     COUNT(*)                 FROM raw.sf_claims_raw WHERE submitted_ts_utc <= '2026-10-31 23:59:59'
UNION ALL SELECT 'snowflake_distinct', COUNT(DISTINCT claim_id) FROM raw.sf_claims_raw WHERE submitted_ts_utc <= '2026-10-31 23:59:59'
UNION ALL SELECT 'portal_rows',        COUNT(*)                 FROM raw.portal_claims
UNION ALL SELECT 'portal_distinct',    COUNT(DISTINCT claim_id) FROM raw.portal_claims;
```

**Paso 2 — Ubicar dónde empieza la discrepancia, capa por capa (40 min)**

| Salto | Pregunta | Causa candidata |
|---|---|---|
| Fuente → Snowflake | ¿Qué claims no llegaron? | Rechazo del COPY INTO (ICD-10 malformado) |
| Snowflake: filas vs. distinct | ¿Hay filas extra? | Re-ejecución de batches |
| Snowflake → Portal | ¿Qué falta en el portal? | Corte del snapshot (late-arriving), definición (VOID) |
| Por mes | ¿Los totales cuadran pero los meses no? | Zona horaria (UTC vs. Chicago) |

```sql
-- Por mes: UTC vs. hora local, para detectar el corrimiento de zona horaria
SELECT DATE_TRUNC('month', submitted_ts_utc) AS mes_utc,
       DATE_TRUNC('month', CONVERT_TIMEZONE('UTC','America/Chicago', submitted_ts_utc)) AS mes_local,
       COUNT(*)
FROM claims_dedup GROUP BY 1, 2 HAVING mes_utc <> mes_local;
```

**Paso 3 — Compara con `_answer_key.json`.** ¿Encontraste las 5 causas (A–E)? ¿Te sobra algo "UNEXPLAINED"?

**Paso 4 — Métricas para liderazgo (30 min)**

| Métrica | Fórmula | Para qué sirve |
|---|---|---|
| Claims afectados | # claims con razón ≠ MATCHED y ≠ definición | Tamaño del problema |
| % impactado | afectados / total fuente | Severidad |
| Match rate | MATCHED / total | Avance: [80%] → [95%] → [99.9%] |
| Over-count del portal | filas portal − claims distintos | Impacto visible para el cliente |
| $ en riesgo | SUM(billed) de los afectados | Lenguaje de negocio |
| Backlog de defectos | # causas abiertas por estado | Seguimiento |
| DQ pass rate | tests dbt PASS / total | Prevención |
| ETA y riesgo | fecha de fix por causa | Expectativas |

**Paso 5 — Plan de remediación:** una fila por causa con *owner* (Engineering / Ops / Data), fix en origen, control preventivo (test dbt o alerta) y fecha `[ ]`.

**✅ Terminado cuando:** tienes una tabla causa → # claims → % → fix → control, y un match rate "antes vs. después" simulado (aplica los fixes en tu query y vuelve a medir).

---

## Día 7 — Jue 26 (2 h) · Inglés escrito

> 🇺🇸 El 26 de noviembre es **Thanksgiving** en EE. UU. Tenlo presente si tu inicio con HaloMD cae esa semana: probablemente el equipo no esté disponible ese día. Confírmalo con Howdy.

**Objetivo:** 3 definiciones de métricas y 1 nota de hallazgos, listas para Confluence o Slack. Llena los `[ ]` con TUS resultados.

### Plantilla de definición de métrica

**1. Claim Match Rate**
> **EN** — *Definition:* Percentage of source-system claims, as of the reporting cutoff, that appear exactly once in the client portal. *Formula:* `matched_claims / source_claims_in_scope`. *Grain:* claim. *Excludes:* claims with status VOID (excluded by business definition). *Owner:* Data & Engineering. *Refresh:* daily. *Target:* ≥ [99.9]%.
>
> **ES** — Porcentaje de claims del sistema fuente, al corte del reporte, que aparecen exactamente una vez en el portal del cliente. Fórmula: claims conciliados / claims de la fuente en alcance. Grain: claim. Excluye los claims VOID (por definición de negocio). Responsable: Data & Engineering. Actualización: diaria. Meta: ≥ [99.9]%.

**2. Net Paid Amount**
> **EN** — *Definition:* Total amount actually paid by the payer for a claim, including partial payments and net of reversals (takebacks). *Formula:* `SUM(paid_amount)` across all 835 remittance lines for the claim. *Grain:* claim. *Note:* Reversals carry negative amounts; do not filter them out.
>
> **ES** — Monto total que el payer realmente pagó por un claim, incluyendo pagos parciales y restando reversos. Fórmula: suma de `paid_amount` en todas las líneas 835 del claim. Grain: claim. Nota: los reversos vienen en negativo; no los filtres.

**3. Late Payment Rate**
> **EN** — *Definition:* Share of paid claims whose first payment arrived after the payer's prompt-pay window. *Formula:* `claims where days_to_first_payment > prompt_pay_days / paid claims`. *Grain:* claim, reported by payer and month. *Caveat:* prompt-pay windows vary by state and plan type; values here are [assumed / per contract].
>
> **ES** — Proporción de claims pagados cuyo primer pago llegó después del plazo de prompt pay del payer. Fórmula: claims con días al primer pago > plazo / claims pagados. Grain: claim, reportado por payer y mes. Advertencia: los plazos varían por estado y tipo de plan; aquí son [supuestos / según contrato].

### Nota de hallazgos (máx. 200 palabras)

> **EN — Subject: Portal vs. source claim count discrepancy — findings and next steps**
>
> **Summary:** As of the [Oct 31] cutoff, the portal shows [ ] rows versus [ ] claims in the source system. [ ] claims ([ ]%) are affected. The current match rate is [ ]%.
>
> **Root causes:**
> 1. [ ] claims were rejected during the Snowflake load because of malformed ICD-10 codes.
> 2. [ ] batches were loaded twice, inflating the portal by [ ] rows.
> 3. [ ] claims arrived after the portal snapshot (late-arriving data).
> 4. Monthly counts shift because the portal uses Central Time while the source uses UTC ([ ] claims).
> 5. [ ] VOID claims are excluded by definition (not a defect).
>
> **Next steps:** dedup in staging plus a uniqueness test ([date]); a rejected-row alert on load ([date]); align the timezone definition with the client ([date]).
>
> **Risk:** [ ]. **Ask:** confirm whether VOID claims should be shown.

> **ES — Asunto: Discrepancia de conteo de claims entre portal y fuente: hallazgos y siguientes pasos**
> Resumen: al corte del [31 oct], el portal muestra [ ] filas contra [ ] claims en la fuente. Hay [ ] claims ([ ]%) afectados y el match rate actual es [ ]%. Causas: (1) [ ] claims rechazados en la carga a Snowflake por ICD-10 malformado; (2) [ ] batches cargados dos veces, que inflan el portal en [ ] filas; (3) [ ] claims que llegaron después del corte; (4) corrimiento mensual por zona horaria, Central vs. UTC ([ ] claims); (5) [ ] claims VOID excluidos por definición, que no son un defecto. Siguientes pasos: dedup en staging + test de unicidad, alerta de filas rechazadas, alinear la definición de zona horaria. Riesgo: [ ]. Pregunta al cliente: ¿deben mostrarse los VOID?

**Ejercicio:** llena los `[ ]` con tus números del día 6. Después pide a Claude que revise la gramática y el tono, **pero valida tú cada cifra contra tu query**.

**✅ Terminado cuando:** las 3 definiciones y la nota están completas, sin `[ ]`, y cada número se puede rastrear a una query.

---

## Checklist final

- [ ] Explico el flujo claim → payer → 835 → pago/ajuste → disputa en 2 minutos
- [ ] Dedup, reversos, parciales, pagos tardíos y conciliación EFT en Snowflake
- [ ] Mismas queries en SQL Server + plan de ejecución explicado (Scan/Seek/Key Lookup)
- [ ] `my_recon.py` + pytest + llamada a la API con reintentos
- [ ] `dbt build` verde con tests y docs
- [ ] PR verde y PR rojo en CI
- [ ] Escenario portal vs. fuente con las 5 causas y las métricas para liderazgo
- [ ] 3 definiciones + nota de hallazgos en inglés
