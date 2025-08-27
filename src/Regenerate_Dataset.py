#!/usr/bin/env python3
import os
import sys
import json
import hashlib
import argparse
import shutil
from PIL import Image
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# Cache for loaded base images
BASE_CACHE = {}
identifiers_that_need_icc_skipped = ['000207320e5ea147','0001fbcd56db7f74']

def md5_file(path):
    """Compute MD5 hash of a file."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def unpack9_vectorized(bitstream: np.ndarray, shape: tuple) -> np.ndarray:
    """
    Unpack a uint8 bitstream into an int16 diff array.
    shape is (h, w, c), total codes = h*w*c, each code is 9 bits.
    """
    h, w, c = shape
    total_codes = h * w * c
    total_bits = total_codes * 9

    # Unpack bits little-endian, then trim to needed bits
    bits = np.unpackbits(bitstream, bitorder='little')[:total_bits]
    bits = bits.reshape((total_codes, 9))

    # Compute magnitude (bits 0-7) and sign (bit 8)
    mag = bits[:, :8].dot(1 << np.arange(8, dtype=np.uint16)).astype(np.int16)
    sign = bits[:, 8].astype(bool)

    diff_flat = np.where(sign, -mag, mag)
    return diff_flat.reshape((h, w, c))

def process_reconstruct(task):
    base_path, diff_npz, out_img, expected_hash, diff_path = task

    # Load base image once
    base_img_original = BASE_CACHE[base_path]

    # Load compressed diffs
    npz_data = np.load(diff_npz)
    bitstream = npz_data['bitstream']
    shape = tuple(npz_data['shape'].tolist())

    # Unpack diffs
    diff_arr = unpack9_vectorized(bitstream, shape)
    h, w, _ = diff_arr.shape

    # Prepare output directory
    os.makedirs(os.path.dirname(out_img), exist_ok=True)

    corrector_txt = os.path.join(diff_path, 'rotation_Corrector.txt')
    corrector_true_txt = os.path.join(diff_path, 'rotation_info.txt')
    if os.path.exists(corrector_txt):
        # Read specified rotation and intermediate size
        with open(corrector_txt, 'r') as f:
            angle = int(f.readline().strip())
            interm_size = eval(f.readline().strip())  # (width, height)
        # Apply rotation
        rotated = base_img_original.convert('RGB').rotate(angle)
        # Resize to intermediary size
        resized = rotated.resize(interm_size, Image.LANCZOS)

        # Load and apply base image corrector
        corrector_npz = np.load(os.path.join(diff_path, 'base_image_corrector.npz'))
        # Assume the npz contains a single array of corrections
        corr_key = next(iter(corrector_npz.files))
        corr_arr = corrector_npz[corr_key]

        # Convert to array and add corrector
        base_arr = np.asarray(resized, dtype=np.int16)
        base_arr = base_arr + corr_arr.astype(np.int16)

        # If intermediary size differs from diff target, resize to diff dimensions
        if base_arr.shape[0] != h or base_arr.shape[1] != w:
            temp_img = Image.fromarray(np.clip(base_arr, 0, 255).astype(np.uint8))
            temp_img = temp_img.resize((w, h), Image.LANCZOS)
            base_arr = np.asarray(temp_img, dtype=np.int16)

        # Apply diff and save
        rec_arr = np.clip(base_arr + diff_arr, 0, 255).astype(np.uint8)
        Image.fromarray(rec_arr).save(out_img)

        # Verify hash
        actual_hash = md5_file(out_img)
        if actual_hash == expected_hash:
            return base_path, angle, w, h
        else:
            raise ValueError(
                f"Hash mismatch for {out_img} after applying corrector: "
                f"expected {expected_hash}, got {actual_hash}"
            )
    elif os.path.exists(corrector_true_txt):
        with open(corrector_true_txt, 'r') as f:
            angle = int(f.readline().strip())

        rotated = base_img_original.convert('RGB').rotate(angle)
        resized = rotated.resize((w, h), Image.LANCZOS)

        base_arr = np.asarray(resized, dtype=np.int16)
        rec_arr = np.clip(base_arr + diff_arr, 0, 255).astype(np.uint8)
        Image.fromarray(rec_arr).save(out_img)

        actual_hash = md5_file(out_img)
        if actual_hash == expected_hash:
            return base_path, angle, w, h

    # If all rotations failed
    raise ValueError(
        f"Hash mismatch for {out_img} after trying all orientations: "
        #f"expected {expected_hash}, got {actual_hash}"
    )


def process_image(base_path, reconstructed_dir, unique_rotation, diff_dir, info_by_loc, hash_map):
    # Extract identifier (filename without extension)
    filename = os.path.basename(base_path)
    identifier, _ = os.path.splitext(filename)

    # Prepare destination paths
    dest_dir = os.path.join(reconstructed_dir, identifier)
    diff_path = os.path.join(diff_dir, identifier)
    os.makedirs(dest_dir, exist_ok=True)

    dest_path = os.path.join(dest_dir, "ori.png")

    # Open image
    img = base_img_original = BASE_CACHE[base_path]
    original_mode = img.mode
    icc_profile = img.info.get("icc_profile")
    # TODO
    # Very hacky needs to be fixxed althought not an easy one to handle for 
    # perhaps after ('L') check change to RGB mode and turn off ICC profile and try to save again
    # I think that will work 
    if identifier in identifiers_that_need_icc_skipped:
        icc_profile = None 

    def save_image(arr, mode):
        """
        Helper to save the NumPy array arr as an image with given mode.
        """
        img_out = Image.fromarray(arr)
        img_out = img_out.convert(mode)
        if icc_profile:
            img_out.save(dest_path, icc_profile=icc_profile)
        else:
            img_out.save(dest_path)

    # Determine processing path
    corrector_txt = os.path.join(diff_path, 'rotation_Corrector.txt')
    if os.path.exists(corrector_txt):
        # Custom corrector flow
        with open(corrector_txt, 'r') as f:
            angle = int(f.readline().strip())
            interm_size = eval(f.readline().strip())  # (width, height)

        rotated = base_img_original.convert('RGB').rotate(angle)
        resized = rotated.resize(interm_size, Image.LANCZOS)

        corrector_npz = np.load(os.path.join(diff_path, 'base_image_corrector.npz'))
        corr_key = next(iter(corrector_npz.files))
        corr_arr = corrector_npz[corr_key]

        base_arr = np.asarray(resized, dtype=np.int16)
        base_arr += corr_arr.astype(np.int16)
        final_arr = np.clip(base_arr, 0, 255).astype(np.uint8)

        # Save using original_mode
        save_image(final_arr, original_mode)
    else:
        # Standard rotation & resize
        angle, _, _ = unique_rotation.get(base_path, (0, 0, 0))
        if angle:
            img = img.rotate(angle)
        shape = info_by_loc.get('/'+identifier+'/ori.png')
        th, tw = shape['image_height'], shape['image_width']
        img = img.resize((tw, th), Image.LANCZOS).convert(original_mode)
        img.save(dest_path)

    # Verify hash, with fallback to 'L' mode on mismatch
    for num, h_entry in hash_map.items():
        loc = h_entry['base_image_location'].lstrip('/')
        if loc.startswith(identifier + '/'):
            expected_hash = h_entry['base_image_hash'].lower()
            actual_hash = md5_file(dest_path)
            if actual_hash != expected_hash:
                # Retry saving in 'L' mode
                processed = Image.open(dest_path)
                arr = np.asarray(processed, dtype=np.uint8)
                save_image(arr, 'L')
                rerun_hash = md5_file(dest_path)
                if rerun_hash == expected_hash:
                    return None

                # Retry saving in 'RGB' mode without ICC profile
                img_out = Image.fromarray(arr).convert('RGB')
                img_out.save(dest_path)  # No ICC profile on this path
                rerun_hash_rgb = md5_file(dest_path)
                if rerun_hash_rgb == expected_hash:
                    return None

                # Final failure
                err = (f"Hash mismatch for {dest_path}: expected {expected_hash}, "
                       f"got {rerun_hash_rgb} after fallback to 'RGB'")
                print(err, file=sys.stderr)
                return err
            break


def check_hash(entry, hash_map, reconstructed_dir):
    # 1) grab your index into the hash_map
    num = entry["image_number"]
    h_entry = hash_map[num]

    # 2) the expected hash
    expected_hash = h_entry["base_image_hash"].lower()

    # 3) build the destination path
    rel_loc   = h_entry["base_image_location"].lstrip("/")  # "00dc2b13ab646b62/ori.png"
    dest_path = os.path.join(reconstructed_dir, rel_loc)         # "/data/.../00dc2b13ab646b62/ori.png"
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    # 4) compute & compare
    actual_hash = md5_file(dest_path)
    if actual_hash != expected_hash:
        # Return an error message so main thread can print it
        return f"Hash mismatch for {dest_path}: expected {expected_hash}, got {actual_hash}"
    return None

def load_base(bp):
    # each worker opens & converts one image
    return Image.open(bp).convert("RGB")

def main():
    parser = argparse.ArgumentParser(
        description="Fast reconstruct from 9-bit-packed .npz diffs (multithreaded)"
    )
    parser.add_argument('--dataset-specific-json', default='tars/PAIR_V1.trimmed.json',
                        help='JSON file for this specific dataset being created')
    parser.add_argument('--hash-json', default='tars/PAIR_V1_HASHES.trimmed.json',
                        help='JSON path for hashes')
    parser.add_argument('--openv7-download-folder', default="OpenV7_Originals",
                        help='Folder for OpenV7 images.')
    parser.add_argument('--tars-dir', default='tars',
                        help='Directory containing original dataset tar-derived JSON/manifest')
    parser.add_argument("--diff-dir", default="extracted",
                         help="Directory to save .npz diffs (default: /data/BBC_PAIR_DIFF/)")
    parser.add_argument("--reconstructed-dir", default="BBC_PAIR", 
                        help="Where to save outputs")
    parser.add_argument("--workers",   type=int, default=(lambda c: c if c <= 40 else c//2)(os.cpu_count() or 1), help="Number of threads")
    parser.add_argument("--remove-tar-files", action="store_true", help="Remove the original tar files")
    args = parser.parse_args()
        
    # Load JSONs
    info_data = json.load(open(args.dataset_specific_json))
    hash_data = json.load(open(args.hash_json))
    hash_map  = {e["image_number"]: e for e in hash_data}

    unique_bases = set()
    # Collect base image paths
    for entry in info_data:
        base_rel = entry["base_image_location"].lstrip("/").replace("ori.png", "ori.jpg").replace("/ori", "")
        #remove the folder prefix and chnage png to jpg
        unique_bases.add(os.path.join(args.openv7_download_folder, base_rel))

    # parallelize base‐image loading
    with ThreadPoolExecutor(max_workers=args.workers or os.cpu_count()) as exe:
        futures = {exe.submit(load_base, bp): bp for bp in unique_bases}
        for f in tqdm(as_completed(futures),
                    total=len(futures),
                    desc="Loading base images",
                    unit="img"):
            bp = futures[f]
            BASE_CACHE[bp] = f.result()

    # Build reconstruction tasks
    print("Building reconstruction tasks...")
    tasks = []
    for entry in tqdm(info_data):
        num = entry["image_number"]
        h_entry = hash_map[num]
        base_rel = entry["base_image_location"].lstrip("/").replace("ori.png", "ori.jpg").replace("/ori", "")
        base_path = os.path.join(args.openv7_download_folder, base_rel)

        for mask in entry.get("masks", []):
            mnum = str(mask["mask_number"])
            if mnum not in h_entry["inpainters_hashes"]:
                continue
            for name, exp_hash in h_entry["inpainters_hashes"][mnum].items():
                if name not in mask.get("inpainters", {}):
                    continue
                inp_rel  = mask["inpainters"][name].lstrip("/")
                diff_npz = os.path.join(args.diff_dir, os.path.splitext(inp_rel)[0] + ".npz")
                out_img  = os.path.join(args.reconstructed_dir, inp_rel)
                diff_path = os.path.join(args.diff_dir, inp_rel.split('/')[0]+'/')
                tasks.append((base_path, diff_npz, out_img, exp_hash, diff_path))
    
    os.makedirs(args.reconstructed_dir, exist_ok=True)

    # Parallel reconstruction with progress bar, gathering rotations
    errors = []
    rotation_info = []  # List to hold (base_path, angle) for images that needed rotation

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(process_reconstruct, t): t for t in tasks}
        for future in tqdm(as_completed(futures), total=len(futures),
                        desc="Reconstructing (fast) this may also appear to freeze", unit="file"):
            try:
                result = future.result()
                # process_reconstruct returns (base_path, angle) on success
                if result is not None:
                    base_path, angle, w, h = result
                    if angle != 0:
                        rotation_info.append((base_path, angle, w, h))
            except Exception as e:
                print(f"ERROR on task {futures[future]}: {e}", file=sys.stderr)
                errors.append((futures[future], e))
                break
    if errors:
        sys.exit("Reconstruction failed.")
    print(f"Reconstructed {len(tasks)} images into '{args.reconstructed_dir}' successfully.")

    # Deduplicate rotation_info by base_path (keep first occurrence of angle,w,h)
    print("Deduplicating rotations...")
    unique_rotation = {}
    for path, angle, w, h in tqdm(rotation_info):
        # if we haven’t seen this path yet, or this entry has larger area than
        # the one we stored, replace it
        prev = unique_rotation.get(path)
        if prev is None or (w * h) > (prev[1] * prev[2]):
            unique_rotation[path] = (angle, w, h)

    # Gather unique base_paths from tasks
    unique_bases = {t[0] for t in tasks}

    info_by_loc = {
        entry['base_image_location']: entry['image_shape']
        for entry in info_data
    }

    print("Copying originals with rotations, resizing and hash verification...")
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                process_image, bp, args.reconstructed_dir, unique_rotation, args.diff_dir, info_by_loc, hash_map
            ): bp
            for bp in unique_bases
        }

        for future in tqdm(as_completed(futures), total=len(futures), desc="Processing images"):
            bp = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                print(f"❌ Error processing {bp}: {exc}")

    # ------------------------------------------------------------------
    # Move painter whole-image outputs referenced under each mask['painters']
    # from the diff directory into the reconstructed directory, preserving
    # their relative paths. If a destination already exists, skip it. If the
    # source is missing, report and continue. "Move" semantics: we attempt
    # shutil.move; if it fails due to cross-device or other issues we fallback
    # to copy + remove.
    # ------------------------------------------------------------------
    print("Moving painter image files referenced in JSON ...")
    painter_paths = []
    for entry in info_data:
        for mask in entry.get('masks', []):
            painters = mask.get('painters')
            if not painters:
                continue
            for _name, rel_path in painters.items():
                if not rel_path:
                    continue
                rel_path_clean = rel_path.lstrip('/')
                src = os.path.join(args.diff_dir, rel_path_clean)
                dst = os.path.join(args.reconstructed_dir, rel_path_clean)
                painter_paths.append((src, dst))

    moved = 0
    skipped_existing = 0
    missing = 0
    errors_move = 0
    for src, dst in tqdm(painter_paths, desc="Moving painters", unit="file"):
        if os.path.exists(dst):
            skipped_existing += 1
            continue
        if not os.path.exists(src):
            print(f"Painter source missing: {src}")
            missing += 1
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            shutil.move(src, dst)
            moved += 1
        except Exception:
            try:
                shutil.copy2(src, dst)
                os.remove(src)
                moved += 1
            except Exception as e:
                print(f"Failed to move painter file {src} -> {dst}: {e}")
                errors_move += 1

    print(f"Painter move summary: moved={moved}, existing={skipped_existing}, missing={missing}, errors={errors_move}")
    
    # ------------------------------------------------------------------
    # Move mask files (original_mask_location and edited_mask_location)
    # ------------------------------------------------------------------
    print("Moving mask image files referenced in JSON ...")
    mask_paths = []
    for entry in info_data:
        for mask in entry.get('masks', []):
            for key in ('original_mask_location', 'edited_mask_location'):
                rel_path = mask.get(key)
                if not rel_path:
                    continue
                rel_path_clean = rel_path.lstrip('/')
                src = os.path.join(args.diff_dir, rel_path_clean)
                dst = os.path.join(args.reconstructed_dir, rel_path_clean)
                mask_paths.append((src, dst))

    # Deduplicate identical src/dst pairs
    seen = set()
    dedup_mask_paths = []
    for src, dst in mask_paths:
        if (src, dst) not in seen:
            seen.add((src, dst))
            dedup_mask_paths.append((src, dst))

    mask_moved = 0
    mask_existing = 0
    mask_missing = 0
    mask_errors = 0
    for src, dst in tqdm(dedup_mask_paths, desc="Moving masks", unit="file"):
        if os.path.exists(dst):
            mask_existing += 1
            continue
        if not os.path.exists(src):
            print(f"Mask source missing: {src}")
            mask_missing += 1
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            shutil.move(src, dst)
            mask_moved += 1
        except Exception:
            try:
                shutil.copy2(src, dst)
                os.remove(src)
                mask_moved += 1
            except Exception as e:
                print(f"Failed to move mask file {src} -> {dst}: {e}")
                mask_errors += 1

    print(f"Mask move summary: moved={mask_moved}, existing={mask_existing}, missing={mask_missing}, errors={mask_errors}")

    # ------------------------------------------------------------------
    # Move dataset-specific JSON into reconstructed dir as BBC_PAIR.json
    # and copy manifest.json there too.
    # ------------------------------------------------------------------
    try:
        dst_dataset_json = os.path.join(args.reconstructed_dir, 'BBC_PAIR.json')
        os.makedirs(args.reconstructed_dir, exist_ok=True)
        if os.path.abspath(args.dataset_specific_json) != os.path.abspath(dst_dataset_json):
            if os.path.exists(dst_dataset_json):
                print(f"Overwriting existing BBC_PAIR.json at {dst_dataset_json}")
                os.remove(dst_dataset_json)
            shutil.move(args.dataset_specific_json, dst_dataset_json)
            print(f"Moved dataset JSON to {dst_dataset_json}")
        else:
            print("Dataset JSON already at destination; skipping move.")
    except Exception as e:
        print(f"Failed to move dataset JSON: {e}")

    # Copy manifest.json if present
    manifest_src = os.path.join(args.tars_dir, 'manifest.json')
    if os.path.exists(manifest_src):
        manifest_dst = os.path.join(args.reconstructed_dir, 'manifest.json')
        try:
            shutil.copy2(manifest_src, manifest_dst)
            print(f"Copied manifest.json to {manifest_dst}")
        except Exception as e:
            print(f"Failed to copy manifest.json: {e}")
    else:
        print("manifest.json not found in tars/; skipping copy")
    
    # ------------------------------------------------------------------
    # Final cleanup: remove source folders (openv7 download + diff dir)
    # ------------------------------------------------------------------
    print("Removing source folders ...")
    cleanup_targets = [
        (args.openv7_download_folder, "openv7-download-folder"),
        (args.diff_dir, "diff-dir"),
    ]
    if args.remove_tar_files:
        cleanup_targets.append((args.tars_dir, "tars-dir"))

    for folder, label in cleanup_targets:
        if os.path.isdir(folder):
            try:
                shutil.rmtree(folder)
                print(f"Removed {label}: {folder}")
            except Exception as e:
                print(f"Failed to remove {label} {folder}: {e}")
        else:
            print(f"Skip remove (not found) {label}: {folder}")
    
if __name__ == "__main__":
    main()
