"""
Conciliación fuente -> Snowflake -> portal con pandas.
Uso (desde la raíz): python solutions/recon.py   -> lee ./data, escribe ./out/recon_detail.csv
SOLUCIÓN de referencia: intenta tu versión el domingo 22 antes de abrirla.
"""
from pathlib import Path
import pandas as pd

DATA, OUT = Path("data"), Path("out")
CUTOFF = pd.Timestamp("2026-10-31 23:59:59")


def load():
    src = pd.read_csv(DATA / "source_claims.csv", parse_dates=["submitted_ts_utc"])
    sf = pd.read_csv(DATA / "sf_claims_raw.csv", parse_dates=["submitted_ts_utc", "_loaded_at"])
    portal = pd.read_csv(DATA / "portal_claims.csv")
    return src, sf, portal


def reconcile(src, sf, portal):
    src = src[src["submitted_ts_utc"] <= CUTOFF]                       # misma "foto" que el portal
    sf_dup_ids = sf.loc[sf["claim_id"].duplicated(), "claim_id"].unique()
    sf = sf[sf["submitted_ts_utc"] <= CUTOFF]                          # alinear la foto en ambos lados
    sf_d = sf.sort_values("_loaded_at").drop_duplicates("claim_id", keep="last")

    df = (src[["claim_id", "claim_status"]]
          .merge(sf_d[["claim_id", "_loaded_at"]], on="claim_id", how="outer", indicator="src_vs_sf")
          .merge(portal[["claim_id"]].drop_duplicates().assign(in_portal=True), on="claim_id", how="left"))
    df["in_portal"] = df["in_portal"].fillna(False).astype(bool)

    def reason(r):
        if r.src_vs_sf == "left_only":
            return "A_NOT_LOADED_TO_SNOWFLAKE"
        if r.src_vs_sf == "right_only":
            return "X_IN_SNOWFLAKE_NOT_IN_SOURCE"
        if not r.in_portal and r.claim_status == "VOID":
            return "E_VOID_EXCLUDED_BY_DEFINITION"
        if not r.in_portal and r._loaded_at > CUTOFF:
            return "B_LATE_ARRIVING"
        if not r.in_portal:
            return "Z_UNEXPLAINED"
        return "MATCHED"

    df["recon_reason"] = df.apply(reason, axis=1)
    df["is_duplicated_in_sf"] = df["claim_id"].isin(sf_dup_ids)
    return df


def summary(df, portal):
    s = df["recon_reason"].value_counts().rename_axis("reason").reset_index(name="claims")
    s["pct"] = (s["claims"] / len(df) * 100).round(2)
    extra = {
        "portal_rows": len(portal),
        "portal_distinct_claims": portal["claim_id"].nunique(),
        "portal_overcount_from_duplicates": len(portal) - portal["claim_id"].nunique(),
        "match_rate_pct": float(round((df["recon_reason"] == "MATCHED").mean() * 100, 2)),
    }
    return s, extra


if __name__ == "__main__":
    src, sf, portal = load()
    df = reconcile(src, sf, portal)
    s, extra = summary(df, portal)
    OUT.mkdir(exist_ok=True)
    df.to_csv(OUT / "recon_detail.csv", index=False)
    print(s.to_string(index=False))
    print(extra)
