#!/usr/bin/env python3
"""
Dataset downloader for a static HTTP/HTTPS base URL exposing the dataset files.

Features
- Choose any percentage 0..100 that can be made from:
    * 9 x 10% splits (split_01_10pct ... split_09_10pct) covering the first 90%
    * final 10% broken into: 1%, 5%, 2%_a, 2%_b  (in that strict order)
- Always downloads `last_200.tar.gz` (test set) and core metadata files
    (PAIR_V1.json, PAIR_V1_HASHES.json, MD5SUMS.txt)
- Verifies MD5 checksums from MD5SUMS.txt (optional)
- Produces:
        dest/manifest.json              (what was downloaded + index ranges)
        dest/PAIR_V1.trimmed.json       (PAIR_V1 pruned to downloaded entries ∪ last_200)
        dest/checksums_report.json      (per-file MD5 verification results)

Usage:
    python 11Downloader_test.py \
            --base-url https://your-host/path \
            --dest /data/dataset \
            --percent 37

Notes
- --base-url is required and should NOT include the filename (ends at directory path).
- If PAIR_V1.json already exists in --dest, it will be reused to build trimmed output.
"""

from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from typing import Dict, List, Tuple
import shutil
import tarfile
import requests
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

# ----------------------------
# Constants describing filenames available at the base URL
# ----------------------------
CORE_FILES = [
    "PAIR_V1.json",
    "PAIR_V1_HASHES.json",
    "MD5SUMS.txt",
]
TEN_PCT_SPLITS = [f"split_{i:02d}_10pct.tar.gz" for i in range(1, 10)]  # 01..09
LAST10_SPLITS_IN_ORDER = [
    ("last10pct_1pct.tar.gz", 1),
    ("last10pct_5pct.tar.gz", 5),
    ("last10pct_2pct_a.tar.gz", 2),
    ("last10pct_2pct_b.tar.gz", 2),
]
ALWAYS_FILES = ["last_200.tar.gz"]

ALL_KNOWN_ARCHIVES = TEN_PCT_SPLITS + [s for (s, _) in LAST10_SPLITS_IN_ORDER] + ALWAYS_FILES

# ----------------------------
# S3 helpers
# ----------------------------
def download_one_http(base_url: str, name: str, dest_path: Path, overwrite: bool = False,
                      file_bar: tqdm | None = None, total_bar: tqdm | None = None,
                      retries: int = 3, backoff: float = 2.0):
    """Download a single file with retry + exponential backoff, updating bars.

    On each failed attempt (network error, non-200, size mismatch) retries until attempts exhausted.
    Partial file is removed before retry. Raises RuntimeError after final failure.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    url = base_url.rstrip('/') + '/' + name
    if dest_path.exists() and not overwrite:
        if file_bar and file_bar.total and file_bar.n < file_bar.total:
            remaining = file_bar.total - file_bar.n
            file_bar.update(remaining)
            if total_bar:
                total_bar.update(remaining)
        return name

    attempt = 0
    last_err: Exception | None = None
    while attempt <= retries:
        if attempt > 0:
            sleep_s = min(backoff * (2 ** (attempt - 1)), 60)
            time.sleep(sleep_s)
        try:
            if dest_path.exists():
                try:
                    dest_path.unlink()
                except Exception:
                    pass
            with requests.get(url, stream=True, timeout=180) as r:
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}")
                length = int(r.headers.get('Content-Length') or 0)
                if file_bar and length and (file_bar.total is None):
                    file_bar.total = length
                written = 0
                with dest_path.open('wb') as f:
                    for chunk in r.iter_content(chunk_size=1024 * 256):
                        if not chunk:
                            continue
                        f.write(chunk)
                        written += len(chunk)
                        if file_bar:
                            file_bar.update(len(chunk))
                        if total_bar:
                            total_bar.update(len(chunk))
                if length and written != length:
                    raise RuntimeError(f"Size mismatch wrote {written} expected {length}")
                if file_bar and file_bar.total and file_bar.n < file_bar.total:
                    delta = file_bar.total - file_bar.n
                    file_bar.update(delta)
                    if total_bar:
                        total_bar.update(delta)
                return name
        except Exception as e:  # noqa: BLE001
            last_err = e
            attempt += 1
            continue
    raise RuntimeError(f"Failed to download {url} after {retries+1} attempts: {last_err}")


def safe_extract_tar(archive: Path, extract_root: Path, overwrite: bool = False,
                     file_bar: tqdm | None = None, total_bar: tqdm | None = None):
    """Extract a .tar.gz archive to extract_root updating per-archive and overall bars.

    Strips leading 'data/BBC_PAIR_DIFF/' so extracted structure is flattened.
    Guards against path traversal; returns archive name.
    """
    extract_root.mkdir(parents=True, exist_ok=True)
    processed = 0
    try:
        with tarfile.open(archive, 'r:gz') as tf:
            members = tf.getmembers()
            for m in members:
                # Normalize relative path
                rel_name = m.name.lstrip('./')
                parts = rel_name.split('/')
                if len(parts) >= 2 and parts[0] == 'data' and parts[1] == 'BBC_PAIR_DIFF':
                    parts = parts[2:]
                rel_name = '/'.join([p for p in parts if p not in ('', '.')])
                if not rel_name:
                    processed += 1
                    if file_bar:
                        file_bar.update(1)
                    if total_bar:
                        total_bar.update(1)
                    continue
                if any(p == '..' for p in parts):
                    raise RuntimeError(f"Blocked path traversal component in {archive.name}: {m.name}")
                target_path = extract_root / rel_name
                target_resolved = target_path.resolve()
                if not str(target_resolved).startswith(str(extract_root.resolve())):
                    raise RuntimeError(f"Blocked path traversal attempt: {m.name}")
                if m.isdir():
                    target_path.mkdir(parents=True, exist_ok=True)
                else:
                    if target_path.exists() and not overwrite:
                        pass
                    else:
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        src = tf.extractfile(m)
                        if src is not None:
                            with src, open(target_path, 'wb') as out_f:
                                shutil.copyfileobj(src, out_f)
                processed += 1
                if file_bar:
                    file_bar.update(1)
                if total_bar:
                    total_bar.update(1)
        # Complete bar if something left (shouldn't typically happen)
        if file_bar and file_bar.total and file_bar.n < file_bar.total:
            file_bar.update(file_bar.total - file_bar.n)
        return archive.name
    except Exception as e:
        # Ensure bars still move to completion to avoid hang in UI
        if file_bar and file_bar.total and file_bar.n < file_bar.total:
            file_bar.update(file_bar.total - file_bar.n)
        raise RuntimeError(f"Failed to extract {archive}: {e}")


def parallel_download(base_url: str, names: List[str], dest: Path, overwrite: bool, workers: int,
                      retries: int, backoff: float):
    """Download files in parallel with overall and per-file byte progress bars."""
    # Pre-fetch sizes (HEAD) to compute overall total; ignore failures
    size_map: Dict[str, int] = {}
    session = requests.Session()
    for name in names:
        try:
            resp = session.head(base_url.rstrip('/') + '/' + name, timeout=15, allow_redirects=True)
            if resp.status_code == 200:
                size = int(resp.headers.get('Content-Length') or 0)
                if size > 0:
                    size_map[name] = size
        except Exception:
            pass
    total_bytes = sum(size_map.values()) if size_map else None

    # Create overall bar
    total_bar = tqdm(total=total_bytes, desc="Total", unit='B', unit_scale=True, unit_divisor=1024, position=0)
    # Create per-file bars
    file_bars: Dict[str, tqdm] = {}
    for idx, name in enumerate(names, start=1):
        file_bars[name] = tqdm(total=size_map.get(name), desc=name, unit='B', unit_scale=True, unit_divisor=1024, position=idx, leave=False)

    results = []
    errors = []
    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            future_map = {
                ex.submit(download_one_http, base_url, name, dest / name, overwrite, file_bars[name], total_bar, retries, backoff): name
                for name in names
            }
            for fut in as_completed(future_map):
                name = future_map[fut]
                try:
                    fut.result()
                    results.append(name)
                except Exception as e:
                    errors.append((name, str(e)))
    finally:
        # Close bars in reverse order to avoid display issues
        for bar in reversed(list(file_bars.values())):
            bar.close()
        total_bar.close()
    return results, errors


def parallel_extract(archives: List[Path], extract_dest: Path, overwrite: bool, workers: int):
    """Parallel extract with overall (files) and per-archive progress bars."""
    # Pre-scan to count members for each archive
    member_counts: Dict[str, int] = {}
    for p in archives:
        try:
            with tarfile.open(p, 'r:gz') as tf:
                member_counts[p.name] = len(tf.getmembers())
        except Exception:
            member_counts[p.name] = 0
    total_files = sum(member_counts.values()) or None

    # Overall bar (files) position 0, per-archive bars subsequent
    overall_bar = tqdm(total=total_files, desc="Extract Total", unit='file', position=0)
    archive_bars: Dict[str, tqdm] = {}
    for idx, p in enumerate(archives, start=1):
        archive_bars[p.name] = tqdm(total=member_counts.get(p.name), desc=f"{p.name}", unit='file', position=idx, leave=False)

    results = []
    errors = []
    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            future_map = {
                ex.submit(safe_extract_tar, p, extract_dest, overwrite, archive_bars[p.name], overall_bar): p.name
                for p in archives
            }
            for fut in as_completed(future_map):
                name = future_map[fut]
                try:
                    fut.result()
                    results.append(name)
                except Exception as e:
                    errors.append((name, str(e)))
    finally:
        for bar in reversed(list(archive_bars.values())):
            bar.close()
        overall_bar.close()
    return results, errors

# ----------------------------
# MD5 helpers
# ----------------------------
def md5_file(path: Path, bufsize: int = 1024 * 1024) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        while True:
            chunk = f.read(bufsize)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()

def parse_md5sums(text: str) -> Dict[str, str]:
    """
    Accepts formats like:
      <md5>  filename
    and ignores blank/comment lines.
    """
    m: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            md5 = parts[0]
            fname = parts[-1].lstrip("*")  # handle 'md5sum -b' style
            m[fname] = md5.lower()
    return m

# ----------------------------
# Split selection + index math
# ----------------------------
def choose_archives_for_percent(percent: int) -> List[str]:
    """
    Choose which .tar.gz archives to download for the requested percent of the main dataset.
    Always include ALWAYS_FILES *in addition* to the splits returned here.
    """
    if percent < 0 or percent > 100:
        raise ValueError("percent must be between 0 and 100")

    chosen: List[str] = []

    # First: full 10% blocks from the first 90%
    full_tens = min(percent // 10, 9)
    chosen.extend(TEN_PCT_SPLITS[:full_tens])
    rem = percent - full_tens * 10

    # Then: take from the final 10% in strict order 1,5,2,2
    for name, size in LAST10_SPLITS_IN_ORDER:
        if rem >= size:
            chosen.append(name)
            rem -= size

    if rem != 0:
        # cannot be composed with available final-10% pieces
        raise ValueError(
            f"Requested {percent}% cannot be composed from available splits. "
            f"Remainder after composition: {rem}%."
        )

    return chosen

def compute_boundaries(n: int) -> Dict[str, Tuple[int, int]]:
    """
    Mirror the exact logic used when packaging archives in 10PackageDS_And_Cleanup.py.

    Packaging script logic summary (important differences from a naive 0..100% of N):
      - The final 200 entries (if n >= 200) are pulled off first as the test set (last_200).
      - Percent splits (9 * 10%, then 1%,5%,2%,2%) are applied ONLY over the *main* portion
        consisting of the first M = n - 200 entries.
      - The 9 x 10% splits each have size: base_pct = M // 10 (integer floor). This means the
        first 9*base_pct entries are consumed; the remainder (M - 9*base_pct) becomes last_chunk.
      - The last chunk is subdivided into counts derived from int(M * p) for p in (0.01,0.05,0.02,0.02)
        with the final (2%_b) taking whatever remains so total coverage of the main M entries matches
        the packager's behavior (possible small deviations from exact percentages due to flooring).

    We reproduce that deterministic segmentation to ensure selected indices for trimming align
    exactly with what each archive actually contains.
    """
    ranges: Dict[str, Tuple[int, int]] = {}

    if n <= 0:
        return {"last_200.tar.gz": (0, 0)}

    # Handle scenarios with fewer than 200 entries: everything is the test set.
    if n <= 200:
        ranges["last_200.tar.gz"] = (0, n)
        # Other splits are empty
        for name in TEN_PCT_SPLITS:
            ranges[name] = (0, 0)
        for name, _ in LAST10_SPLITS_IN_ORDER:
            ranges[name] = (0, 0)
        return ranges

    M = n - 200  # main portion size
    base_pct = M // 10  # integer floor size for each of the first 9 splits

    # First 9 * 10% splits (each exactly base_pct entries)
    for i, name in enumerate(TEN_PCT_SPLITS):
        start = i * base_pct
        end = (i + 1) * base_pct
        ranges[name] = (start, end)

    # Remaining chunk after the first 9 splits
    last_chunk_start = 9 * base_pct
    remaining = M - last_chunk_start

    props = [0.01, 0.05, 0.02, 0.02]
    prop_names = [name for (name, _) in LAST10_SPLITS_IN_ORDER]

    counts = [int(M * p) for p in props[:-1]]  # compute first 3 via int()
    # Adjust final count so that total coverage == remaining
    used = sum(counts)
    last_count = remaining - used
    counts.append(max(0, last_count))

    # Assign boundaries for the final 4 sub-splits
    cursor = last_chunk_start
    for name, count in zip(prop_names, counts):
        start = cursor
        end = cursor + max(0, count)
        ranges[name] = (start, end)
        cursor = end

    # Finally the last 200 indices belong to last_200.tar.gz
    ranges["last_200.tar.gz"] = (M, n)

    return ranges

def indices_for_archives(ranges: Dict[str, Tuple[int, int]], chosen: List[str]) -> List[Tuple[int, int, str]]:
    """
    Returns list of (start, end, tag) where tag is the archive name.
    """
    out = []
    for name in chosen:
        if name not in ranges:
            continue
        start, end = ranges[name]
        if start < end:
            out.append((start, end, name))
    return out

def merge_intervals(intervals: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Merge overlapping index intervals."""
    if not intervals:
        return []
    intervals = sorted(intervals)
    merged = [intervals[0]]
    for s, e in intervals[1:]:
        ls, le = merged[-1]
        if s <= le:
            merged[-1] = (ls, max(le, e))
        else:
            merged.append((s, e))
    return merged

# ----------------------------
# Main routine
# ----------------------------
def main():
    ap = argparse.ArgumentParser(description="Download composable dataset splits from a static HTTP/HTTPS base URL.")
    ap.add_argument("--base-url", required=True, help="HTTP/HTTPS base URL where dataset files live (e.g. https://host/path). Do NOT include a filename.")
    ap.add_argument("--dest", required=True, help="Destination directory on local filesystem.")
    ap.add_argument("--percent", type=int, required=True, help="Percentage (0..100) of the main dataset to download (test set last_200 is always included).")
    ap.add_argument("--overwrite", action="store_true", help="Re-download files even if present.")
    ap.add_argument("--skip-md5", action="store_true", help="Skip MD5 verification against MD5SUMS.txt.")
    ap.add_argument("--dry-run", action="store_true", help="Print what would be downloaded and exit.")
    ap.add_argument("--extract-dest", help="Directory where archives will be extracted (default: <dest>/extracted).")
    ap.add_argument("--no-extract", action="store_true", help="Skip extracting .tar.gz archives after download.")
    ap.add_argument("--workers", type=int, default=3, help="Parallel worker threads for download/extract (default: 3).")
    ap.add_argument("--download-retries", type=int, default=3, help="Retries per file on download failure (default: 3).")
    ap.add_argument("--retry-backoff", type=float, default=2.0, help="Initial seconds for exponential backoff (default: 2.0).")
    args = ap.parse_args()

    dest = Path(args.dest).expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)

    # 1) Make the plan: which archives correspond to requested percent
    try:
        chosen_splits = choose_archives_for_percent(args.percent)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    # Always include test set + core files
    archives_to_get = chosen_splits + ALWAYS_FILES
    core_files_to_get = CORE_FILES[:]

    # 2) Dry run printout
    if args.dry_run:
        print("Dry run.")
        print("CORE:", core_files_to_get)
        print("ARCHIVES:", archives_to_get)
        base = args.base_url.rstrip('/')
        preview = [base + '/' + f for f in (core_files_to_get + archives_to_get)[:5]]
        print("Sample URLs:", preview)
        print("Note: last_200.tar.gz (test set) is always included.")
        return

    # 3) Prefetch MD5SUMS.txt (if missing) and pre-check existing files by MD5 to avoid re-downloading
    md5sums_path = dest / "MD5SUMS.txt"
    md5_map: Dict[str, str] = {}
    if md5sums_path.exists():
        try:
            md5_map = parse_md5sums(md5sums_path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            md5_map = {}
    else:
        try:
            print("Fetching MD5SUMS.txt for pre-checks ...")
            download_one_http(
                args.base_url, "MD5SUMS.txt", md5sums_path, args.overwrite,
                None, None, args.download_retries, args.retry_backoff
            )
            md5_map = parse_md5sums(md5sums_path.read_text(encoding="utf-8", errors="ignore"))
        except Exception as e:
            print(f"WARNING: Could not prefetch MD5SUMS.txt ({e}); skipping pre-checks.", file=sys.stderr)
            md5_map = {}

    def has_valid_md5(name: str) -> bool:
        local = dest / name
        expected = md5_map.get(name)
        if not local.exists() or not expected:
            return False
        try:
            return md5_file(local) == expected
        except Exception:
            return False

    core_to_download = [n for n in core_files_to_get if not has_valid_md5(n)]
    archives_to_download = [n for n in archives_to_get if not has_valid_md5(n)]

    # 4) Download core files (PAIR_V1.json needed for trimming) in parallel
    if core_to_download:
        print("Downloading core files ...")
        _, core_errors = parallel_download(
            args.base_url, core_to_download, dest, args.overwrite,
            max(1, min(args.workers, len(core_to_download))), args.download_retries, args.retry_backoff
        )
    else:
        core_errors = []
    if core_errors:
        for name, err in core_errors:
            print(f"ERROR core file {name}: {err}", file=sys.stderr)
        # If PAIR_V1.json failed we cannot continue
        if any(n == 'PAIR_V1.json' for n, _ in core_errors):
            sys.exit(6)

    # 4) Load PAIR_V1.json to compute trimming
    pair_path = dest / "PAIR_V1.json"
    if not pair_path.exists():
        print(f"PAIR_V1.json was not found at {pair_path}", file=sys.stderr)
        sys.exit(3)
    with pair_path.open("r", encoding="utf-8") as f:
        try:
            pair = json.load(f)
            if not isinstance(pair, list):
                raise ValueError("PAIR_V1.json is not a JSON list.")
        except Exception as e:
            print(f"Failed to parse PAIR_V1.json: {e}", file=sys.stderr)
            sys.exit(4)

    n = len(pair)
    if n == 0:
        print("PAIR_V1.json is empty.", file=sys.stderr)
        sys.exit(5)

    # 5) Compute index ranges per split and decide which indices are included
    ranges = compute_boundaries(n)
    selected_intervals = indices_for_archives(ranges, chosen_splits + ["last_200.tar.gz"])
    merged_for_info = merge_intervals([(s, e) for (s, e, _) in selected_intervals])

    # 6) Download archive files in parallel
    if archives_to_download:
        print("Downloading archive files ...")
        downloaded_archives, archive_errors = parallel_download(
            args.base_url, archives_to_download, dest, args.overwrite,
            args.workers, args.download_retries, args.retry_backoff
        )
    else:
        downloaded_archives, archive_errors = [], []
    if archive_errors:
        for name, err in archive_errors:
            print(f"ERROR archive {name}: {err}", file=sys.stderr)

    # (Extraction moved to after MD5 verification.)
    extract_dest = Path(args.extract_dest).expanduser().resolve() if args.extract_dest else (dest / "extracted")
    extracted_archives: List[str] = []

    # 7) Verify MD5s (optional) BEFORE extraction so we avoid wasting time on corrupt archives.
    checksums_report = {}
    if not args.skip_md5:
        print("Verifying MD5 checksums ...")
        md5sums_text = (dest / "MD5SUMS.txt").read_text(encoding="utf-8", errors="ignore")
        md5_map = parse_md5sums(md5sums_text)

        for name in core_files_to_get + archives_to_get:
            local = dest / name
            if not local.exists():
                checksums_report[name] = {"status": "missing"}
                continue
            expected = md5_map.get(name)
            if not expected:
                checksums_report[name] = {"status": "no_expected_md5"}
                continue
            actual = md5_file(local)
            checksums_report[name] = {
                "status": "ok" if actual == expected else "mismatch",
                "expected_md5": expected,
                "actual_md5": actual,
            }
        with (dest / "checksums_report.json").open("w", encoding="utf-8") as f:
            json.dump(checksums_report, f, indent=2)

        mismatches = [k for k, v in checksums_report.items() if v.get("status") == "mismatch"]
        if mismatches:
            print("WARNING: MD5 mismatches detected for:", ", ".join(mismatches), file=sys.stderr)
        else:
            print("All Tar MD5 checksums verified successfully.")

    # 8) Now extract archives (after MD5 verification)
    if not args.no_extract:
        print(f"Extracting archives to {extract_dest} ... It's normal for this to take a while and appear to freeze.")
        archive_paths = [dest / n for n in archives_to_get if n.endswith('.tar.gz') and (dest / n).exists()]
        extracted, extract_errors = parallel_extract(archive_paths, extract_dest, args.overwrite, args.workers)
        extracted_archives.extend(extracted)
        for name, err in extract_errors:
            print(f"ERROR extracting {name}: {err}", file=sys.stderr)

    # 9) Build trimmed PAIR_V1.json and corresponding trimmed PAIR_V1_HASHES.json
    #    Collect all indices covered by chosen_splits and last_200; then filter.
    include_mask = [False] * n
    for (start, end, tag) in selected_intervals:
        for i in range(start, end):
            include_mask[i] = True

    trimmed = [entry for i, entry in enumerate(pair) if include_mask[i]]

    trimmed_path = dest / "PAIR_V1.trimmed.json"
    with trimmed_path.open("w", encoding="utf-8") as f:
        json.dump(trimmed, f, indent=2)

    # Trim PAIR_V1_HASHES.json if present (assumed list aligned by index)
    hashes_path = dest / "PAIR_V1_HASHES.json"
    trimmed_hashes_path = dest / "PAIR_V1_HASHES.trimmed.json"
    trimmed_hashes = None
    if hashes_path.exists():
        try:
            with hashes_path.open("r", encoding="utf-8") as f:
                hashes_data = json.load(f)
            if isinstance(hashes_data, list) and len(hashes_data) == n:
                trimmed_hashes = [h for i, h in enumerate(hashes_data) if include_mask[i]]
                with trimmed_hashes_path.open("w", encoding="utf-8") as f:
                    json.dump(trimmed_hashes, f, indent=2)
            else:
                print("PAIR_V1_HASHES.json not a list with same length as PAIR_V1.json; skipping trim.", file=sys.stderr)
        except Exception as e:
            print(f"Failed to trim PAIR_V1_HASHES.json: {e}", file=sys.stderr)

    # 10) Write manifest (what went into this build)
    manifest = {
        "requested_percent_main": args.percent,
        "note": "The requested percent applies to the main dataset. The last_200 test set is always included in addition.",
        "archives_downloaded": archives_to_get,
        "core_files_downloaded": core_files_to_get,
        "md5_verification": (not args.skip_md5),
        "ranges_by_archive": {k: {"start": v[0], "end": v[1], "count": max(0, v[1]-v[0])} for k, v in ranges.items()},
        "selected_index_intervals": [{"start": s, "end": e} for (s, e) in merged_for_info],
        "counts": {
            "total_entries_in_PAIR_V1": n,
            "trimmed_entries_count": len(trimmed),
            "last_200_count": min(200, n),
        },
        "composition_rule": {
            "first_90": TEN_PCT_SPLITS,
            "final_10_ordered": [name for (name, _) in LAST10_SPLITS_IN_ORDER],
            "final_10_sizes": {name: sz for (name, sz) in LAST10_SPLITS_IN_ORDER},
        },
    "destination": str(dest),
    "extraction_destination": (str(extract_dest) if not args.no_extract else None),
    "archives_extracted": extracted_archives,
    "download_errors": {name: err for name, err in (archive_errors + core_errors)} if (archive_errors or core_errors) else {},
    "download_retry_policy": {"retries": args.download_retries, "initial_backoff": args.retry_backoff},
    "base_url": args.base_url,
    }
    with (dest / "manifest.json").open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    # 11) Friendly summary
    print("\nDone.")
    print(f"- Saved trimmed JSON: {trimmed_path}")
    print(f"- Saved manifest:     {dest / 'manifest.json'}")
    if not args.skip_md5:
        print(f"- Checksums report:   {dest / 'checksums_report.json'}")
    print(f"- Archives fetched:   {', '.join(archives_to_get)}")
    if not args.no_extract:
        print(f"- Extracted to:       {extract_dest}")
    if (dest / 'PAIR_V1_HASHES.trimmed.json').exists():
        print(f"- Saved trimmed HASH: {dest / 'PAIR_V1_HASHES.trimmed.json'}")

if __name__ == "__main__":
    main()
