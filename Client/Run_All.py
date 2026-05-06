#!/usr/bin/env python3
"""
Wrapper to run the 4-step pipeline back-to-back:
    1) Downloader (Downloader.py)
    2) OpenV7 list creator (OpenV7_List_Creator.py)
    3) OpenV7 downloader (OpenV7_Downloader.py)
    4) Dataset regeneration (Regenerate_Dataset.py)

Options:
- --percent (required)
- --username and --password (or use PAIR_USERNAME/PAIR_PASSWORD environment variables)
- --base-url (optional, defaults to BBC PAIR dataset URL)
- --clean [partial|full] (default: partial)
- --root-dir PATH (default: current working directory)

Note on --root-dir:
  The path must be accessible from inside the container. Paths under the repo root
  are mounted automatically. If you need to store files elsewhere on the host,
  mount that directory into the container when running Docker.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def run_step(title: str, argv: list[str]):
    print(f"\n=== {title} ===")
    print(" ", " ".join(argv))
    proc = subprocess.run(argv)
    if proc.returncode != 0:
        print(f"Step failed: {title} (exit {proc.returncode})", file=sys.stderr)
        sys.exit(proc.returncode)


def main():
    # Intro ASCII banner
    print(
        "\n" +
        "==============================================================\n"
        "|            BBC PAIR DATASET DOWNLOADER                     |\n"
        "==============================================================\n"
    )

    ap = argparse.ArgumentParser(description="Run full pipeline with simple flags")
    ap.add_argument("--base-url", default="https://bbc-pair.datasets.bbctest01.uk/data", help="HTTP/HTTPS base URL of the dataset host (defaults to BBC PAIR dataset)")
    ap.add_argument("--percent", required=True, type=int, help="Percentage (0..100) of main dataset to download")
    ap.add_argument("--username", help="Username for authentication (can also use PAIR_USERNAME environment variable)")
    ap.add_argument("--password", help="Password for authentication (can also use PAIR_PASSWORD environment variable)")
    ap.add_argument("--clean", choices=["partial", "full"], default="partial", help="Cleanup mode: partial keeps tar files; full removes tar files")
    ap.add_argument("--quickclean", action="store_true", help="Remove tar files immediately after their extraction to save disk space")
    ap.add_argument("--root-dir", default=str(Path.cwd()), help="Root directory for outputs (tars, extracted, OpenV7_Originals, BBC_PAIR)")
    ap.add_argument("--workers", type=int, default=None, help="Override downloader parallelism (default: script’s default)")
    args = ap.parse_args()

    root = Path(args.root_dir).expanduser().resolve()
    tars = root / "tars"
    extracted = root / "extracted"
    openv7 = root / "OpenV7_Originals"
    outdir = root / "BBC_PAIR"

    # Ensure dirs exist for early steps
    tars.mkdir(parents=True, exist_ok=True)
    extracted.mkdir(parents=True, exist_ok=True)

    py = sys.executable

    # 1) Downloader
    dl_cmd = [
        py,
        "Client/Downloader.py",
        "--base-url", args.base_url,
        "--dest", str(tars),
        "--extract-dest", str(extracted),
        "--percent", str(args.percent),
    ]
    
    # Add authentication
    if args.username:
        dl_cmd += ["--username", args.username]
    if args.password:
        dl_cmd += ["--password", args.password]
    
    if args.workers is not None:
        dl_cmd += ["--workers", str(args.workers)]
    if args.quickclean:
        dl_cmd += ["--quickclean"]
    run_step("Step 1/4: Download splits", dl_cmd)

    # 2) List creator
    run_step(
        "Step 2/4: Create OpenV7 download list",
        [
            py, "Client/OpenV7_List_Creator.py",
            "--dataset-specific-json", str(tars / "PAIR_V1.trimmed.json"),
            "--download-list", str(tars / "download_list.txt"),
        ],
    )

    # 3) OpenV7 downloader
    run_step(
        "Step 3/4: Download OpenV7 images",
        [
            py, "Client/OpenV7_Downloader.py",
            "--download-list", str(tars / "download_list.txt"),
            "--openv7-download-folder", str(openv7),
        ],
    )

    # 4) Regenerate dataset
    regen_cmd = [
        py, "Client/Regenerate_Dataset.py",
        "--dataset-specific-json", str(tars / "PAIR_V1.trimmed.json"),
        "--hash-json", str(tars / "PAIR_V1_HASHES.trimmed.json"),
        "--openv7-download-folder", str(openv7),
        "--tars-dir", str(tars),
        "--diff-dir", str(extracted),
        "--reconstructed-dir", str(outdir),
    ]
    if args.clean == "full":
        regen_cmd.append("--remove-tar-files")

    run_step("Step 4/4: Regenerate dataset", regen_cmd)

    print("\nAll steps completed successfully.")
    print(f"Output dataset: {outdir}")

    print("\n")
    # Outro ASCII banner
    print(
        "\"To have exploited so great a scientific invention for the purpose and pursuit of 'entertainment' alone would have been a prostitution of its powers and an insult to the character and intelligence of the people\" - John Reith"
    )


if __name__ == "__main__":
    main()
