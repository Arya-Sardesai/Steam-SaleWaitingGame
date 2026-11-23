"""All constants in one place. Change things here, not inside the scripts."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- Keys (from .env locally, from repo secrets in GitHub Actions) ---
STEAM_API_KEY = os.getenv("STEAM_API_KEY", "")
ITAD_API_KEY = os.getenv("ITAD_API_KEY", "")
HF_TOKEN = os.getenv("HF_TOKEN", "")

# --- Paths (everything under data/ is gitignored) ---
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
UNIVERSE_DIR = DATA / "universe"
RAW_APPDETAILS_DIR = DATA / "raw" / "appdetails"
PARSED_DIR = DATA / "parsed"
STATE_DIR = DATA / "state"
DONE_LOG = STATE_DIR / "appdetails_done.csv"
RUNS_LOG = STATE_DIR / "runs.csv"

# --- Steam endpoints ---
APP_LIST_URL = "https://api.steampowered.com/IStoreService/GetAppList/v1/"
APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
COUNTRY = "ca"        # prices in CAD
LANGUAGE = "english"

# --- Politeness / rate limits ---
SECONDS_BETWEEN_CALLS = 1.5          # ~200 calls per 5 min, Steam's store limit
BACKOFF_SECONDS = [60, 120, 300]     # waits after a 429/403, then give up on that ID for this run
MAX_RUN_HOURS = 5.5                  # stop cleanly before this
FLUSH_EVERY = 100                    # write a raw part file every N records
USER_AGENT = "Steam-SaleWaitingGame (portfolio project; github.com/Arya-Sardesai)"

# --- Quality gate ---
MIN_OK_RATE = 0.90   # a run below this is flagged as unhealthy

# --- Step 2: daily price snapshot ---
PRICE_BATCH_SIZE = 200               # confirmed working in batch_test.py (Nov 23)
PRICES_DIR = DATA / "prices"         # prices/snapshot_date=YYYY-MM-DD/prices.parquet
RAW_PRICES_DIR = DATA / "raw" / "prices"
PRICE_HISTORY_PATH = DATA / "prices" / "price_history.parquet"
PRICE_RUNS_LOG = STATE_DIR / "price_runs.csv"
MIN_PRICE_COVERAGE = 0.95            # share of games that must get an answer, or the run fails

# --- Hugging Face dataset (where Actions stores data, since it has no disk between runs) ---
HF_DATASET_REPO = os.getenv("HF_DATASET_REPO") or "Arya2305/steam-salewaitinggame"
