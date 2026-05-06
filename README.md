# BBC - PAIR (Paired Authentic and Inpainted References) DATASET DOWNLOADER
## Prerequisites
#### <u>Docker</u>
https://www.docker.com/
#### <u> Username and password </u> 
To access this dataset please visit https://bbc-pair.datasets.bbctest01.uk/ and follow the process there to recieve a username and password to facilitate downloading of the dataset
#### <u> Dataset information </u>
The download process will take a while, in the meantime familiarise yourself with the dataset [here](https://bbc-pair.datasets.bbctest01.uk/dataset_information.html).

If downloading the full dataset you will need 1.2TB of storage space available. The final dataset will only take up 450GB but the reconstruction process requires more space.

You can add --quickclean when calling Run_All.py to reduce this by 500GB (down to 600GB total) but be warned this removes tar files as soon as extraction is complete.

## Quick start
1) Build the container image:
   - VS Code Task: “docker: build”
   - Or run in a terminal:
    ```
    docker compose build
    ```

2) Set up authentication (choose one method):
   - Environment variables (Linux/macOS):
   ```bash
   export PAIR_USERNAME=your_username
   export PAIR_PASSWORD=your_password
   ```
   - Environment variables (PowerShell/Windows):
   ```powershell
   $env:PAIR_USERNAME="your_username"
   $env:PAIR_PASSWORD="your_password"
   ```
   - Or pass credentials as command arguments (see examples below)

3) Run the full pipeline with the orchestrator to download 1% of the dataset:
   - With environment variables:
   ```
   docker compose run --rm app python Client/Run_All.py --percent 1 --clean partial
   ```
   - Or with command line credentials:
   ```
   docker compose run --rm app python Client/Run_All.py --percent 1 --clean partial --username your_username --password your_password
   ```
   Optionally add --quickclean to the command to immediately clear the tar files when downloading larger portions of the dataset.


This will:
- Download core files and the requested percent of archives plus the last_200 test set from the BBC PAIR dataset
- Verify MD5s, extract archives, generate OpenV7 list, download Open Images, and reconstruct BBC_PAIRed References)

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

**Note:** The dataset is very large due to having to reconstruct images **Optionally add --quickclean to the command to immediately clear the tar files when downloading larger portions of the dataset.**

- Partial vs full clean after reconstruction:
  - Partial (default): keeps tar files so you can reuse downloads on later runs
```
docker compose run --rm app python Client/Run_All.py --percent 5 --clean partial --username your_username --password your_password
```
  - Full: also removes tar files after success
```
docker compose run --rm app python Client/Run_All.py --percent 5 --clean full --username your_username --password your_password
```

- Override concurrency (downloader parallel workers):
```
docker compose run --rm app python Client/Run_All.py --percent 10 --clean partial --workers 6 --username your_username --password your_password
```

- Choose a different output location on the host:
  - By default, paths are created under the repo. You can choose another folder with `--root-dir`:
```
docker compose run --rm app python Client/Run_All.py --percent 1 --clean partial --root-dir ./runs/run1 --username your_username --password your_password
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
- Ensure you have valid authentication credentials for the BBC PAIR dataset
- If authentication fails, verify your username and password are correct
- The default URL points to the BBC PAIR dataset at `https://bbc-pair.datasets.bbctest01.uk/data` which contains the expected files (PAIR_V1.json, PAIR_V1_HASHES.json, MD5SUMS.txt, split_*.tar.gz, last_200.tar.gz)
- If you re-run with the same `--root-dir`, prefer `--clean partial` to reuse existing downloads
- For slow or flaky networks, consider retrying with a smaller `--percent` or lower `--workers`

## Testing Authentication
You can test your credentials without downloading the full dataset:

**Linux/macOS:**
```bash
# Test with environment variables
export PAIR_USERNAME=your_username
export PAIR_PASSWORD=your_password
docker compose run --rm app python Client/Downloader.py --dest ./test --percent 0 --dry-run
```

**PowerShell/Windows:**
```powershell
# Test with environment variables
$env:PAIR_USERNAME="your_username"
$env:PAIR_PASSWORD="your_password"
docker compose run --rm app python Client/Downloader.py --dest ./test --percent 0 --dry-run
```

**Any platform with command line arguments:**
```bash
docker compose run --rm app python Client/Downloader.py --dest ./test --percent 0 --dry-run --username your_username --password your_password
```

The `--dry-run` flag will show what would be downloaded without actually downloading anything.

## Quick PyTorch DataLoader example (optional)
If you only want to try loading a small batch from the reconstructed dataset using PyTorch, there's a minimal example under `example_data_loader/` that is intentionally separate from the main repo requirements.

Windows PowerShell quickstart:

1. Create and activate a virtual environment
  ```powershell
  cd example_data_loader
  python -m venv .venv
  .\.venv\Scripts\Activate.ps1
  ```

2. Install minimal deps for the example
  ```powershell
  pip install -r requirements-example.txt
  ```

3. Point the example to your data paths (adjust if needed)
  ```powershell
  $env:BBC_PAIR_JSON="\path\to\BBC_PAIR.json"
  $env:BBC_PAIR_IMAGES="\path\to\BBC_PAIR"
  ```

4. Run the example
  ```powershell
  python pytorch_dataloader_example.py
  ```

Notes:
- The example will report the dataset size, build a single batch, and print the tensor shape and a few sample fields.
- `requirements-example.txt` is intentionally minimal and not used by the main pipeline.

# 📜 License
Please visit <https://bbc-pair.datasets.bbctest01.uk/> to see information with respect to licencing

## Contacts
woody.bayliss@bbc.co.uk, juil.sock@bbc.co.uk, marc.gorrizblanch@bbc.co.uk



