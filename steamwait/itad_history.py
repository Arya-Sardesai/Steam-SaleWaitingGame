"""Step 3: years of past Steam sales per game, from IsThereAnyDeal.

Probe first (no saving, ~4 requests):   python -m steamwait.itad_history probe
Stage 1, map Steam IDs -> ITAD IDs:     python -m steamwait.itad_history lookup     (~1K requests, ~10 min)
Stage 2, price history per game:        python -m steamwait.itad_history history    (resumable, ~15h total)

Both stages resume where they stopped. Ctrl+C is safe. Runs alongside the Steam
backfill without conflict: different service, different rate limit.
Raw responses are saved first (data/raw/itad_history/), parsed later by parse_itad.py.
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

LOOKUP_URL = f"{config.ITAD_BASE}/lookup/id/shop/{config.ITAD_STEAM_SHOP_ID}/v1"
HISTORY_URL = f"{config.ITAD_BASE}/games/history/v2"
DONE_STATUSES = {"ok", "empty", "not_found"}


def _client() -> httpx.Client:
    if not config.ITAD_API_KEY:
        sys.exit("ITAD_API_KEY missing from .env")
    return httpx.Client(timeout=60, headers={"User-Agent": config.USER_AGENT})


def _request(client: httpx.Client, method: str, url: str, **kw) -> httpx.Response | None:
    """One call with polite retries. Honours ITAD's Retry-After on 429."""
    kw.setdefault("params", {})["key"] = config.ITAD_API_KEY
    for attempt in range(4):
        try:
            r = client.request(method, url, **kw)
        except httpx.HTTPError as e:
            print(f"  network error: {type(e).__name__}: {e}")
            time.sleep(30)
            continue
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 60)) + 1
            print(f"  ITAD rate limit, waiting {wait}s")
            time.sleep(wait)
            continue
        if r.status_code >= 500:
            time.sleep(30 * (attempt + 1))
            continue
        return r
    return None


def _history_params(gid: str, since: str | None = config.ITAD_HISTORY_SINCE) -> dict:
    params = {"id": gid, "country": config.ITAD_COUNTRY, "shops": config.ITAD_STEAM_SHOP_ID}
    if since:
        params["since"] = since
    return params


# ---------- probe ----------
def probe() -> None:
    with _client() as c:
        r = _request(c, "POST", LOOKUP_URL, json=["app/620", "app/1145360", "app/999999999"])
        print("LOOKUP", r.status_code if r else None, r.text[:400] if r else "")
        ids = r.json() if r is not None and r.status_code == 200 else {}
        gid = ids.get("app/620")
        if not gid:
            sys.exit("Lookup didn't return an ID for Portal 2. Paste this output to Chat.")
        for label, since in (("default (no since)", None), (f"since={config.ITAD_HISTORY_SINCE}",
                                                            config.ITAD_HISTORY_SINCE)):
            r = _request(c, "GET", HISTORY_URL, params=_history_params(gid, since))
            print(f"\nHISTORY {label}: HTTP {r.status_code if r else None}")
            if r is not None and r.status_code == 200:
                log = r.json()
                sales = sum(1 for e in log if ((e.get("deal") or {}).get("cut") or 0) > 0)
                print(f"  {len(log)} price changes for Portal 2, {sales} of them sales")
                if log:
                    print("  newest:", log[0].get("timestamp"), (log[0].get("deal") or {}).get("cut"))
                    print("  oldest:", log[-1].get("timestamp"), (log[-1].get("deal") or {}).get("cut"))
            else:
                print(" ", r.text[:400] if r else "")
            time.sleep(1)


# ---------- stage 1: lookup ----------
def lookup() -> None:
    universe = pd.read_parquet(latest_universe())["appid"].astype(int)
    known = pd.read_parquet(config.ITAD_IDS_PATH) if config.ITAD_IDS_PATH.exists() else \
        pd.DataFrame({"appid": pd.Series(dtype="int64"), "itad_id": pd.Series(dtype="string")})
    todo = universe[~universe.isin(known["appid"])].tolist()
    print(f"Universe {len(universe):,} | already mapped {len(known):,} | to look up {len(todo):,}")

    new_rows = []
    try:
        with _client() as c:
            for i in range(0, len(todo), config.ITAD_LOOKUP_BATCH):
                batch = todo[i:i + config.ITAD_LOOKUP_BATCH]
                r = _request(c, "POST", LOOKUP_URL, json=[f"app/{a}" for a in batch])
                if r is None or r.status_code != 200:
                    print(f"  batch at {batch[0]} failed ({r.status_code if r else 'no response'}), "
                          "will retry next run")
                    continue
                mapping = r.json()
                new_rows += [{"appid": a, "itad_id": mapping.get(f"app/{a}")} for a in batch]
                if (i // config.ITAD_LOOKUP_BATCH) % 50 == 0:
                    print(f"  {i + len(batch):,}/{len(todo):,}")
                time.sleep(config.ITAD_SECONDS_BETWEEN_CALLS)
    except KeyboardInterrupt:
        print("\nInterrupted, saving what we have.")
    finally:
        if new_rows:
            out = pd.concat([known, pd.DataFrame(new_rows)], ignore_index=True)
            out["itad_id"] = out["itad_id"].astype("string")
            config.ITAD_IDS_PATH.parent.mkdir(parents=True, exist_ok=True)
            out.to_parquet(config.ITAD_IDS_PATH, index=False)
            print(f"Saved {len(out):,} mappings ({out['itad_id'].notna().sum():,} matched)")


# ---------- stage 2: history ----------
def _flush(buffer: list[dict]) -> None:
    if not buffer:
        return
    now = dt.datetime.now(dt.timezone.utc)
    out_dir = config.RAW_ITAD_DIR / f"fetched={now:%Y-%m-%d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    with gzip.open(out_dir / f"part-{now:%H%M%S}-{buffer[0]['appid']}.jsonl.gz", "wt",
                   encoding="utf-8") as f:
        for rec in buffer:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    append_csv(config.ITAD_DONE_LOG,
               [{k: rec[k] for k in ("appid", "status", "fetched_at")} for rec in buffer],
               ["appid", "status", "fetched_at"])
    buffer.clear()


def prioritise(ids: pd.DataFrame) -> pd.DataFrame:
    """Most useful games first, so a slow backfill still gets the important data early.

    tier 0: for sale today AND Steam's price_change_number > 0 (has had price changes)
    tier 1: price_change_number > 0 but not for sale today (free/unreleased/delisted)
    tier 2: for sale today but price_change_number == 0 (likely never discounted)
    tier 3: everything else (never priced: free or unreleased, history will be empty)
    Inside a tier: RANDOM order (fixed seed). So if you stop at any point, the games done
    so far are a fair random sample of that tier, which is what the model needs.
    """
    universe = pd.read_parquet(latest_universe())[["appid", "price_change_number"]]
    q = ids.merge(universe, on="appid", how="left")
    q["price_change_number"] = q["price_change_number"].fillna(0)

    priced = set()
    snaps = sorted(config.PRICES_DIR.glob("snapshot_date=*/prices.parquet"))
    if snaps:
        s = pd.read_parquet(snaps[-1], columns=["appid", "status"])
        priced = set(s.loc[s["status"] == "priced", "appid"])
    else:
        print("No local price snapshot, so tiers use price_change_number only. "
              "Get one with: python -m steamwait.hf_sync download")

    changed = q["price_change_number"] > 0
    for_sale = q["appid"].isin(priced)
    q["tier"] = 3
    q.loc[~changed & for_sale, "tier"] = 2
    q.loc[changed & ~for_sale, "tier"] = 1
    q.loc[changed & for_sale, "tier"] = 0
    return q.sample(frac=1, random_state=42).sort_values("tier", kind="stable")


def history(limit: int | None, hours: float, max_tier: int, appids: list[int] | None = None) -> None:
    if not config.ITAD_IDS_PATH.exists():
        sys.exit("Run the lookup stage first: python -m steamwait.itad_history lookup")
    ids = pd.read_parquet(config.ITAD_IDS_PATH).dropna(subset=["itad_id"])
    done = set()
    if config.ITAD_DONE_LOG.exists():
        d = pd.read_csv(config.ITAD_DONE_LOG)
        done = set(d.loc[d["status"].isin(DONE_STATUSES), "appid"].astype(int))
    if appids:   # on demand: fetch these now, even if done before or in a skipped tier
        queue = prioritise(ids[ids["appid"].isin(appids)])
        missing = sorted(set(appids) - set(queue["appid"]))
        if missing:
            print(f"No IsThereAnyDeal match for {missing} (run the lookup stage first, or ITAD doesn't list it)")
    else:
        queue = prioritise(ids[~ids["appid"].isin(done)])
        queue = queue[queue["tier"] <= max_tier]
    if limit:
        queue = queue.head(limit)
    print(f"Matched games {len(ids):,} | done {len(done):,} | this run {len(queue):,}")
    print("Queue by priority tier:", queue["tier"].value_counts().sort_index().to_dict())

    deadline = time.monotonic() + hours * 3600
    counts: dict[str, int] = {}
    buffer: list[dict] = []
    try:
        with _client() as c:
            for n, (appid, gid) in enumerate(zip(queue["appid"], queue["itad_id"]), 1):
                if time.monotonic() > deadline:
                    print("Time budget reached, stopping cleanly.")
                    break
                fetched_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
                r = _request(c, "GET", HISTORY_URL, params=_history_params(gid))
                if r is None:
                    status, payload = "error", None
                elif r.status_code == 404:
                    status, payload = "not_found", None
                elif r.status_code != 200:
                    status, payload = "error", None
                else:
                    try:
                        payload = r.json()
                        status = "ok" if payload else "empty"
                    except ValueError:
                        status, payload = "error", None
                counts[status] = counts.get(status, 0) + 1
                buffer.append({"appid": int(appid), "itad_id": gid, "fetched_at": fetched_at,
                               "http_status": r.status_code if r is not None else None,
                               "status": status, "payload": payload})
                if len(buffer) >= config.FLUSH_EVERY:
                    _flush(buffer)
                if n % 100 == 0:
                    print(f"{n:,}/{len(queue):,}  {counts}")
                time.sleep(config.ITAD_SECONDS_BETWEEN_CALLS)
    except KeyboardInterrupt:
        print("\nInterrupted, saving what we have.")
    finally:
        _flush(buffer)
        print(f"\nDone this run. {counts}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("stage", choices=["probe", "lookup", "history"])
    p.add_argument("--limit", type=int, help="history: only N games (pilot: 200)")
    p.add_argument("--hours", type=float, default=config.MAX_RUN_HOURS)
    p.add_argument("--max-tier", type=int, default=1,
                   help="history: skip tiers above this (default 1 skips never-priced games)")
    p.add_argument("--appids", type=int, nargs="+", help="history: fetch just these games now")
    a = p.parse_args()
    if a.stage == "probe":
        probe()
    elif a.stage == "lookup":
        lookup()
    else:
        history(a.limit, a.hours, a.max_tier, a.appids)


if __name__ == "__main__":
    main()
