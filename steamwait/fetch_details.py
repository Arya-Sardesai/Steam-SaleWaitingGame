"""Step 2: fetch full store details for every game. Resumable, runs over many days.

Pilot (do this first):   python -m steamwait.fetch_details --limit 500 --sample
Full run (scheduled):    python -m steamwait.fetch_details

- Saves RAW responses first (data/raw/appdetails/fetched=DATE/part-*.jsonl.gz),
  then marks those IDs done in data/state/appdetails_done.csv.
- Only 'ok' and 'not_found' count as done. Errors and rate limits are retried next run.
- Stops cleanly after MAX_RUN_HOURS or Ctrl+C, and never loses already-fetched data.
"""
import argparse
import csv
import datetime as dt
import gzip
import json
import time
from pathlib import Path

import httpx
import pandas as pd

from steamwait import config

DONE_STATUSES = {"ok", "not_found"}



# ---------- state ----------
def latest_universe() -> Path:
    files = sorted(config.UNIVERSE_DIR.glob("appids_*.parquet"))
    if not files:
        raise SystemExit("No universe file. Run: python -m steamwait.app_list")
    return files[-1]


def load_done() -> set[int]:
    if not config.DONE_LOG.exists():
        return set()
    done = pd.read_csv(config.DONE_LOG)
    return set(done.loc[done["status"].isin(DONE_STATUSES), "appid"].astype(int))


def append_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerows(rows)


# ---------- fetching ----------
def classify(http_status: int, body) -> str:
    """Turn one response into a status. Every outcome gets a name: no silent errors."""
    if http_status in (429, 403):
        return "rate_limited"
    if http_status != 200:
        return "error"
    if not isinstance(body, dict) or not body:
        return "error"
    entry = next(iter(body.values()))
    if not isinstance(entry, dict):
        return "error"
    return "ok" if entry.get("success") else "not_found"


def fetch_one(client: httpx.Client, appid: int) -> dict:
    params = {"appids": appid, "cc": config.COUNTRY, "l": config.LANGUAGE}
    fetched_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    try:
        r = client.get(config.APPDETAILS_URL, params=params)
        http_status = r.status_code
        try:
            body = r.json()
        except ValueError:
            body = None
    except httpx.HTTPError as e:
        return {"appid": appid, "fetched_at": fetched_at, "http_status": None,
                "status": "error", "error": f"{type(e).__name__}: {e}", "payload": None}

    status = classify(http_status, body)
    payload = body.get(str(appid)) if isinstance(body, dict) else None
    return {"appid": appid, "fetched_at": fetched_at, "http_status": http_status,
            "status": status, "error": None, "payload": payload}


# ---------- writing ----------
def flush(buffer: list[dict]) -> None:
    """Raw file first, then done-log. If we crash in between, IDs are just refetched."""
    if not buffer:
        return
    now = dt.datetime.now(dt.timezone.utc)
    out_dir = config.RAW_APPDETAILS_DIR / f"fetched={now:%Y-%m-%d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"part-{now:%H%M%S}-{buffer[0]['appid']}.jsonl.gz"
    with gzip.open(out, "wt", encoding="utf-8") as f:
        for rec in buffer:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    append_csv(config.DONE_LOG,
               [{k: rec[k] for k in ("appid", "status", "fetched_at")} for rec in buffer],
               ["appid", "status", "fetched_at"])
    buffer.clear()


# ---------- priority ----------
def prioritise(queue: pd.Series) -> pd.Series:
    """Most useful games first, so the demo gets coverage fast.

    1. for sale today AND already have IsThereAnyDeal history -> modelable the moment this lands
    2. for sale today
    3. everything else (free, unreleased, delisted)
    Inside each group: newest app IDs first, since recent games are what people search for.
    Falls back to app-ID order if the snapshot / ITAD files aren't there.
    """
    priced, has_history = set(), set()
    snaps = sorted(config.PRICES_DIR.glob("snapshot_date=*/prices.parquet"))
    if snaps:
        s = pd.read_parquet(snaps[-1], columns=["appid", "status"])
        priced = set(s.loc[s["status"] == "priced", "appid"])
    if config.ITAD_DONE_LOG.exists():
        d = pd.read_csv(config.ITAD_DONE_LOG)
        has_history = set(d.loc[d["status"] == "ok", "appid"])
    q = pd.DataFrame({"appid": queue.to_numpy()})
    q["group"] = 3
    q.loc[q["appid"].isin(priced), "group"] = 2
    q.loc[q["appid"].isin(priced) & q["appid"].isin(has_history), "group"] = 1
    print("Queue by priority group:", q["group"].value_counts().sort_index().to_dict())
    return q.sort_values(["group", "appid"], ascending=[True, False])["appid"]


# ---------- main loop ----------
def run(limit: int | None, sample: bool, hours: float, appids: list[int] | None = None) -> None:
    universe = pd.read_parquet(latest_universe())
    done = load_done()
    queue = universe.loc[~universe["appid"].isin(done), "appid"].astype(int)
    if appids:
        queue = pd.Series(appids, dtype=int)             # on demand: fetch these now, even if done before
    elif sample:
        queue = queue.sample(frac=1, random_state=42)   # random order, so a pilot isn't all 2004 games
    else:
        queue = prioritise(queue)
    queue = queue.tolist()[:limit] if limit else queue.tolist()

    print(f"Universe {len(universe):,} | done {len(done):,} | this run {len(queue):,}")
    deadline = time.monotonic() + hours * 3600
    counts: dict[str, int] = {}
    buffer: list[dict] = []
    started = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    try:
        with httpx.Client(timeout=30, headers={"User-Agent": config.USER_AGENT}) as client:
            for i, appid in enumerate(queue, 1):
                if time.monotonic() > deadline:
                    print("Time budget reached, stopping cleanly.")
                    break

                rec = fetch_one(client, appid)
                tries = 0
                while rec["status"] == "rate_limited" and tries < len(config.BACKOFF_SECONDS):
                    wait = config.BACKOFF_SECONDS[tries]
                    print(f"  rate limited on {appid}, waiting {wait}s")
                    time.sleep(wait)
                    rec = fetch_one(client, appid)
                    tries += 1

                counts[rec["status"]] = counts.get(rec["status"], 0) + 1
                buffer.append(rec)
                if len(buffer) >= config.FLUSH_EVERY:
                    flush(buffer)
                if i % 100 == 0:
                    print(f"{i:,}/{len(queue):,}  {counts}")
                time.sleep(config.SECONDS_BETWEEN_CALLS)
    except KeyboardInterrupt:
        print("\nInterrupted, saving what we have.")
    finally:
        flush(buffer)
        total = sum(counts.values())
        ok_rate = counts.get("ok", 0) + counts.get("not_found", 0)
        ok_rate = ok_rate / total if total else 0.0
        append_csv(config.RUNS_LOG, [{
            "started": started,
            "ended": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "fetched": total, **{s: counts.get(s, 0) for s in ("ok", "not_found", "rate_limited", "error")},
            "healthy_rate": round(ok_rate, 3),
            "remaining": len(universe) - len(load_done()),
        }], ["started", "ended", "fetched", "ok", "not_found", "rate_limited", "error",
             "healthy_rate", "remaining"])
        print(f"\nDone. {counts}  healthy rate {ok_rate:.1%}")
        if total and ok_rate < config.MIN_OK_RATE:
            print(f"WARNING: healthy rate below {config.MIN_OK_RATE:.0%}. Check data/state/runs.csv")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None, help="max games this run (pilot: 500)")
    p.add_argument("--sample", action="store_true", help="random order instead of appid order")
    p.add_argument("--hours", type=float, default=config.MAX_RUN_HOURS)
    p.add_argument("--appids", type=int, nargs="+", help="fetch just these games now, e.g. --appids 1478500")
    a = p.parse_args()
    run(a.limit, a.sample, a.hours, a.appids)


if __name__ == "__main__":
    main()
