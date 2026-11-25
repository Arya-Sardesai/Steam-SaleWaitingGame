"""Run a SQL query against the dbt warehouse and print the result.

    python scripts/query.py "select * from dim_games limit 10"
    python scripts/query.py queries/odd_types.sql        (a .sql file works too)

Read-only, so it can't damage anything. Avoids PowerShell's quoting headaches with python -c.
"""
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent

if len(sys.argv) < 2:
    sys.exit(__doc__)
arg = sys.argv[1]
sql = Path(arg).read_text(encoding="utf-8") if arg.endswith(".sql") else " ".join(sys.argv[1:])

con = duckdb.connect(str(ROOT / "data" / "warehouse.duckdb"), read_only=True)
con.sql(sql).show(max_rows=50, max_width=200)
