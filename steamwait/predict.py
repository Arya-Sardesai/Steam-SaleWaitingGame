"""Train the final models on all history and write everything the website needs, as of today.

Run in WSL (LightGBM needs Linux there), AFTER `python scripts/run_dbt.py build` on Windows:
    python -m steamwait.predict
Only reads dbt TABLES, never the stg_ views: those store Windows paths (C:/...) that WSL can't open.

Writes to site/data/:
  index.json        search index: [id, name, status] for every game + the sale calendar
  games/NN.json     full record per game, 64 shards by id % 64 (page loads one shard per game)
  browse.json       ranked lists for the Browse page
  model_card.json   honest scores on the last 12 months + reliability curves + top drivers
  status.json       live system status: counts, backfill progress, last dbt run, freshness

Modelling choices carried over from the notebooks:
- Timing: LightGBM at 7, 14 and 30 days, every game weighted equally. Notebook 03 showed the
  raw probabilities are already calibrated, so no extra calibration step.
- Discount: LightGBM quantile regression (10th/50th/90th), range widened with conformalized
  quantile regression to cover ~80% of real sales.
- "Why we think so": per-game SHAP contributions from the 30-day model, turned into sentences.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd
import shap
from sklearn.metrics import average_precision_score, mean_absolute_error, roc_auc_score

from steamwait import features as F

ROOT = Path(__file__).resolve().parent.parent
WAREHOUSE = ROOT / "data" / "warehouse.duckdb"
OUT_DIR = ROOT / "site" / "data"
HORIZONS = (7, 14, 30)
TARGET_COVERAGE = 0.80
N_SHARDS = 64
EPOCH = pd.Timestamp("2016-01-01")          # history dates stored as days since this
STATUS_CODE = {"forecast": "f", "on_sale": "s", "no_history": "n"}
SEED = 330


def DAY(n):
    return pd.to_timedelta(n, unit="D")


# ---------- models ----------
def timing_model() -> lgb.LGBMClassifier:
    return lgb.LGBMClassifier(n_estimators=400, learning_rate=0.05, num_leaves=31, min_child_samples=50,
                              subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                              random_state=SEED, verbose=-1)


def depth_model(alpha: float) -> lgb.LGBMRegressor:
    return lgb.LGBMRegressor(objective="quantile", alpha=alpha, n_estimators=400, learning_rate=0.05,
                             num_leaves=31, min_child_samples=50, random_state=SEED, verbose=-1)


def timing_X(df: pd.DataFrame) -> pd.DataFrame:
    return df[F.TIMING_NUMERIC + F.TIMING_CATEGORICAL].astype({"check_month": "category"})


def depth_X(df: pd.DataFrame) -> pd.DataFrame:
    return df[F.DEPTH_FEATURES].astype(float)


def weighted_quantile(values, weights, q) -> float:
    order = np.argsort(values)
    v, w = values[order], weights[order]
    cum = np.cumsum(w) / w.sum()
    return float(v[min(np.searchsorted(cum, q), len(v) - 1)])


def conformal_margin(lo, hi, y, w, coverage=TARGET_COVERAGE) -> float:
    """Conformalized quantile regression: widen [lo, hi] until `coverage` of held-out sales fit."""
    scores = np.maximum(lo - y, y - hi)
    level = min(1.0, coverage * (1 + 1 / len(scores)))
    return weighted_quantile(scores, w, level)


def reliability(p, y, w, bins=10) -> list[dict]:
    idx = np.clip((p * bins).astype(int), 0, bins - 1)
    out = []
    for b in range(bins):
        m = idx == b
        if w[m].sum() > 0:
            out.append({"predicted": round(float(np.average(p[m], weights=w[m])), 3),
                        "actual": round(float(np.average(y[m], weights=w[m])), 3), "n": int(m.sum())})
    return out


# ---------- training ----------
def train_timing(gaps, event_dates, as_of, card):
    models, holdout_start = {}, as_of - DAY(365)
    for h in HORIZONS:
        rows = F.build_checkins(gaps, event_dates, horizon=h)
        rows = rows[rows["check_date"] + DAY(h) <= as_of]
        fit = F.add_game_weights(rows[rows["check_date"] + DAY(h) < holdout_start].copy())
        test = F.add_game_weights(rows[rows["check_date"] >= holdout_start].copy())

        m = timing_model().fit(timing_X(fit), fit["label"], sample_weight=fit["weight"])
        p = m.predict_proba(timing_X(test))[:, 1]
        y, w = test["label"].to_numpy(), test["weight"].to_numpy()
        rule = (test["days_to_next_major_event"] <= h).astype(float).to_numpy()
        card[f"timing_{h}d"] = {
            "test_period": f"{holdout_start.date()} to {as_of.date()}",
            "test_rows": int(len(test)),
            "average_precision": round(average_precision_score(y, p, sample_weight=w), 3),
            "roc_auc": round(roc_auc_score(y, p, sample_weight=w), 3),
            "seasonal_rule_average_precision": round(average_precision_score(y, rule, sample_weight=w), 3),
            "seasonal_rule_roc_auc": round(roc_auc_score(y, rule, sample_weight=w), 3),
            "positive_rate": round(float(np.average(y, weights=w)), 3),
            "reliability": reliability(p, y, w),
        }
        allw = F.add_game_weights(rows.copy())
        models[h] = timing_model().fit(timing_X(allw), allw["label"], sample_weight=allw["weight"])
        c = card[f"timing_{h}d"]
        print(f"timing {h:>2}d: AP {c['average_precision']} (seasonal rule {c['seasonal_rule_average_precision']})")
    return models


def train_depth(con, event_dates, as_of, card):
    depth = F.build_depth_rows(con, event_dates)
    depth = depth[(depth["n_prev_sales"] >= 1) & (depth["start_date"] < as_of)]
    cal_start = as_of - DAY(365)
    fit = F.add_game_weights(depth[depth["start_date"] < cal_start].copy())
    cal = F.add_game_weights(depth[depth["start_date"] >= cal_start].copy())
    models = {a: depth_model(a).fit(depth_X(fit), fit["max_cut_percent"], sample_weight=fit["weight"])
              for a in (0.1, 0.5, 0.9)}
    lo, mid, hi = (models[a].predict(depth_X(cal)) for a in (0.1, 0.5, 0.9))
    y, w = cal["max_cut_percent"].to_numpy(), cal["weight"].to_numpy()
    raw_cov = float(np.average((y >= lo) & (y <= hi), weights=w))
    margin = conformal_margin(lo, hi, y, w)
    card["discount"] = {
        "calibration_period": f"{cal_start.date()} to {as_of.date()}",
        "calibration_sales": int(len(cal)),
        "mae_points": round(mean_absolute_error(y, mid, sample_weight=w), 2),
        "baseline_same_as_last_mae_points": round(mean_absolute_error(y, cal["last_cut"], sample_weight=w), 2),
        "raw_range_coverage": round(raw_cov, 3),
        "conformal_margin_points": round(margin, 2),
        "widened_range_coverage": round(float(np.average((y >= lo - margin) & (y <= hi + margin), weights=w)), 3),
    }
    d = card["discount"]
    print(f"discount: MAE {d['mae_points']} pts | coverage {raw_cov:.0%} -> {d['widened_range_coverage']:.0%}")
    return models, margin, depth


# ---------- today's state per game ----------
def todays_rows(con, gaps, depth, event_dates, as_of):
    games = con.sql("""
        select appid, name, main_publisher, genres, release_date,
               current_price_cents, current_regular_cents, current_cut_percent,
               total_sales, deepest_cut_ever, lowest_price_ever_cents, typical_cut_percent,
               typical_days_between_sales
        from dim_games where is_modelable
    """).df()

    now = gaps[~gaps["event_observed"]].sort_values("gap_start").groupby("appid").tail(1).copy()
    now["check_date"] = as_of
    now["days_waited"] = (as_of - now["gap_start"]).dt.days
    now = F.add_calendar_features(now, "check_date", event_dates)
    now["check_month"] = as_of.month

    hist = depth.sort_values("start_date").groupby("appid")
    last = hist.tail(1).set_index("appid")[["max_cut_percent", "median_last3_cut", "max_cut_so_far", "n_prev_sales"]]
    now = now.join(last, on="appid")
    now["last_cut"] = now["max_cut_percent"]
    now["max_cut_so_far"] = np.fmax(now["max_cut_so_far"], now["max_cut_percent"])
    now["n_prev_sales"] = now["n_prev_sales"].fillna(0) + 1
    now["days_since_prev_sale"] = now["days_waited"]
    soon = now["days_to_next_major_event"] <= 30
    now["is_major_event_sale"] = soon.astype(int)
    now["days_since_major_event_at_sale"] = np.where(soon, 0, now["days_since_major_event"])
    now["start_month"] = np.where(soon, now["next_major_event"].dt.month, as_of.month)
    return games, now


def depth_X_today(now: pd.DataFrame) -> pd.DataFrame:
    """Depth features for a sale that would start soon (not the timing calendar features)."""
    d = now[["last_cut", "median_last3_cut", "max_cut_so_far", "n_prev_sales", "days_since_prev_sale",
             "regular_dollars", "is_major_event_sale", "game_age_years", "start_month"]].copy()
    d["days_since_major_event"] = now["days_since_major_event_at_sale"]
    return d[F.DEPTH_FEATURES].astype(float)


def price_histories(con, appids: set[int]) -> dict[int, list]:
    """Compact history per game: [[days since 2016-01-01, price_cents, cut%], ...]."""
    h = con.sql("""
        select appid, changed_date, price_cents, cut_percent
        from int_price_changes order by appid, changed_at
    """).df()
    h = h[h["appid"].isin(appids)]
    h["d"] = (pd.to_datetime(h["changed_date"]) - EPOCH).dt.days
    return {int(a): [[int(d), int(p), int(c)] for d, p, c in zip(g["d"], g["price_cents"], g["cut_percent"])]
            for a, g in h.groupby("appid")}


# ---------- events ----------
def season_name(d: pd.Timestamp) -> str:
    m = d.month
    if m in (3, 4):
        return "Spring Sale"
    if m in (6, 7):
        return "Summer Sale"
    if m in (9, 10, 11):
        return "Autumn Sale"
    return "Winter Sale"


def all_events(con, event_dates) -> list[dict]:
    seed = con.sql("select start_date, event_name from steam_events").df()
    named = {pd.Timestamp(d).date(): n for d, n in zip(seed["start_date"], seed["event_name"])}
    out = []
    for d in event_dates:
        d = pd.Timestamp(d)
        n = named.get(d.date())
        out.append({"date": d.date().isoformat(), "name": n or f"Steam {season_name(d)} {d.year}",
                    "detected": n is None})
    seen = {e["date"] for e in out}
    for d, n in named.items():         # fests from the seed that aren't "major" still show on the calendar
        if d.isoformat() not in seen:
            out.append({"date": d.isoformat(), "name": n, "detected": False, "minor": True})
    return sorted(out, key=lambda e: e["date"])


# ---------- "why we think so" ----------
def reasons_for(row: pd.Series, contrib: dict[str, float], event_name: str | None) -> list[dict]:
    """Turn the 3 biggest SHAP contributions into plain sentences. '+' = pushes toward a sale soon."""
    def txt(f):
        v = row.get(f)
        if f == "days_to_next_major_event":
            if pd.isna(v):
                return "No big Steam sale has been announced yet."
            name = event_name or "The next big Steam sale"
            return (f"{name} starts in {int(v)} days. Most games get discounted during these."
                    if v <= 30 else f"The next big Steam sale ({name}) is still {int(v)} days away.")
        if f == "after_sale_number":
            n = int(row.get("n_prev_sales") or 0)
            return f"It has been on sale {n} times before." if n >= 10 else f"It has only been on sale {n} times on record."
        if f == "days_waited":
            typ = row.get("typical_days_between_sales")
            base = f"It's been {int(v)} days since its last sale"
            if pd.notna(typ) and typ > 0:
                return base + (f", longer than its usual gap of about {int(typ)} days." if v > typ
                               else f". It usually waits about {int(typ)} days between sales.")
            return base + "."
        if f == "days_since_major_event":
            return f"The last big Steam sale was {int(v)} days ago." if pd.notna(v) else None
        if f == "prev_gap_days":
            return f"Last time it went {int(v)} days between sales." if pd.notna(v) else None
        if f == "game_age_years":
            return f"It's {v:.0f} years old." if pd.notna(v) and v >= 1 else "It's a recent release."
        if f == "prev_sale_cut":
            return f"Its last sale was {int(v)}% off." if pd.notna(v) else None
        if f == "prev_sale_length_days":
            return f"Its last sale lasted {int(v)} days." if pd.notna(v) else None
        if f == "regular_dollars":
            return f"Its regular price is ${v:.2f}." if pd.notna(v) else None
        if f == "check_month":
            return "The time of year matters for how this game is discounted."
        return None

    out = []
    for f, c in sorted(contrib.items(), key=lambda kv: -abs(kv[1])):
        t = txt(f)
        if t and abs(c) > 0.05:
            out.append({"text": t, "dir": "+" if c > 0 else "-"})
        if len(out) == 3:
            break
    return out


def verdict(p30: float) -> str:
    if p30 >= 0.6:
        return "wait"
    if p30 <= 0.25:
        return "buy"
    return "toss_up"


def num(x, nd=2):
    if x is None or x is pd.NA:
        return None
    try:
        if pd.isna(x):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(x, (int, np.integer)):
        return int(x)
    v = round(float(x), nd)
    return int(v) if nd == 0 else v


def dollars(cents):
    return None if pd.isna(cents) else round(float(cents) / 100, 2)


# ---------- status ----------
def read_csv_count(path: Path, statuses: set[str]) -> int | None:
    if not path.exists():
        return None
    try:
        d = pd.read_csv(path, usecols=["appid", "status"])
        return int(d.loc[d["status"].isin(statuses), "appid"].nunique())
    except Exception:
        return None


def system_status(con, as_of, counts) -> dict:
    uni = sorted((ROOT / "data" / "universe").glob("appids_*.parquet"))

    def q(sql):
        return con.sql(sql).fetchall()[0][0]

    status = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "price_snapshot_date": as_of.date().isoformat(),
        "games_tracked": int(len(pd.read_parquet(uni[-1], columns=["appid"]))) if uni else None,
        "games_with_metadata": int(q("select count(*) from dim_games")),
        "price_changes": int(q("select count(*) from int_price_changes")),
        "sale_events": int(q("select count(*) from fct_sale_events")),
        "history_since": str(q("select min(changed_date) from int_price_changes")),
        "predictions": counts,
        "backfills": {
            "metadata_done": read_csv_count(ROOT / "data/state/appdetails_done.csv", {"ok", "not_found"}),
            "history_done": read_csv_count(ROOT / "data/state/itad_history_done.csv", {"ok", "empty", "not_found"}),
        },
    }
    rr = ROOT / "dbt" / "target" / "run_results.json"
    if rr.exists():
        r = json.loads(rr.read_text())
        st = pd.Series([x["status"] for x in r.get("results", [])]).value_counts().to_dict()
        status["dbt"] = {"finished_at": r.get("metadata", {}).get("generated_at"),
                         "elapsed_seconds": round(r.get("elapsed_time", 0), 1),
                         "results": {k: int(v) for k, v in st.items()}}
    return status


# ---------- browse ----------
def browse_lists(records: list[dict]) -> dict:
    def slim(r, **extra):
        return {"id": r["id"], "name": r["name"], "price": r["price"], "regular": r["regular"],
                "lowest_ever": r["lowest_ever"], **extra}

    fc = [r for r in records if r["status"] == "forecast" and r["regular"]]
    sale = [r for r in records if r["status"] == "on_sale" and r["lowest_ever"] and r["price"]]
    soon = sorted(fc, key=lambda r: -r["p14"])[:40]
    savings = sorted(fc, key=lambda r: -(r["p30"] * (r["regular"] - r["price_range"][1])))[:40]
    at_low = sorted([r for r in sale if r["price"] <= r["lowest_ever"] * 1.001],
                    key=lambda r: -(r["regular"] - r["price"]))[:40]
    pricey = sorted([r for r in fc if r["typical_cut"] and r["typical_cut"] >= 40 and r["p30"] >= 0.5],
                    key=lambda r: -(r["typical_cut"] * r["p30"]))[:40]
    return {
        "likely_soon": [slim(r, p14=r["p14"], p30=r["p30"], cut_range=r["cut_range"],
                             next_event_name=r["next_event_name"]) for r in soon],
        "biggest_savings": [slim(r, p30=r["p30"], price_range=r["price_range"], cut_range=r["cut_range"])
                            for r in savings],
        "at_all_time_low": [slim(r, cut_now=r["cut_now"]) for r in at_low],
        "dont_buy_yet": [slim(r, p30=r["p30"], typical_cut=r["typical_cut"]) for r in pricey],
    }


def write_outputs(records, card, events, status, as_of) -> None:
    games_dir = OUT_DIR / "games"
    games_dir.mkdir(parents=True, exist_ok=True)
    for old in games_dir.glob("*.json"):
        old.unlink()

    def dump(obj):
        return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)

    meta = {"as_of": as_of.date().isoformat(), "shards": N_SHARDS, "history_epoch": EPOCH.date().isoformat(),
            "events": events}
    (OUT_DIR / "index.json").write_text(dump({**meta, "games": [[r["id"], r["name"], STATUS_CODE[r["status"]]]
                                                                 for r in records]}), encoding="utf-8")
    shards: dict[int, dict] = {i: {} for i in range(N_SHARDS)}
    for r in records:
        shards[r["id"] % N_SHARDS][str(r["id"])] = r
    for i, shard in shards.items():
        (games_dir / f"{i:02d}.json").write_text(dump(shard), encoding="utf-8")
    (OUT_DIR / "browse.json").write_text(dump({"as_of": meta["as_of"], **browse_lists(records)}), encoding="utf-8")
    (OUT_DIR / "model_card.json").write_text(json.dumps(card, indent=1), encoding="utf-8")
    (OUT_DIR / "status.json").write_text(json.dumps(status, indent=1), encoding="utf-8")
    old = OUT_DIR / "predictions.json"
    if old.exists():
        old.unlink()
    idx = (OUT_DIR / "index.json").stat().st_size / 1e6
    big = max(f.stat().st_size for f in games_dir.glob("*.json")) / 1e3
    print(f"site/data: index {idx:.2f} MB | {N_SHARDS} shards, largest {big:.0f} KB")


# ---------- main ----------
def main() -> None:
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    as_of = pd.Timestamp(con.sql("select max(price_as_of) from dim_games").fetchall()[0][0])
    print(f"Predictions as of {as_of.date()}")

    event_dates = F.load_event_dates(con)
    events = all_events(con, event_dates)
    event_by_date = {e["date"]: e["name"] for e in events}
    gaps = F.load_gaps(con)
    card = {"as_of": as_of.date().isoformat()}
    timing = train_timing(gaps, event_dates, as_of, card)
    depth_models, margin, depth = train_depth(con, event_dates, as_of, card)
    games, now = todays_rows(con, gaps, depth, event_dates, as_of)
    now = now.join(games.set_index("appid")[["typical_days_between_sales"]], on="appid")

    shap_by_appid = pd.DataFrame()
    if len(now):
        Xn = timing_X(now)
        for h in HORIZONS:
            now[f"p{h}"] = timing[h].predict_proba(Xn)[:, 1]
        now["p14"] = np.maximum(now["p14"], now["p7"])          # cumulative: p7 <= p14 <= p30
        now["p30"] = np.maximum(now["p30"], now["p14"])
        lo, mid, hi = (depth_models[a].predict(depth_X_today(now)) for a in (0.1, 0.5, 0.9))
        now["cut_lo"] = np.clip(np.minimum(lo, mid) - margin, 5, 95)
        now["cut_mid"] = np.clip(mid, 5, 95)
        now["cut_hi"] = np.clip(np.maximum(hi, mid) + margin, 5, 95)
        sv = shap.TreeExplainer(timing[30]).shap_values(Xn)
        sv = sv[1] if isinstance(sv, list) else sv
        shap_df = pd.DataFrame(sv, columns=Xn.columns)
        card["top_drivers"] = shap_df.abs().mean().sort_values(ascending=False).round(3).head(8).to_dict()
        shap_by_appid = shap_df.set_index(now["appid"].to_numpy())
    preds = now.set_index("appid")

    histories = price_histories(con, set(games["appid"].astype(int)))
    records, counts = [], {"forecast": 0, "on_sale": 0, "no_history": 0}
    for g in games.itertuples(index=False):
        appid = int(g.appid)
        genres = [str(x) for x in list(g.genres)[:3]] if isinstance(g.genres, (list, np.ndarray)) else []
        rec = {"id": appid, "name": g.name, "publisher": g.main_publisher, "genres": genres,
               "released": None if pd.isna(g.release_date) else str(pd.Timestamp(g.release_date).date()),
               "price": dollars(g.current_price_cents), "regular": dollars(g.current_regular_cents),
               "lowest_ever": dollars(g.lowest_price_ever_cents), "deepest_ever": num(g.deepest_cut_ever, 0),
               "typical_cut": num(g.typical_cut_percent, 0), "typical_gap_days": num(g.typical_days_between_sales, 0),
               "sales_recorded": num(g.total_sales, 0), "history": histories.get(appid, [])}
        cut_now = 0 if pd.isna(g.current_cut_percent) else int(g.current_cut_percent)
        if cut_now > 0:
            rec.update(status="on_sale", verdict="on_sale", cut_now=cut_now)
        elif appid in preds.index:
            p = preds.loc[appid]
            nxt = None if pd.isna(p["next_major_event"]) else pd.Timestamp(p["next_major_event"]).date().isoformat()
            reg = rec["regular"] or 0
            contrib = shap_by_appid.loc[appid].to_dict() if appid in shap_by_appid.index else {}
            name = event_by_date.get(nxt) if nxt else None
            rec.update(
                status="forecast", verdict=verdict(p["p30"]),
                p7=num(p["p7"]), p14=num(p["p14"]), p30=num(p["p30"]),
                days_since_sale=num(p["days_waited"], 0),
                next_event=nxt, next_event_name=name,
                days_to_next_event=num(p["days_to_next_major_event"], 0),
                cut_range=[int(round(p["cut_lo"])), int(round(p["cut_hi"]))], cut_likely=int(round(p["cut_mid"])),
                price_range=[num(reg * (1 - p["cut_hi"] / 100)), num(reg * (1 - p["cut_lo"] / 100))],
                reasons=reasons_for(p, contrib, name),
            )
        else:
            rec.update(status="no_history", verdict=None)
        counts[rec["status"]] += 1
        records.append(rec)

    write_outputs(records, card, events, system_status(con, as_of, counts), as_of)
    print(f"\n{counts}")


if __name__ == "__main__":
    main()
