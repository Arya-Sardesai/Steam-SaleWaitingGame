"""Batching test: can Steam's appdetails return many games in one call?

Run:  python scripts/batch_test.py
No API key needed. Makes 3 requests total, 3 seconds apart.
"""
import json
import time
import urllib.request

# Well-known app IDs plus filler. Unknown IDs are fine: Steam just says success=false.
KNOWN = [620, 400, 1145360, 413150, 105600, 367520, 892970, 1091500, 730, 570]
FILLER = list(range(200010, 201910, 10))  # 190 unique arbitrary IDs, many won't exist
SAMPLE = KNOWN + FILLER                    # 200 unique IDs

URL = "https://store.steampowered.com/api/appdetails?appids={ids}&filters=price_overview&cc=ca"


def try_batch(n: int) -> None:
    ids = SAMPLE[:n]
    req = urllib.request.Request(URL.format(ids=",".join(map(str, ids))),
                                 headers={"User-Agent": "steam-salewaitinggame batch test"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.status
            body = json.loads(resp.read() or b"null")
    except Exception as e:  # report it, don't hide it
        print(f"n={n:>3}  FAILED: {type(e).__name__}: {e}")
        return

    if not isinstance(body, dict):
        print(f"n={n:>3}  HTTP {status}  body was {body!r} -> batching NOT supported")
        return

    ok = [k for k, v in body.items() if v.get("success")]
    priced = [k for k in ok if isinstance(v := body[k].get("data"), dict) and "price_overview" in v]
    print(f"n={n:>3}  HTTP {status}  entries={len(body)}  success={len(ok)}  with_price={len(priced)}")
    for k in priced[:3]:
        print("        example:", k, body[k]["data"]["price_overview"])


if __name__ == "__main__":
    for n in (50, 100, 200):
        try_batch(n)
        time.sleep(3)
    print("\nIf 'entries' matches n, batching works. Paste this output back to Chat.")
