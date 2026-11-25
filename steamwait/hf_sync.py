"""Move data between this machine and the Hugging Face dataset repo.

Upload today's snapshot (Actions does this):   python -m steamwait.hf_sync upload
Download everything to your PC for analysis:   python -m steamwait.hf_sync download
Is today's snapshot already uploaded? (Actions):  python -m steamwait.hf_sync exists

Needs HF_TOKEN (write access) in .env or repo secrets, and the dataset repo to exist.
"""
import argparse
import datetime as dt

from huggingface_hub import HfApi, snapshot_download

from steamwait import config


def upload(date: str) -> None:
    api = HfApi(token=config.HF_TOKEN)
    for local in (config.PRICES_DIR / f"snapshot_date={date}",
                  config.RAW_PRICES_DIR / f"snapshot_date={date}"):
        if not local.exists():
            raise SystemExit(f"Nothing to upload at {local}")
        api.upload_folder(folder_path=str(local),
                          path_in_repo=str(local.relative_to(config.DATA)).replace("\\", "/"),
                          repo_id=config.HF_DATASET_REPO, repo_type="dataset",
                          commit_message=f"prices {date}")
    universe = sorted(config.UNIVERSE_DIR.glob("appids_*.parquet"))
    if universe:
        api.upload_file(path_or_fileobj=str(universe[-1]),
                        path_in_repo=f"universe/{universe[-1].name}",
                        repo_id=config.HF_DATASET_REPO, repo_type="dataset",
                        commit_message=f"universe {date}")
    print(f"Uploaded {date} to {config.HF_DATASET_REPO}")


def exists(date: str) -> None:
    """Prints exists=true/false. In GitHub Actions, also writes it as a step output."""
    import os
    found = HfApi(token=config.HF_TOKEN).file_exists(
        repo_id=config.HF_DATASET_REPO, repo_type="dataset",
        filename=f"prices/snapshot_date={date}/prices.parquet")
    line = f"exists={'true' if found else 'false'}"
    print(f"{date}: {line}")
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(line + "\n")


def download() -> None:
    path = snapshot_download(repo_id=config.HF_DATASET_REPO, repo_type="dataset",
                             token=config.HF_TOKEN or None, local_dir=str(config.DATA),
                             allow_patterns=["prices/*", "universe/*"])
    print(f"Downloaded to {path}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["upload", "download", "exists"])
    p.add_argument("--date", default=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"))
    a = p.parse_args()
    {"upload": lambda: upload(a.date), "download": download,
     "exists": lambda: exists(a.date)}[a.action]()


if __name__ == "__main__":
    main()
