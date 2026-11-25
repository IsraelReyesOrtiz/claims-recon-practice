"""
HaloMD practice dataset v2 — claims, pagos y escenario "portal vs. fuente".

Genera ./data/*.csv + ./data/_answer_key.json (NO lo abras hasta el miércoles 25).

Tablas
  providers, payers, cpt_codes              dimensiones
  source_claims                             sistema fuente (system of record), 1 fila por claim
  sf_claims_raw                             lo que aterrizó en Snowflake (con fallas de carga)
  portal_claims                             lo que muestra el portal al cliente
  remittances                               líneas 835: pagos, parciales, negaciones, reversos, huérfanos
  bank_deposits                             depósitos EFT para conciliar
  idr_disputes                              disputas No Surprises Act

Uso:  pip install pandas numpy
      python generate_dataset_v2.py --claims 30000 --seed 7
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

PORTAL_CUTOFF = pd.Timestamp("2026-10-31 23:59:59")  # snapshot del portal (UTC)


def main(n: int, seed: int, out: Path):
    rng = np.random.default_rng(seed)
    out.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ dims
    n_prov = 80
    providers = pd.DataFrame({
        "provider_id": [f"PRV{i:04d}" for i in range(1, n_prov + 1)],
        "npi": rng.integers(1_000_000_000, 1_999_999_999, n_prov).astype(str),
        "provider_name": [f"Provider Group {i:03d}" for i in range(1, n_prov + 1)],
        "specialty": rng.choice(["Emergency Medicine", "Anesthesiology", "Radiology", "Neonatology"],
                                n_prov, p=[.45, .3, .2, .05]),
        "state": rng.choice(["TX", "FL", "GA", "AZ", "NJ", "NY"], n_prov),
    })
    payers = pd.DataFrame({
        "payer_id": [f"PAY{i:02d}" for i in range(1, 9)],
        "payer_name": ["Blue Star Health", "UnityCare", "Aetnex", "CignaPlus",
                       "Humanity Health", "Lone Star BCBS", "Sunshine Plan", "Peach State Mutual"],
        "prompt_pay_days": [30, 30, 45, 30, 45, 30, 30, 45],   # plazo prometido para pagar
        "avg_days_to_pay": [22, 35, 40, 28, 55, 25, 33, 60],   # comportamiento real
    })
    cpt = pd.DataFrame({
        "cpt_code": ["99283", "99284", "99285", "99291", "00790", "01967", "70450", "74177"],
        "description": ["ED visit L3", "ED visit L4", "ED visit L5", "Critical care 1st hr",
                        "Anesthesia upper abdomen", "Labor epidural", "CT head", "CT abd/pelvis"],
        "base_charge": [780, 1350, 2100, 2600, 1900, 1700, 1100, 1800],
        "qpa_ratio": [.28, .27, .26, .30, .35, .38, .22, .20],
    })

    # --------------------------------------------------------- source_claims
    ci = rng.integers(0, len(cpt), n)
    dos = pd.Timestamp("2026-01-01") + pd.to_timedelta(rng.integers(0, 290, n), "D")
    # submitted_ts en UTC, con hora (clave para el problema de zona horaria)
    submitted_ts = dos + pd.to_timedelta(rng.integers(2, 20, n), "D") + pd.to_timedelta(rng.integers(0, 86400, n), "s")
    billed = (cpt["base_charge"].values[ci] * rng.normal(1, .15, n)).clip(100).round(2)
    status = rng.choice(["SUBMITTED", "PAID", "DENIED", "VOID"], n, p=[.12, .72, .12, .04])
    src = pd.DataFrame({
        "claim_id": [f"CLM{i:07d}" for i in range(1, n + 1)],
        "provider_id": rng.choice(providers["provider_id"], n),
        "payer_id": rng.choice(payers["payer_id"], n),
        "patient_id": [f"PT{x:06d}" for x in rng.integers(1, n // 2, n)],
        "date_of_service": dos.date,
        "submitted_ts_utc": submitted_ts,
        "cpt_code": cpt["cpt_code"].values[ci],
        "icd10_primary": rng.choice(["R07.9", "S06.0X0A", "R10.9", "J18.9", "O80", "I21.9"], n),
        "network_status": rng.choice(["OON", "INN"], n, p=[.85, .15]),
        "billed_amount": billed,
        "qpa_amount": (billed * cpt["qpa_ratio"].values[ci] * rng.normal(1, .08, n)).round(2),
        "claim_status": status,
    })
    src = src[src["submitted_ts_utc"] <= pd.Timestamp("2026-11-15")].reset_index(drop=True)

    # Causa A: ICD-10 malformado -> el COPY INTO de Snowflake rechaza la fila
    bad = rng.random(len(src)) < .006
    src.loc[bad, "icd10_primary"] = src.loc[bad, "icd10_primary"] + "|X"

    # --------------------------------------------------------- sf_claims_raw
    sf = src[~bad].copy()
    sf["_batch_id"] = "B" + pd.to_datetime(sf["submitted_ts_utc"]).dt.strftime("%Y%m%d")
    # Causa B: llegada tardía (late-arriving) -> se cargó DESPUÉS del corte del portal
    lag_h = rng.exponential(6, len(sf))
    late = rng.random(len(sf)) < .012
    lag_h[late] = rng.uniform(24 * 20, 24 * 40, late.sum())
    sf["_loaded_at"] = pd.to_datetime(sf["submitted_ts_utc"]) + pd.to_timedelta(lag_h, "h")
    # Causa C: un batch se re-ejecutó -> duplicados (mismo claim_id, otro _loaded_at)
    rerun_batches = rng.choice(sf["_batch_id"].unique(), 6, replace=False)
    dups = sf[sf["_batch_id"].isin(rerun_batches)].copy()
    dups["_loaded_at"] = dups["_loaded_at"] + pd.Timedelta(hours=26)
    sf = pd.concat([sf, dups], ignore_index=True)
    # Ruido de formato (no cambia conteos)
    m = rng.random(len(sf))
    sf.loc[m < .01, "cpt_code"] = " " + sf.loc[m < .01, "cpt_code"]
    sf.loc[(m >= .01) & (m < .02), "network_status"] = sf.loc[(m >= .01) & (m < .02), "network_status"].str.lower()
    sf = sf.sample(frac=1, random_state=seed).reset_index(drop=True)

    # --------------------------------------------------------- portal_claims
    # Portal: snapshot al corte, SIN deduplicar, excluye VOID,
    # y convierte la fecha a America/Chicago (causa D: zona horaria)
    portal = sf[sf["_loaded_at"] <= PORTAL_CUTOFF].copy()
    portal = portal[portal["claim_status"] != "VOID"]
    portal["submitted_date_local"] = (pd.to_datetime(portal["submitted_ts_utc"]).dt.tz_localize("UTC")
                                      .dt.tz_convert("America/Chicago").dt.date)
    portal = portal[["claim_id", "provider_id", "payer_id", "submitted_date_local", "claim_status", "billed_amount"]]

    # --------------------------------------------------------- remittances
    pay_src = src[src["claim_status"].isin(["PAID", "DENIED"])].copy()
    pdays = pay_src["payer_id"].map(payers.set_index("payer_id")["avg_days_to_pay"])
    pay_src["payment_date"] = (pd.to_datetime(pay_src["submitted_ts_utc"]).dt.normalize()
                               + pd.to_timedelta(pdays.values + rng.integers(-12, 20, len(pay_src)), "D"))
    denied = pay_src["claim_status"].eq("DENIED").values
    allowed = np.where(denied, 0, (pay_src["qpa_amount"] * rng.normal(1, .1, len(pay_src))).round(2))
    pr = np.where(denied, 0, (allowed * rng.choice([0, .1, .2], len(pay_src))).round(2))
    rem = pd.DataFrame({
        "claim_id": pay_src["claim_id"].values,
        "payer_id": pay_src["payer_id"].values,
        "payment_date": pay_src["payment_date"].dt.date.values,
        "billed_amount": pay_src["billed_amount"].values,
        "allowed_amount": allowed,
        "patient_resp": pr,
        "paid_amount": (allowed - pr).round(2),
        "carc_code": np.where(denied, rng.choice(["CO-197", "CO-16", "CO-50", "PR-1"], len(pay_src)), "CO-45"),
        "line_type": np.where(denied, "DENIAL", "PAYMENT"),
    })
    # Pagos parciales: 5% del pago se divide en 2 líneas
    split = rem[rem["line_type"].eq("PAYMENT")].sample(frac=.05, random_state=seed).copy()
    rem.loc[split.index, "paid_amount"] = (rem.loc[split.index, "paid_amount"] * .6).round(2)
    split["paid_amount"] = (split["paid_amount"] * .4).round(2)
    split["payment_date"] = (pd.to_datetime(split["payment_date"]) + pd.Timedelta(days=21)).dt.date
    split["line_type"] = "PAYMENT_2ND"
    # Reversos (takebacks) con monto negativo
    rev = rem[rem["line_type"].eq("PAYMENT")].sample(frac=.02, random_state=seed + 1).copy()
    rev["paid_amount"] = -rev["paid_amount"]
    rev["payment_date"] = (pd.to_datetime(rev["payment_date"]) + pd.Timedelta(days=35)).dt.date
    rev["carc_code"], rev["line_type"] = "CO-129", "REVERSAL"
    # Huérfanos: pagos con claim_id que no existe
    orp = rem.sample(n=35, random_state=seed + 2).copy()
    orp["claim_id"] = [f"CLM9{i:06d}" for i in range(len(orp))]
    orp["line_type"] = "PAYMENT"
    rem = pd.concat([rem, split, rev, orp], ignore_index=True)
    rem = rem[pd.to_datetime(rem["payment_date"]) <= pd.Timestamp("2026-11-15")]  # sin pagos futuros
    rem = rem.sample(frac=1, random_state=seed).reset_index(drop=True)
    rem.insert(0, "remit_line_id", [f"RL{i:08d}" for i in range(1, len(rem) + 1)])
    rem["eft_number"] = rem["payer_id"] + "-" + pd.to_datetime(rem["payment_date"]).dt.strftime("%Y%m%d")

    # --------------------------------------------------------- bank_deposits
    dep = rem.groupby(["eft_number", "payer_id"], as_index=False).agg(
        expected=("paid_amount", "sum"), d=("payment_date", "max"))
    r = rng.random(len(dep))
    dep["deposit_amount"] = np.where(r < .04, (dep["expected"] - rng.uniform(10, 400, len(dep))), dep["expected"]).round(2)
    dep["deposit_date"] = (pd.to_datetime(dep["d"]) + pd.to_timedelta(rng.integers(1, 4, len(dep)), "D")).dt.date
    dep = dep[r > .015]  # 1.5% sin depósito
    bank = dep[["eft_number", "payer_id", "deposit_date", "deposit_amount"]].reset_index(drop=True)
    bank.insert(0, "deposit_id", [f"DEP{i:06d}" for i in range(1, len(bank) + 1)])

    # --------------------------------------------------------- idr_disputes
    net = rem.groupby("claim_id", as_index=False)["paid_amount"].sum()
    el = src.merge(net, on="claim_id")
    el = el[(el["network_status"] == "OON") & (el["paid_amount"] < el["billed_amount"] * .5)].sample(frac=.15, random_state=seed)
    one = pd.to_datetime(el["submitted_ts_utc"]).dt.normalize() + pd.to_timedelta(rng.integers(30, 50, len(el)), "D")
    one_end = one + pd.offsets.BDay(30)
    init = [d + pd.offsets.BDay(int(k)) for d, k in zip(one_end, rng.choice([1, 2, 3, 4, 5, 6], len(el), p=[.3, .25, .2, .13, .07, .05]))]
    o = rng.random(len(el))
    idr = pd.DataFrame({
        "dispute_id": [f"DISP-{i:06d}" for i in range(1, len(el) + 1)],
        "claim_id": el["claim_id"].values,
        "open_negotiation_end": one_end.dt.date.values,
        "idr_initiated_date": pd.to_datetime(init).date,
        "qpa_amount": el["qpa_amount"].values,
        "provider_offer": (el["qpa_amount"] * rng.uniform(2.5, 5, len(el))).round(2).values,
        "payer_offer": (el["qpa_amount"] * rng.uniform(.95, 1.1, len(el))).round(2).values,
        "idr_status": np.select([o < .7, o < .82, o < .92], ["Provider Won", "Payer Won", "Ineligible"], "Pending"),
    })

    # --------------------------------------------------------- answer key
    sf_dedup = sf.drop_duplicates("claim_id")
    key = {
        "source_claims_total": int(len(src)),
        "A_rejected_by_load_bad_icd10": int(bad.sum()),
        "B_late_arriving_submitted_before_cutoff_loaded_after": int(((sf_dedup["_loaded_at"] > PORTAL_CUTOFF) &
                                                                  (pd.to_datetime(sf_dedup["submitted_ts_utc"]) <= PORTAL_CUTOFF)).sum()),
        "C_duplicate_rows_from_rerun_batches": int(len(dups)),
        "C_rerun_batch_ids": sorted(rerun_batches.tolist()),
        "D_timezone_month_shift_rows": int((pd.to_datetime(src["submitted_ts_utc"]).dt.month !=
                                            pd.to_datetime(src["submitted_ts_utc"]).dt.tz_localize("UTC")
                                            .dt.tz_convert("America/Chicago").dt.month).sum()),
        "E_void_excluded_by_portal_definition": int((src["claim_status"] == "VOID").sum()),
        "portal_rows": int(len(portal)),
        "remit_reversals": int(len(rev)),
        "remit_partial_second_lines": int(len(split)),
        "remit_orphans": int(len(orp)),
        "portal_cutoff_utc": str(PORTAL_CUTOFF),
    }

    tables = {"providers": providers, "payers": payers, "cpt_codes": cpt, "source_claims": src,
              "sf_claims_raw": sf, "portal_claims": portal, "remittances": rem,
              "bank_deposits": bank, "idr_disputes": idr}
    for name, df in tables.items():
        df.to_csv(out / f"{name}.csv", index=False)
        print(f"{name:15s} {len(df):>8,} filas")
    (out / "_answer_key.json").write_text(json.dumps(key, indent=2))
    print("answer key -> data/_answer_key.json (no lo abras hasta el día 6)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--claims", type=int, default=30000)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--out", type=Path, default=Path("data"))
    a = p.parse_args()
    main(a.claims, a.seed, a.out)
