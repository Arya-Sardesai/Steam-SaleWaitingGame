import pandas as pd

from steamwait.price_history import build_periods
from steamwait.snapshot_prices import parse_batch


def test_parse_batch_every_status():
    body = {
        "620": {"success": True, "data": {"price_overview": {
            "currency": "CAD", "initial": 1249, "final": 124, "discount_percent": 90}}},
        "570": {"success": True, "data": []},          # free game: Steam sends an empty list
        "1": {"success": False},
        # 2 is missing from the response entirely
    }
    rows = {r["appid"]: r for r in parse_batch(body, [620, 570, 1, 2], "2026-11-24", "t")}
    assert rows[620]["status"] == "priced" and rows[620]["discount_percent"] == 90
    assert rows[570]["status"] == "no_price"
    assert rows[1]["status"] == "not_found"
    assert rows[2]["status"] == "error"


def test_parse_batch_survives_garbage():
    rows = parse_batch(None, [5, 6], "2026-11-24", "t")
    assert [r["status"] for r in rows] == ["error", "error"]


def _snap(date, appid, final, disc, status="priced"):
    return {"snapshot_date": date, "fetched_at": "t", "appid": appid, "status": status,
            "currency": "CAD", "price_initial_cents": 2000, "price_final_cents": final,
            "discount_percent": disc}


def test_periods_only_on_change_and_errors_ignored():
    snaps = pd.DataFrame([
        _snap("2026-12-15", 1, 2000, 0),
        _snap("2026-12-16", 1, 2000, 0),
        _snap("2026-12-17", 1, 1000, 50),          # Winter Sale starts
        _snap("2026-12-18", 1, None, None, "error"),  # failed fetch: must NOT create a period
        _snap("2026-12-19", 1, 1000, 50),
        _snap("2027-01-05", 1, 2000, 0),           # sale over
        _snap("2026-12-15", 2, 500, 0),
        _snap("2026-12-19", 2, 500, 0),
    ])
    p = build_periods(snaps).sort_values(["appid", "valid_from"]).reset_index(drop=True)
    g1 = p[p["appid"] == 1]
    assert list(g1["discount_percent"]) == [0, 50, 0]
    assert str(g1.iloc[1]["valid_from"].date()) == "2026-12-17"
    assert str(g1.iloc[1]["valid_to"].date()) == "2027-01-05"
    assert g1.iloc[1]["days_observed"] == 2                 # 17th and 19th; the error day is skipped
    assert pd.isna(g1.iloc[2]["valid_to"])                  # still current
    assert len(p[p["appid"] == 2]) == 1                     # never changed = one period
