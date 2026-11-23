"""Step 2: today's price for every game, 200 games per request.

Full run:          python -m steamwait.snapshot_prices
Keyless test now:  python -m steamwait.snapshot_prices --appids 620,400,1145360,413150,105600

Writes data/prices/snapshot_date=YYYY-MM-DD/prices.parquet (one row per game)
and the raw responses to data/raw/prices/snapshot_date=YYYY-MM-DD/.
Exits with an error if too few games got an answer, so GitHub Actions emails you.
"""
import argparse
import datetime as dt
import gzip
import json
import sys
import time

import httpx
import pandas as pd

from steamwait import config
from steamwait.fetch_details import append_csv, latest_universe

FIELDS = ["snapshot_date", "fetched_at", "appid", "status", "currency",
          "price_initial_cents", "price_final_cents", "discount_percent"]


def parse_batch(body, appids: list[int], snapshot_date: str, fetched_at: str) -> list[dict]:
    """One batch response -> one row per requested appid. Missing IDs become 'error'."""
    rows = []
    body = body if isinstance(body, dict) else {}
    for appid in appids:
        entry = body.get(str(appid))
        row = {"snapshot_date": snapshot_date, "fetched_at": fetched_at, "appid": appid,
               "status": "error", "currency": None, "price_initial_cents": None,
               "price_final_cents": None, "discount_percent": None}
        if isinstance(entry, dict):
            if not entry.get("success"):
                row["status"] = "not_found"
            else:
                data = entry.get("data")
                price = data.get("price_overview") if isinstance(data, dict) else None
                if price:
                    row.update(status="priced", currency=price.get("currency"),
                               price_initial_cents=price.get("initial"),
                               price_final_cents=price.get("final"),
                               discount_percent=price.get("discount_percent"))
                else:
                    row["status"] = "no_price"   # free, unreleased, or not sold in Canada
        rows.append(row)
    return rows


def fetch_batch(client: httpx.Client, appids: list[int]):
    """Returns (http_status, body). Retries with backoff on rate limits."""
    params = {"appids": ",".join(map(str, appids)), "filters": "price_overview",
              "cc": config.COUNTRY, "l": config.LANGUAGE}
    for attempt in range(len(config.BACKOFF_SECONDS) + 1):
        try:
            r = client.get(config.APPDETAILS_URL, params=params)
        except httpx.HTTPError as e:
            print(f"  network error: {type(e).__name__}: {e}")
            status, body = None, None
        else:
            status = r.status_code
            try:
                body = r.json()
            except ValueError:
                body = None
            if status == 200 and isinstance(body, dict):
                return status, body
        if attempt < len(config.BACKOFF_SECONDS):
            wait = config.BACKOFF_SECONDS[attempt]
            print(f"  batch starting {appids[0]}: HTTP {status}, waiting {wait}s")
            time.sleep(wait)
    return status, body


def run(appids: list[int], snapshot_date: str) -> pd.DataFrame:
    fetched_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    raw_dir = config.RAW_PRICES_DIR / f"snapshot_date={snapshot_date}"
    raw_dir.mkdir(parents=True, exist_ok=True)
    batches = [appids[i:i + config.PRICE_BATCH_SIZE]
               for i in range(0, len(appids), config.PRICE_BATCH_SIZE)]
    print(f"{len(appids):,} games in {len(batches):,} batches")

    rows: list[dict] = []
    with httpx.Client(timeout=60, headers={"User-Agent": config.USER_AGENT}) as client, \
            gzip.open(raw_dir / "responses.jsonl.gz", "wt", encoding="utf-8") as raw:
        for i, batch in enumerate(batches, 1):
            status, body = fetch_batch(client, batch)
            raw.write(json.dumps({"fetched_at": fetched_at, "http_status": status,
                                  "appids": batch, "body": body}) + "\n")
            rows.extend(parse_batch(body, batch, snapshot_date, fetched_at))
            if i % 50 == 0:
                print(f"  {i:,}/{len(batches):,} batches")
            time.sleep(config.SECONDS_BETWEEN_CALLS)

    df = pd.DataFrame(rows, columns=FIELDS)
    for col in ("price_initial_cents", "price_final_cents", "discount_percent"):
        df[col] = df[col].astype("Int64")
    out_dir = config.PRICES_DIR / f"snapshot_date={snapshot_date}"
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_dir / "prices.parquet", index=False)   # rerun same day = overwrite
    return df


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--appids", help="comma-separated IDs to test without a universe file")
    p.add_argument("--limit", type=int, help="only the first N games of the universe")
    a = p.parse_args()

    if a.appids:
        appids = [int(x) for x in a.appids.split(",") if x.strip()]
    else:
        appids = pd.read_parquet(latest_universe())["appid"].astype(int).tolist()
    if a.limit:
        appids = appids[:a.limit]

    snapshot_date = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    started = time.monotonic()
    df = run(appids, snapshot_date)

    counts = df["status"].value_counts().to_dict()
    coverage = 1 - counts.get("error", 0) / len(df) if len(df) else 0.0
    on_sale = int((df["discount_percent"].fillna(0) > 0).sum())
    append_csv(config.PRICE_RUNS_LOG, [{
        "snapshot_date": snapshot_date, "games": len(df),
        **{s: counts.get(s, 0) for s in ("priced", "no_price", "not_found", "error")},
        "on_sale": on_sale, "coverage": round(coverage, 4),
        "minutes": round((time.monotonic() - started) / 60, 1),
    }], ["snapshot_date", "games", "priced", "no_price", "not_found", "error",
         "on_sale", "coverage", "minutes"])

    print(f"\n{snapshot_date}: {counts} | on sale: {on_sale:,} | coverage {coverage:.1%}")
    print(df[df["status"] == "priced"].head(5).to_string(index=False))
    if coverage < config.MIN_PRICE_COVERAGE:
        sys.exit(f"FAILED: coverage {coverage:.1%} below {config.MIN_PRICE_COVERAGE:.0%}")


if __name__ == "__main__":
    main()
