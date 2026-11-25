import pandas as pd
from recon import reconcile

T = pd.Timestamp


def _src(rows):
    return pd.DataFrame(rows, columns=["claim_id", "claim_status", "billed_amount", "submitted_ts_utc"])


def test_each_reason_is_classified():
    src = _src([
        ["C1", "PAID", 100, T("2026-10-01")],   # MATCHED
        ["C2", "PAID", 100, T("2026-10-01")],   # NOT_LOADED
        ["C3", "VOID", 100, T("2026-10-01")],   # VOID_EXCLUDED
        ["C4", "PAID", 100, T("2026-10-30")],   # LATE_ARRIVING
    ])
    sf = pd.DataFrame({
        "claim_id": ["C1", "C1", "C3", "C4"],                  # C1 duplicado (re-run)
        "submitted_ts_utc": [T("2026-10-01")] * 3 + [T("2026-10-30")],
        "_loaded_at": [T("2026-10-01"), T("2026-10-02"), T("2026-10-01"), T("2026-11-20")],
    })
    portal = pd.DataFrame({"claim_id": ["C1", "C1"]})          # el portal no deduplica
    out = reconcile(src, sf, portal, cutoff=T("2026-10-31 23:59:59")).set_index("claim_id")["recon_reason"]
    assert out.to_dict() == {"C1": "MATCHED", "C2": "NOT_LOADED", "C3": "VOID_EXCLUDED", "C4": "LATE_ARRIVING"}


def test_grain_is_one_row_per_claim():
    src = _src([["C1", "PAID", 100, T("2026-10-01")]])
    sf = pd.DataFrame({"claim_id": ["C1", "C1"], "submitted_ts_utc": [T("2026-10-01")] * 2,
                       "_loaded_at": [T("2026-10-01"), T("2026-10-02")]})
    out = reconcile(src, sf, pd.DataFrame({"claim_id": ["C1"]}))
    assert out["claim_id"].is_unique
