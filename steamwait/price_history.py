"""Step 2b: squash daily snapshots into price PERIODS (change-data capture).

Run:  python -m steamwait.price_history

100K games x 365 days is ~36M rows, but prices rarely change. This keeps one row
per price period instead:  appid | price | discount | valid_from | valid_to | last_seen
valid_to is the date the NEXT price started (exclusive). Empty = still current.
Days where a game errored are skipped, so a failed fetch never looks like a price change.
"""
import pandas as pd

from steamwait import config

STATE_COLS = ["status", "price_initial_cents", "price_final_cents", "discount_percent"]


def load_snapshots() -> pd.DataFrame:
    files = sorted(config.PRICES_DIR.glob("snapshot_date=*/prices.parquet"))
    if not files:
        raise SystemExit("No snapshots yet. Run: python -m steamwait.snapshot_prices")
    return pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)


def build_periods(snaps: pd.DataFrame) -> pd.DataFrame:
    df = snaps[snaps["status"] != "error"].copy()
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
    df = df.sort_values(["appid", "snapshot_date"]).drop_duplicates(["appid", "snapshot_date"], keep="last")

    # A new period starts whenever the price state differs from the previous observed day.
    state = df[STATE_COLS].astype("string").fillna("<NA>").agg("|".join, axis=1)
    changed = state.ne(state.groupby(df["appid"]).shift())
    df["period_id"] = changed.groupby(df["appid"]).cumsum()

    periods = (df.groupby(["appid", "period_id"], as_index=False)
                 .agg(**{c: (c, "first") for c in STATE_COLS},
                      currency=("currency", "first"),
                      valid_from=("snapshot_date", "min"),
                      last_seen=("snapshot_date", "max"),
                      days_observed=("snapshot_date", "size")))
    periods["valid_to"] = periods.groupby("appid")["valid_from"].shift(-1)
    return periods.drop(columns="period_id")[
        ["appid", "status", "currency", "price_initial_cents", "price_final_cents",
         "discount_percent", "valid_from", "valid_to", "last_seen", "days_observed"]]


def main() -> None:
    snaps = load_snapshots()
    periods = build_periods(snaps)
    config.PRICE_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    periods.to_parquet(config.PRICE_HISTORY_PATH, index=False)
    n_days = snaps["snapshot_date"].nunique()
    print(f"{len(snaps):,} snapshot rows over {n_days} days -> {len(periods):,} price periods "
          f"({len(periods) / max(len(snaps), 1):.1%} of original size)")
    print(periods.sort_values(["appid", "valid_from"]).head(10).to_string(index=False))


if __name__ == "__main__":
    main()
