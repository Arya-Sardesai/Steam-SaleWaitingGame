"""Feature building shared by the notebooks AND predict.py.

Keeping this in one place means the website's predictions are built exactly the way the
models were evaluated (no "training/serving skew"). Only needs pandas + numpy + duckdb, so it
runs on Windows and in WSL alike.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CHECK_DAYS = (0, 7, 14, 30, 60, 90)

TIMING_NUMERIC = [
    "days_waited", "days_to_next_major_event", "days_since_major_event",
    "regular_dollars", "prev_sale_cut", "prev_sale_length_days", "prev_gap_days",
    "game_age_years", "after_sale_number",
]
TIMING_CATEGORICAL = ["check_month"]

DEPTH_FEATURES = [
    "last_cut", "median_last3_cut", "max_cut_so_far", "n_prev_sales", "days_since_prev_sale",
    "regular_dollars", "is_major_event_sale", "days_since_major_event", "game_age_years",
    "start_month",
]


# ---------- loading ----------
def load_event_dates(con) -> np.ndarray:
    """Start dates of big platform sales: detected in the data (past) + announced (future)."""
    df = con.sql("""
        select start_date from fct_platform_sale_days where is_major_event
        union select start_date from steam_events where event_type = 'seasonal'
    """).df()
    return np.sort(pd.to_datetime(df["start_date"]).to_numpy(dtype="datetime64[ns]"))


def load_gaps(con) -> pd.DataFrame:
    gaps = con.sql("""
        select g.*, d.name,
               date_diff('day', d.release_date, g.gap_start) / 365.25 as game_age_years
        from fct_sale_gaps g
        join dim_games d using (appid)
        where d.is_modelable
    """).df()
    gaps["gap_start"] = pd.to_datetime(gaps["gap_start"])
    gaps["regular_dollars"] = gaps["regular_cents"] / 100
    return gaps


# ---------- calendar ----------
def add_calendar_features(df: pd.DataFrame, date_col: str, event_dates: np.ndarray,
                          prefix: str = "") -> pd.DataFrame:
    """Days to the next big sale and days since the last one, as of df[date_col]."""
    d = pd.to_datetime(df[date_col]).to_numpy(dtype="datetime64[ns]")
    nat = np.datetime64("NaT", "ns")
    nxt = np.searchsorted(event_dates, d, side="right")
    next_evt = np.where(nxt < len(event_dates), event_dates[np.minimum(nxt, len(event_dates) - 1)], nat)
    prv = nxt - 1
    prev_evt = np.where(prv >= 0, event_dates[np.maximum(prv, 0)], nat)
    df[f"{prefix}days_to_next_major_event"] = (next_evt - d) / np.timedelta64(1, "D")
    df[f"{prefix}days_since_major_event"] = (d - prev_evt) / np.timedelta64(1, "D")
    df[f"{prefix}next_major_event"] = pd.to_datetime(next_evt)
    return df


# ---------- timing rows (notebooks 02/03) ----------
def build_checkins(gaps: pd.DataFrame, event_dates: np.ndarray, horizon: int,
                   check_days=CHECK_DAYS) -> pd.DataFrame:
    """One row per (gap, check-in day) while the game is still waiting.

    Label: does a sale start within `horizon` days of the check-in? Rows where the gap was
    censored inside the window are dropped (answer unknown).
    """
    rows = []
    for t in check_days:
        g = gaps[gaps["duration_days"] > t].copy()
        g["days_waited"] = t
        g["check_date"] = g["gap_start"] + pd.to_timedelta(t, unit="D")
        days_left = g["duration_days"] - t
        g["label"] = (g["event_observed"] & (days_left <= horizon)).astype(int)
        known = g["event_observed"] | (days_left > horizon)
        rows.append(g[known])
    df = pd.concat(rows, ignore_index=True)
    df = add_calendar_features(df, "check_date", event_dates)
    df["check_month"] = df["check_date"].dt.month
    return df


def add_game_weights(df: pd.DataFrame) -> pd.DataFrame:
    """Every game gets equal total weight, so constant discounters don't dominate."""
    df["weight"] = 1.0 / df.groupby("appid")["appid"].transform("size")
    return df


# ---------- depth rows (notebook 03) ----------
def build_depth_rows(con, event_dates: np.ndarray) -> pd.DataFrame:
    """One row per sale, with features known when the sale STARTS. Target: max_cut_percent."""
    sales = con.sql("""
        select s.appid, s.started_at, s.start_date, s.max_cut_percent, s.regular_cents,
               s.days_since_prev_sale, s.is_left_censored, d.name, d.release_date
        from fct_sale_events s
        join dim_games d using (appid)
        where d.is_modelable
        order by s.appid, s.started_at
    """).df()
    sales["start_date"] = pd.to_datetime(sales["start_date"])
    by_game = sales.groupby("appid")["max_cut_percent"]
    sales["last_cut"] = by_game.shift(1)
    sales["median_last3_cut"] = by_game.transform(lambda s: s.shift(1).rolling(3, min_periods=1).median())
    sales["max_cut_so_far"] = by_game.transform(lambda s: s.shift(1).cummax())
    sales["n_prev_sales"] = sales.groupby("appid").cumcount()
    sales["regular_dollars"] = sales["regular_cents"] / 100
    sales["game_age_years"] = (sales["start_date"] - pd.to_datetime(sales["release_date"])).dt.days / 365.25
    sales["start_month"] = sales["start_date"].dt.month

    # did this sale start with a big platform sale? (within 3 days after its start)
    sales = add_calendar_features(sales, "start_date", event_dates)
    on_event = sales["days_since_major_event"].le(3)
    sales["is_major_event_sale"] = on_event.astype(int)
    # censored starts: real start unknown, so its calendar position is unknown too
    return sales[~sales["is_left_censored"]].reset_index(drop=True)
