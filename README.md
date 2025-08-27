# BBC - PAIR (Paired Authentic and Inpainted References)
🚧 This repository is a placeholder for the upcoming BBC - Paired Authentic and Inpainted References (BBC-PAIR) dataset. Stay tuned!

## Prerequisites
- Docker Desktop installed and running on your machine.
- This repo checked out locally. All commands assume you run them from the repo root; examples use generic command blocks (not shell-specific).

## Quick start
1) Build the container image:
   - VS Code Task: “docker: build”
   - Or run in a terminal:
```
docker compose build
```

2) Run the full pipeline with the orchestrator:
   - Replace the URL with the dataset base directory (not a file):
```
docker compose run --rm app python Client/Run_All.py --base-url https://<your-host>/<path-to-dataset> --percent 1 --clean partial
```

This will:
- Download core files and the requested percent of archives plus the last_200 test set
- Verify MD5s, extract archives, generate OpenV7 list, download Open Images, and reconstruct BBC_PAIR

Outputs (by default) are created under the repo root:
- `tars/` – downloaded files and trimmed JSON
- `extracted/` – extracted diffs
- `OpenV7_Originals/` – Open Images originals
- `BBC_PAIR/` – final reconstructed dataset

After a successful run, your final data will be under `<root-dir>/BBC_PAIR` (or `./BBC_PAIR` if you didn't change `--root-dir`). Inside this folder you'll find:
- `BBC_PAIR.json` – all relevant information for your selected version of the dataset.
- `manifest.json` – the details that make your download unique (e.g., base URL, selected splits/percent, checksums, timestamps, and run parameters).

## About the --percent option
The percent controls how much of the main dataset (excluding the always-included `last_200` test set) is downloaded.

Valid values are those that can be composed from:
- Up to nine 10% chunks (covering the first 90%), plus
- A strict prefix of the final 10% split into 1%, then 5%, then 2%, then 2% (you can only take them in that order).

Equivalently: allowed values are 10*k + s where k ∈ {0..9} and s ∈ {0, 1, 6, 8, 10}. Examples:
- 0, 1, 6, 8, 10, 11, 16, 18, 20, 21, 26, 28, 30, …, 90, 91, 96, 98, 100

If you pass a value that isn’t composable (e.g., 92), the downloader will error and list the remainder.

## Options and common scenarios

- Partial vs full clean after reconstruction:
  - Partial (default): keeps tar files so you can reuse downloads on later runs
```
docker compose run --rm app python Client/Run_All.py --base-url https://<host>/<path> --percent 5 --clean partial
```
  - Full: also removes tar files after success
```
docker compose run --rm app python Client/Run_All.py --base-url https://<host>/<path> --percent 5 --clean full
```

- Override concurrency (downloader parallel workers):
```
docker compose run --rm app python Client/Run_All.py --base-url https://<host>/<path> --percent 10 --clean partial --workers 6
```

- Choose a different output location on the host:
  - By default, paths are created under the repo. You can choose another folder with `--root-dir`:
```
docker compose run --rm app python Client/Run_All.py --base-url https://<host>/<path> --percent 1 --clean partial --root-dir ./runs/run1
```
  - If the target is outside this repo, bind‑mount it into the container and then point `--root-dir` to that mount. One simple way is to temporarily add another volume in `docker-compose.yml`:
    - Edit the `app` service volumes and add a host path, e.g.: `- <absolute-host-path>:/host_data`
    - Then run with: `--root-dir /host_data/run1`

## Running single steps (advanced)
You can run each step individually if needed:
- Downloader: `docker compose run --rm app python Client/Downloader.py --help`
- OpenV7 list: `docker compose run --rm app python Client/OpenV7_List_Creator.py --help`
- OpenV7 download: `docker compose run --rm app python Client/OpenV7_Downloader.py --help`
- Reconstruct: `docker compose run --rm app python Client/Regenerate_Dataset.py --help`

## Troubleshooting
- Ensure the base URL points to a directory that contains the expected files (PAIR_V1.json, PAIR_V1_HASHES.json, MD5SUMS.txt, split_*.tar.gz, last_200.tar.gz).
- If you re-run with the same `--root-dir`, prefer `--clean partial` to reuse existing downloads.
- For slow or flaky networks, consider retrying with a smaller `--percent` or lower `--workers`.

# 🚀 Release Timeline
The release for this dataset will follow a month or so behind successful paper acceptance 

# 📜 License
The licensing terms will be finalized and published prior to dataset release. We aim to make the dataset available for academic and non-commercial research purposes.

## Contacts
woody.bayliss@bbc.co.uk, juil.sock@bbc.co.uk, marc.gorrizblanch@bbc.co.uk


