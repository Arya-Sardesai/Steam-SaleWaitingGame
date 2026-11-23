"""Step 3: turn raw appdetails JSON into one clean table (latest record per game).

Run:  python -m steamwait.parse_details
Writes data/parsed/appdetails.parquet. Re-run any time: it always rebuilds from raw.
"""
import gzip
import json

import pandas as pd

from steamwait import config


def parse_record(rec: dict) -> dict:
    """One raw line -> one flat row. Missing fields become None, never crash."""
    payload = rec.get("payload") or {}
    d = payload.get("data") or {}
    price = d.get("price_overview") or {}
    release = d.get("release_date") or {}
    descriptors = d.get("content_descriptors") or {}

    return {
        "appid": rec["appid"],
        "fetched_at": rec.get("fetched_at"),
        "status": rec.get("status"),
        "name": d.get("name"),
        "type": d.get("type"),
        "is_free": d.get("is_free"),
        "required_age": _to_int(d.get("required_age")),
        "release_date_raw": release.get("date"),
        "coming_soon": release.get("coming_soon"),
        "developers": d.get("developers") or [],
        "publishers": d.get("publishers") or [],
        "genres": [g.get("description") for g in d.get("genres") or []],
        "categories": [c.get("description") for c in d.get("categories") or []],
        "content_descriptor_ids": descriptors.get("ids") or [],
        "content_notes": descriptors.get("notes"),
        "currency": price.get("currency"),
        "price_initial_cents": price.get("initial"),
        "price_final_cents": price.get("final"),
        "discount_percent": price.get("discount_percent"),
        "metacritic": (d.get("metacritic") or {}).get("score"),
        "recommendations": (d.get("recommendations") or {}).get("total"),
    }


def _to_int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def load_raw() -> pd.DataFrame:
    rows = []
    for path in sorted(config.RAW_APPDETAILS_DIR.rglob("*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                rows.append(parse_record(json.loads(line)))
    return pd.DataFrame(rows)


def main() -> None:
    df = load_raw()
    if df.empty:
        raise SystemExit("No raw files yet. Run the fetch first.")
    df = (df.sort_values("fetched_at")
            .drop_duplicates("appid", keep="last")     # refetches: keep the newest
            .reset_index(drop=True))
    config.PARSED_DIR.mkdir(parents=True, exist_ok=True)
    out = config.PARSED_DIR / "appdetails.parquet"
    df.to_parquet(out, index=False)
    print(f"Saved {len(df):,} games -> {out}")
    print(df["status"].value_counts().to_string())


if __name__ == "__main__":
    main()
