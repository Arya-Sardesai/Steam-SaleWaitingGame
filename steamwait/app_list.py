"""Step 1: get the list of every game on Steam (games only, no DLC/software/video).

Run:  python -m steamwait.app_list
Needs STEAM_API_KEY in .env. Writes data/universe/appids_YYYY-MM-DD.parquet
"""
import datetime as dt
import sys
import time

import httpx
import pandas as pd

from steamwait import config


def fetch_app_list() -> pd.DataFrame:
    if not config.STEAM_API_KEY:
        sys.exit("STEAM_API_KEY missing. Put it in .env first.")

    rows, last_appid, page = [], 0, 0
    with httpx.Client(timeout=60, headers={"User-Agent": config.USER_AGENT}) as client:
        while True:
            page += 1
            params = {
                "key": config.STEAM_API_KEY,
                "include_games": "true",
                "include_dlc": "false",
                "include_software": "false",
                "include_videos": "false",
                "include_hardware": "false",
                "max_results": 50000,
                "last_appid": last_appid,
            }
            r = client.get(config.APP_LIST_URL, params=params)
            r.raise_for_status()
            resp = r.json().get("response", {})
            apps = resp.get("apps", [])
            rows.extend(apps)
            print(f"page {page}: +{len(apps)} apps (total {len(rows)})")

            if not resp.get("have_more_results"):
                break
            last_appid = resp.get("last_appid", apps[-1]["appid"] if apps else last_appid)
            time.sleep(1)

    df = pd.DataFrame(rows)
    if df.empty:
        sys.exit("Got zero apps back. Print r.json() and check the parameters against the docs.")
    df = df.drop_duplicates("appid").sort_values("appid").reset_index(drop=True)
    return df


def main() -> None:
    df = fetch_app_list()
    config.UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)
    out = config.UNIVERSE_DIR / f"appids_{dt.date.today():%Y-%m-%d}.parquet"
    df.to_parquet(out, index=False)
    print(f"\nSaved {len(df):,} games -> {out}")
    print(df.sample(min(10, len(df)), random_state=0).to_string(index=False))


if __name__ == "__main__":
    main()
