"""Train the final models on all history and write predictions for every game, as of today.

Run in WSL (LightGBM needs Linux there), AFTER `python scripts/run_dbt.py build` on Windows.
Only reads dbt TABLES, never the stg_ views: those store Windows paths (C:/...) that WSL can't open.
    python -m steamwait.predict

Writes site/data/index.json        search index: [id, name, status] for every game (small, loaded once)
       site/data/games/NN.json      full prediction per game, split into 64 shards by id % 64
                                    (the page only loads the shard for the game you click)
       site/data/model_card.json    how good the models are, measured on the last 12 months

Choices carried over from the notebooks:
- Timing: LightGBM at 14 and 30 days, every game weighted equally. Notebook 03 showed the raw
  probabilities are already calibrated, so no extra calibration step.
- Discount: LightGBM quantile regression (10th/50th/90th). The 10-90 range was too narrow
  (74% coverage), so it's widened with conformalized quantile regression to cover ~80%.
"""
from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, mean_absolute_error, roc_auc_score

from steamwait import features as F

ROOT = Path(__file__).resolve().parent.parent
WAREHOUSE = ROOT / "data" / "warehouse.duckdb"
OUT_DIR = ROOT / "site" / "data"
N_SHARDS = 64
STATUS_CODE = {"forecast": "f", "on_sale": "s", "no_history": "n"}
HORIZONS = (14, 30)
TARGET_COVERAGE = 0.80
SEED = 330


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


def weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    order = np.argsort(values)
    v, w = values[order], weights[order]
    cum = np.cumsum(w) / w.sum()
    return float(v[min(np.searchsorted(cum, q), len(v) - 1)])


def conformal_margin(lo, hi, y, w, coverage=TARGET_COVERAGE) -> float:
    """Conformalized quantile regression: how much to widen [lo, hi] so that `coverage` of
    held-out sales land inside. Score = how far outside the range each sale fell."""
    scores = np.maximum(lo - y, y - hi)
    n = len(scores)
    level = min(1.0, coverage * (1 + 1 / n))     # small-sample correction
    return weighted_quantile(scores, w, level)


# ---------- training ----------
def train_timing(gaps, event_dates, as_of, card):
    models = {}
    holdout_start = as_of - pd.Timedelta(days=365)
    for h in HORIZONS:
        rows = F.build_checkins(gaps, event_dates, horizon=h)
        rows = rows[rows["check_date"] + pd.to_timedelta(h, unit="D") <= as_of]
        fit = F.add_game_weights(rows[rows["check_date"] + pd.to_timedelta(h, unit="D") < holdout_start].copy())
        test = F.add_game_weights(rows[rows["check_date"] >= holdout_start].copy())

        # 1) honest score on the last 12 months, with a model that never saw them
        m = timing_model().fit(timing_X(fit), fit["label"], sample_weight=fit["weight"])
        p = m.predict_proba(timing_X(test))[:, 1]
        rule = (test["days_to_next_major_event"] <= h).astype(float)
        card[f"timing_{h}d"] = {
            "test_period": f"{holdout_start.date()} to {as_of.date()}",
            "test_rows": int(len(test)),
            "average_precision": round(average_precision_score(test["label"], p, sample_weight=test["weight"]), 3),
            "roc_auc": round(roc_auc_score(test["label"], p, sample_weight=test["weight"]), 3),
            "seasonal_rule_average_precision": round(
                average_precision_score(test["label"], rule, sample_weight=test["weight"]), 3),
            "positive_rate": round(float(np.average(test["label"], weights=test["weight"])), 3),
        }
        # 2) final model on everything we know, for today's predictions
        allw = F.add_game_weights(rows.copy())
        models[h] = timing_model().fit(timing_X(allw), allw["label"], sample_weight=allw["weight"])
        print(f"timing {h}d: AP {card[f'timing_{h}d']['average_precision']} "
              f"(seasonal rule {card[f'timing_{h}d']['seasonal_rule_average_precision']})")
    return models


def train_depth(con, event_dates, as_of, card):
    depth = F.build_depth_rows(con, event_dates)
    depth = depth[(depth["n_prev_sales"] >= 1) & (depth["start_date"] < as_of)]
    cal_start = as_of - pd.Timedelta(days=365)
    fit = F.add_game_weights(depth[depth["start_date"] < cal_start].copy())
    cal = F.add_game_weights(depth[depth["start_date"] >= cal_start].copy())

    models = {a: depth_model(a).fit(depth_X(fit), fit["max_cut_percent"], sample_weight=fit["weight"])
              for a in (0.1, 0.5, 0.9)}
    lo, mid, hi = (models[a].predict(depth_X(cal)) for a in (0.1, 0.5, 0.9))
    y, w = cal["max_cut_percent"].to_numpy(), cal["weight"].to_numpy()
    raw_cov = float(np.average((y >= lo) & (y <= hi), weights=w))
    margin = conformal_margin(lo, hi, y, w)
    new_cov = float(np.average((y >= lo - margin) & (y <= hi + margin), weights=w))
    card["discount"] = {
        "calibration_period": f"{cal_start.date()} to {as_of.date()}",
        "calibration_sales": int(len(cal)),
        "mae_points": round(mean_absolute_error(y, mid, sample_weight=w), 2),
        "baseline_same_as_last_mae_points": round(mean_absolute_error(y, cal["last_cut"], sample_weight=w), 2),
        "raw_range_coverage": round(raw_cov, 3),
        "conformal_margin_points": round(margin, 2),
        "widened_range_coverage": round(new_cov, 3),
    }
    print(f"discount: MAE {card['discount']['mae_points']} pts | range coverage "
          f"{raw_cov:.0%} -> {new_cov:.0%} after widening by {margin:.1f} pts")
    return models, margin, depth


# ---------- today's state per game ----------
def todays_rows(con, gaps, depth, event_dates, as_of):
    games = con.sql("""
        select appid, name, current_price_cents, current_regular_cents, current_cut_percent,
               total_sales, deepest_cut_ever, lowest_price_ever_cents, typical_cut_percent,
               typical_days_between_sales, release_date, is_on_sale_now
        from dim_games where is_modelable
    """).df()

    # the open (still waiting) gap for each game: features as of today
    open_gaps = gaps[~gaps["event_observed"]].sort_values("gap_start").groupby("appid").tail(1).copy()
    open_gaps["check_date"] = as_of
    open_gaps["days_waited"] = (as_of - open_gaps["gap_start"]).dt.days
    open_gaps = F.add_calendar_features(open_gaps, "check_date", event_dates)
    open_gaps["check_month"] = as_of.month

    # discount features for "if it went on sale soon"
    hist = depth.sort_values("start_date").groupby("appid")
    last = hist.tail(1).set_index("appid")[["max_cut_percent", "median_last3_cut", "max_cut_so_far", "n_prev_sales"]]
    recent = hist.apply(lambda g: [[d.strftime("%Y-%m-%d"), int(c)] for d, c in
                                   zip(g["start_date"].tail(6), g["max_cut_percent"].tail(6))],
                        include_groups=False).rename("recent_sales")
    open_gaps = open_gaps.join(last, on="appid").join(recent, on="appid")
    # median of the last 3 INCLUDING the most recent sale (build_depth_rows shifts by one)
    open_gaps["last_cut"] = open_gaps["max_cut_percent"]
    open_gaps["max_cut_so_far"] = np.fmax(open_gaps["max_cut_so_far"], open_gaps["max_cut_percent"])
    open_gaps["n_prev_sales"] = open_gaps["n_prev_sales"].fillna(0) + 1
    open_gaps["days_since_prev_sale"] = open_gaps["days_waited"]
    soon = open_gaps["days_to_next_major_event"] <= 30
    open_gaps["is_major_event_sale"] = soon.astype(int)
    open_gaps["days_since_major_event"] = np.where(soon, 0, open_gaps["days_since_major_event"])
    open_gaps["start_month"] = np.where(soon, open_gaps["next_major_event"].dt.month, as_of.month)
    open_gaps["regular_dollars"] = open_gaps["regular_cents"] / 100
    return games, open_gaps


def event_names(con) -> dict:
    ev = con.sql("select start_date, event_name from steam_events").df()
    return {pd.Timestamp(d).date().isoformat(): n for d, n in zip(ev["start_date"], ev["event_name"])}


def verdict(p30: float, on_sale: bool) -> str:
    if on_sale:
        return "on_sale"
    if p30 >= 0.6:
        return "wait"
    if p30 <= 0.25:
        return "buy"
    return "toss_up"


def dollars(cents):
    return None if pd.isna(cents) else round(float(cents) / 100, 2)


def clean(x):
    if x is None or x is pd.NA or (isinstance(x, float) and math.isnan(x)):
        return None
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        return None if np.isnan(x) else round(float(x), 2)
    return x


# ---------- main ----------
def main() -> None:
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    as_of = pd.Timestamp(con.sql("select max(price_as_of) from dim_games").fetchall()[0][0])
    print(f"Predictions as of {as_of.date()}")

    event_dates = F.load_event_dates(con)
    gaps = F.load_gaps(con)
    card = {"as_of": as_of.date().isoformat()}
    timing = train_timing(gaps, event_dates, as_of, card)
    depth_models, margin, depth = train_depth(con, event_dates, as_of, card)
    games, now = todays_rows(con, gaps, depth, event_dates, as_of)
    names = event_names(con)

    if len(now):
        for h in HORIZONS:
            now[f"p{h}"] = timing[h].predict_proba(timing_X(now))[:, 1]
        lo, mid, hi = (depth_models[a].predict(depth_X(now)) for a in (0.1, 0.5, 0.9))
        now["cut_lo"] = np.clip(np.minimum(lo, mid) - margin, 5, 95)
        now["cut_mid"] = np.clip(mid, 5, 95)
        now["cut_hi"] = np.clip(np.maximum(hi, mid) + margin, 5, 95)
    preds = now.set_index("appid")

    out, counts = [], {"forecast": 0, "on_sale": 0, "no_history": 0}
    for g in games.itertuples(index=False):
        rec = {"id": int(g.appid), "name": g.name,
               "price": dollars(g.current_price_cents), "regular": dollars(g.current_regular_cents)}
        cut_now = 0 if pd.isna(g.current_cut_percent) else float(g.current_cut_percent)
        if cut_now > 0:
            rec.update(status="on_sale", verdict="on_sale", cut_now=int(cut_now),
                       deepest_ever=clean(g.deepest_cut_ever), lowest_ever=dollars(g.lowest_price_ever_cents))
            counts["on_sale"] += 1
        elif g.appid in preds.index:
            p = preds.loc[g.appid]
            nxt = p["next_major_event"]
            nxt_iso = None if pd.isna(nxt) else pd.Timestamp(nxt).date().isoformat()
            reg = rec["regular"] or 0
            rec.update(
                status="forecast",
                verdict=verdict(p["p30"], False),
                p14=clean(p["p14"]), p30=clean(p["p30"]),
                days_since_sale=clean(p["days_waited"]),
                next_event=nxt_iso, next_event_name=names.get(nxt_iso) if nxt_iso else None,
                days_to_next_event=clean(p["days_to_next_major_event"]),
                cut_range=[clean(round(p["cut_lo"])), clean(round(p["cut_hi"]))],
                cut_likely=clean(round(p["cut_mid"])),
                price_range=[clean(reg * (1 - p["cut_hi"] / 100)), clean(reg * (1 - p["cut_lo"] / 100))],
                deepest_ever=clean(g.deepest_cut_ever),
                typical_gap_days=clean(g.typical_days_between_sales),
                recent_sales=p["recent_sales"] if isinstance(p["recent_sales"], list) else [],
            )
            counts["forecast"] += 1
        else:
            rec.update(status="no_history", verdict=None)
            counts["no_history"] += 1
        out.append(rec)

    card["coverage"] = counts
    card["upcoming_events"] = [{"date": d, "name": n} for d, n in sorted(names.items()) if d >= as_of.date().isoformat()]
    write_outputs(out, card)
    print(f"\n{counts}")


def write_outputs(records: list[dict], card: dict) -> None:
    games_dir = OUT_DIR / "games"
    games_dir.mkdir(parents=True, exist_ok=True)
    for old in games_dir.glob("*.json"):
        old.unlink()
    dump = lambda obj: json.dumps(obj, separators=(",", ":"), ensure_ascii=False)

    index = {"as_of": card["as_of"], "shards": N_SHARDS,
             "games": [[r["id"], r["name"], STATUS_CODE[r["status"]]] for r in records]}
    (OUT_DIR / "index.json").write_text(dump(index), encoding="utf-8")

    shards: dict[int, dict] = {i: {} for i in range(N_SHARDS)}
    for r in records:
        shards[r["id"] % N_SHARDS][str(r["id"])] = r
    for i, shard in shards.items():
        (games_dir / f"{i:02d}.json").write_text(dump(shard), encoding="utf-8")

    (OUT_DIR / "model_card.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
    idx_mb = (OUT_DIR / "index.json").stat().st_size / 1e6
    shard_kb = max(f.stat().st_size for f in games_dir.glob("*.json")) / 1e3
    print(f"site/data/index.json {idx_mb:.2f} MB | {N_SHARDS} shards, largest {shard_kb:.0f} KB")


if __name__ == "__main__":
    main()
