"""
Orquestación con Dagster: cada modelo dbt es un asset + conciliación en Python + check + schedule.
Uso (desde ~/halomd_practice):  dagster dev -f orchestration/definitions.py
"""
import os
import sys
from pathlib import Path

from dagster import (
    AssetCheckResult, AssetExecutionContext, AssetKey, AssetSelection, Definitions,
    MaterializeResult, MetadataValue, ScheduleDefinition, asset, asset_check, define_asset_job,
)
from dagster_dbt import DbtCliResource, DbtProject, dbt_assets

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "t6"))          # reutiliza recon.py del Tema 6
from recon import load, reconcile, summary  # noqa: E402

DBT_TARGET = os.getenv("DBT_TARGET", "ci")  # ci = DuckDB local; snowflake = tu cuenta
dbt_project = DbtProject(project_dir=ROOT / "halomd_dbt", profiles_dir=ROOT / "halomd_dbt", target=DBT_TARGET)
dbt_project.prepare_if_dev()                # genera manifest.json al arrancar `dagster dev`


# 1) Cada modelo/test de dbt aparece como asset con su linaje
@dbt_assets(manifest=dbt_project.manifest_path)
def halomd_dbt_assets(context: AssetExecutionContext, dbt: DbtCliResource):
    yield from dbt.cli(["build"], context=context).stream()


# 2) Conciliación fuente -> Snowflake -> portal (Python), río abajo de las fuentes raw
@asset(
    deps=[AssetKey(["raw", "sf_claims_raw"])],
    group_name="reconciliation",
    description="Source vs Snowflake vs portal reconciliation (grain: claim).",
)
def claims_recon(context: AssetExecutionContext) -> MaterializeResult:
    src, sf, portal = load(str(ROOT / "data"))
    df = reconcile(src, sf, portal)
    s, kpis = summary(df, portal)
    context.log.info(f"KPIs: {kpis}")
    return MaterializeResult(metadata={
        "match_rate_pct": MetadataValue.float(kpis["match_rate_pct"]),
        "portal_duplicate_rows": MetadataValue.int(kpis["portal_duplicate_rows"]),
        "by_reason": MetadataValue.md(s.reset_index().to_markdown(index=False)),
    })


# 3) Asset check: ¿el match rate cumple el umbral?
MATCH_RATE_THRESHOLD = float(os.getenv("MATCH_RATE_THRESHOLD", "95"))


@asset_check(asset=claims_recon, description="Match rate must be >= threshold")
def match_rate_above_threshold() -> AssetCheckResult:
    src, sf, portal = load(str(ROOT / "data"))
    _, kpis = summary(reconcile(src, sf, portal), portal)
    rate = kpis["match_rate_pct"]
    return AssetCheckResult(
        passed=rate >= MATCH_RATE_THRESHOLD,
        metadata={"match_rate_pct": rate, "threshold": MATCH_RATE_THRESHOLD},
    )


# 4) Job + schedule diario (6:30 a. m. hora del Centro)
daily_job = define_asset_job("daily_claims_pipeline", selection=AssetSelection.all())
daily_schedule = ScheduleDefinition(job=daily_job, cron_schedule="30 6 * * *",
                                    execution_timezone="America/Chicago")

defs = Definitions(
    assets=[halomd_dbt_assets, claims_recon],
    asset_checks=[match_rate_above_threshold],
    jobs=[daily_job],
    schedules=[daily_schedule],
    resources={"dbt": DbtCliResource(project_dir=dbt_project)},
)
