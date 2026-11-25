"""Conciliación fuente -> Snowflake -> portal (grain: 1 fila por claim)."""
import pandas as pd

CUTOFF = pd.Timestamp("2026-10-31 23:59:59")


def load(data_dir="data"):
    src = pd.read_csv(f"{data_dir}/source_claims.csv", parse_dates=["submitted_ts_utc"])
    sf = pd.read_csv(f"{data_dir}/sf_claims_raw.csv", parse_dates=["submitted_ts_utc", "_loaded_at"])
    portal = pd.read_csv(f"{data_dir}/portal_claims.csv")
    return src, sf, portal


def reconcile(src, sf, portal, cutoff=CUTOFF):
    # 1) Misma "foto" en ambos lados
    src = src[src["submitted_ts_utc"] <= cutoff]
    sf = sf[sf["submitted_ts_utc"] <= cutoff]
    # 2) Dedup en Snowflake: gana la carga más reciente
    sf_d = sf.sort_values("_loaded_at").drop_duplicates("claim_id", keep="last")
    # 3) Outer merge para ver quién está dónde
    df = (src[["claim_id", "claim_status", "billed_amount"]]
          .merge(sf_d[["claim_id", "_loaded_at"]], on="claim_id", how="outer", indicator="src_vs_sf")
          .merge(portal[["claim_id"]].drop_duplicates().assign(in_portal=True), on="claim_id", how="left"))
    df["in_portal"] = df["in_portal"].fillna(False).astype(bool)
    # 4) Clasificación vectorizada (orden = prioridad)
    conds = [
        df["src_vs_sf"].eq("left_only"),
        df["src_vs_sf"].eq("right_only"),
        ~df["in_portal"] & df["claim_status"].eq("VOID"),
        ~df["in_portal"] & (df["_loaded_at"] > cutoff),
        ~df["in_portal"],
    ]
    labels = ["NOT_LOADED", "NOT_IN_SOURCE", "VOID_EXCLUDED", "LATE_ARRIVING", "UNEXPLAINED"]
    df["recon_reason"] = pd.Series(pd.NA, index=df.index, dtype="object")
    for c, l in zip(conds, labels):
        df.loc[c & df["recon_reason"].isna(), "recon_reason"] = l
    df["recon_reason"] = df["recon_reason"].fillna("MATCHED")
    return df


def summary(df, portal):
    s = (df.groupby("recon_reason")
           .agg(claims=("claim_id", "size"), billed_usd=("billed_amount", "sum"))
           .sort_values("claims", ascending=False))
    s["pct"] = (s["claims"] / len(df) * 100).round(2)
    kpis = {
        "source_claims": int(len(df)),
        "match_rate_pct": float(round((df["recon_reason"] == "MATCHED").mean() * 100, 2)),
        "portal_rows": int(len(portal)),
        "portal_duplicate_rows": int(len(portal) - portal["claim_id"].nunique()),
    }
    return s, kpis


if __name__ == "__main__":
    src, sf, portal = load()
    df = reconcile(src, sf, portal)
    s, kpis = summary(df, portal)
    print(s.to_string())
    print(kpis)
    df[df["recon_reason"] != "MATCHED"].to_csv("recon_exceptions.csv", index=False)
    print("excepciones ->", "recon_exceptions.csv")
