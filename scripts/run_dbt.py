"""Run dbt through Python, so Windows Smart App Control can't block a dbt.exe launcher.

(Named run_dbt.py, not dbt.py: a file called dbt.py would hide the real dbt package.)

Use it exactly like the dbt command:
    python scripts/run_dbt.py --version
    python scripts/run_dbt.py run
    python scripts/run_dbt.py test
    python scripts/run_dbt.py build
"""
import sys

from dbt.cli.main import cli

if __name__ == "__main__":
    sys.exit(cli())
