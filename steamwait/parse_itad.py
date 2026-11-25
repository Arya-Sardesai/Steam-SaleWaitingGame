"""Step 3b: raw ITAD history -> one tidy table, one row per past Steam price change.

Run:  python -m steamwait.parse_itad
Writes data/parsed/itad_price_log.parquet:
  appid | itad_id | changed_at | price_cents | regular_cents | cut_percent | currency
"""
import gzip
import json

import pandas as pd

from steamwait import config


def parse_record(rec: dict) -> list[dict]:
    rows = []
    for entry in rec.get("payload") or []:
        if not isinstance(entry, dict):
            continue
        deal = entry.get("deal") or {}
        price = deal.get("price") or {}
        regular = deal.get("regular") or {}
        rows.append({
            "appid": rec["appid"],
            "itad_id": rec.get("itad_id"),
            "changed_at": entry.get("timestamp"),
            "shop_id": (entry.get("shop") or {}).get("id"),
            "price_cents": price.get("amountInt"),
            "regular_cents": regular.get("amountInt"),
            "cut_percent": deal.get("cut"),
            "currency": price.get("currency"),
        })
    return rows


def main() -> None:
    rows = []
    for path in sorted(config.RAW_ITAD_DIR.rglob("*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                rows += parse_record(json.loads(line))
    if not rows:
        raise SystemExit("No ITAD history yet. Run: python -m steamwait.itad_history history")
    df = pd.DataFrame(rows)
    df["changed_at"] = pd.to_datetime(df["changed_at"], utc=True, errors="coerce")
    df = (df[df["shop_id"] == config.ITAD_STEAM_SHOP_ID]
            .drop_duplicates(["appid", "changed_at", "price_cents"])
            .sort_values(["appid", "changed_at"])
            .reset_index(drop=True))
    for c in ("price_cents", "regular_cents", "cut_percent"):
        df[c] = df[c].astype("Int64")
    config.PARSED_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(config.ITAD_LOG_PATH, index=False)

    sales = df[df["cut_percent"].fillna(0) > 0]
    print(f"{len(df):,} price changes for {df['appid'].nunique():,} games "
          f"({len(sales):,} were sale starts)")
    print(f"history goes back to {df['changed_at'].min():%Y-%m-%d}")
    print(df.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
