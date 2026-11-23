"""Look at REAL rows from the pilot before trusting any filter.

Run after the pilot + parse:  python scripts/peek_pilot.py
Paste the output back to Chat.
"""
import pandas as pd

df = pd.read_parquet("data/parsed/appdetails.parquet")
ok = df[df["status"] == "ok"]
pd.set_option("display.width", 200, "display.max_colwidth", 60)

print(f"rows: {len(df):,}   ok: {len(ok):,}\n")
print("== status ==");  print(df["status"].value_counts().to_string(), "\n")
print("== type (expect mostly 'game') ==");  print(ok["type"].value_counts(dropna=False).to_string(), "\n")
print("== free vs paid ==");  print(ok["is_free"].value_counts(dropna=False).to_string(), "\n")
print("== currently discounted ==");  print((ok["discount_percent"].fillna(0) > 0).value_counts().to_string(), "\n")

print("== 15 random games ==")
print(ok.sample(min(15, len(ok)), random_state=1)[
    ["appid", "name", "type", "release_date_raw", "price_final_cents", "discount_percent", "genres"]
].to_string(index=False), "\n")

print("== content descriptor IDs: how often each appears ==")
ids = ok["content_descriptor_ids"].explode().dropna()
print(ids.value_counts().to_string() if len(ids) else "none", "\n")

print("== examples per descriptor ID (check which ones are adult content) ==")
for cid in sorted(ids.unique()):
    names = ok[ok["content_descriptor_ids"].apply(lambda xs: cid in list(xs))]["name"].head(5).tolist()
    print(f"  {cid}: {names}")

print("\n== odd release dates (should parse later) ==")
print(ok["release_date_raw"].dropna().sample(min(10, len(ok)), random_state=2).tolist())
