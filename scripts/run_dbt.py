"""Run dbt through Python, so Windows Smart App Control can't block a dbt.exe launcher.

(Named run_dbt.py, not dbt.py: a file called dbt.py would hide the real dbt package.)

Run from the repo root, exactly like the dbt command:
    python scripts/run_dbt.py build      # seeds + models + tests, in order
    python scripts/run_dbt.py run        # models only
    python scripts/run_dbt.py test       # tests only
    python scripts/run_dbt.py docs generate
It points dbt at the dbt/ folder and at data/ automatically.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("DBT_PROJECT_DIR", str(ROOT / "dbt"))
os.environ.setdefault("DBT_PROFILES_DIR", str(ROOT / "dbt"))
os.environ.setdefault("DBT_SEND_ANONYMOUS_USAGE_STATS", "False")
os.environ.setdefault("STEAMWAIT_DATA", str(ROOT / "data").replace("\\", "/"))

from dbt.cli.main import cli  # noqa: E402  (env vars must be set first)

if __name__ == "__main__":
    sys.exit(cli())
