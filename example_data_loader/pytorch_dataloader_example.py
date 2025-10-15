"""
Minimal example showing how to use BbcPairImageDataset with a PyTorch DataLoader.

Adjust the paths below to your local setup:
- METADATA_JSON: points to the BBC_PAIR.json file
- IMAGES_ROOT: root folder that contains the relative paths from the JSON

This script only loads a single batch to verify everything is wired up.
"""
from __future__ import annotations

import os
from pathlib import Path

import sys

# Ensure we can import the dataset module from the 'pytorch' folder
THIS_DIR = Path(__file__).resolve().parent
PYTORCH_DIR = THIS_DIR.parent
if str(PYTORCH_DIR) not in sys.path:
    sys.path.insert(0, str(PYTORCH_DIR))

from bbc_pair_dataset import BbcPairImageDataset
from PIL import Image
import random
import numpy as np
import matplotlib.pyplot as plt

try:
    import torch
    from torch.utils.data import DataLoader
except Exception as exc:
    raise RuntimeError("This example requires PyTorch installed.") from exc


def _pil_resize_to_tensor(img: Image.Image, size: int = 256):
    """Resize to size x size and convert to torch.FloatTensor in [0,1].

    This avoids needing torchvision for the example.
    """
    img = img.convert("RGB").resize((size, size), resample=Image.BICUBIC)
    # Convert via numpy to avoid deprecated TypedStorage path
    arr = np.asarray(img, dtype=np.float32) / 255.0  # (H, W, 3) in [0,1]
    return torch.from_numpy(arr).permute(2, 0, 1).contiguous()


def main():
    # TODO: update these to match your environment
    METADATA_JSON = os.environ.get("BBC_PAIR_JSON", r"C:\\path\\to\\BBC_PAIR.json")
    IMAGES_ROOT = os.environ.get("BBC_PAIR_IMAGES", r"D:\\datasets\\BBC_PAIR_images")

    dataset = BbcPairImageDataset(
        metadata_json=METADATA_JSON,
        images_root=IMAGES_ROOT,
        mode="all",  # show base + inpaint + whole in example
        transform=_pil_resize_to_tensor,
        skip_missing=True,
        return_dict=True,
    )

    def collate_mixed(batch):
        """Custom collate that stacks images/labels but keeps metadata as lists.

        Avoids recursive dict collation on 'meta' where keys differ across samples.
        """
        images = torch.stack([b["image"] for b in batch], dim=0)
        labels = torch.as_tensor([b["label"] for b in batch], dtype=torch.long)
        kinds = [b["kind"] for b in batch]
        models = [b["model"] for b in batch]
        captions = [b["caption"] for b in batch]
        image_paths = [b["image_path"] for b in batch]
        metas = [b.get("meta", {}) for b in batch]
        return {
            "image": images,
            "label": labels,
            "kind": kinds,
            "model": models,
            "caption": captions,
            "image_path": image_paths,
            "meta": metas,
        }

    dl = DataLoader(dataset, batch_size=4, shuffle=True, num_workers=0, collate_fn=collate_mixed)

    print(f"Dataset size: {len(dataset)}")
    batch = next(iter(dl))
    imgs, labels = batch["image"], batch["label"]
    print("Batch images shape:", tuple(imgs.shape))
    print("Batch labels:", labels.tolist())
    print("Kinds:", batch["kind"])  # e.g., ['inpaint', 'inpaint', ...]
    print("First sample path:", batch["image_path"][0])

    # --- Visualize ONLY the final mask entry for a random base image ---
    # Includes: base image, original mask, edited mask, all inpaints and whole generations.
    import json
    from pathlib import Path as _P

    def _join_rel(rel: str) -> Path:
        return dataset.images_root / str(rel).lstrip("/\\")

    # Load metadata JSON
    with open(dataset.metadata_json, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "images" in data and isinstance(data["images"], list):
        records = data["images"]
    elif isinstance(data, list):
        records = data
    else:
        records = []

    # Filter to records that have a final mask with both inpaint and whole entries
    candidates = []
    for rec in records:
        masks = rec.get("masks") or []
        if not masks:
            continue
        last = masks[-1]
        inp = (last.get("inpainters") or {})
        painters = (last.get("painters") or {})
        if inp and painters:
            candidates.append(rec)

    if not candidates:
        print("No records found where the final mask contains both inpaint and whole entries. Skipping viz.")
        return

    chosen = random.choice(candidates)
    imgnum = chosen.get("image_number")
    base_rel = chosen.get("base_image_location")
    masks = chosen.get("masks") or []
    last = masks[-1]
    desc = chosen.get("base_image_description")

    print(f"\nVisualizing image_number={imgnum} final mask entry…")
    if desc:
        print(f"Description: {desc}")

    # Gather display items: base + masks
    display_items = []  # list of tuples (PIL.Image, title)
    try:
        base_img = Image.open(_join_rel(base_rel)).convert("RGB")
        display_items.append((base_img, "base (real)"))
    except Exception as e:
        print(f"Could not load base image: {e}")

    orig_mask_rel = last.get("original_mask_location")
    edit_mask_rel = last.get("edited_mask_location")
    if orig_mask_rel:
        try:
            orig_mask = Image.open(_join_rel(orig_mask_rel)).convert("L")
            display_items.append((orig_mask, "original mask (L)"))
        except Exception as e:
            print(f"Could not load original mask: {e}")
    if edit_mask_rel:
        try:
            edit_mask = Image.open(_join_rel(edit_mask_rel)).convert("L")
            display_items.append((edit_mask, "edited mask (L)"))
        except Exception as e:
            print(f"Could not load edited mask: {e}")

    # Inpainted results (partial generations)
    for model_name, rel in (last.get("inpainters") or {}).items():
        try:
            im = Image.open(_join_rel(rel)).convert("RGB")
            display_items.append((im, f"inpaint | fake | {model_name}"))
        except Exception as e:
            print(f"Skipping inpaint {model_name}: {e}")

    # Whole results (fully generated; Real_Whole is real)
    for model_name, rel in (last.get("painters") or {}).items():
        try:
            im = Image.open(_join_rel(rel)).convert("RGB")
            label_name = "real" if str(model_name).lower().startswith("real_whole") else "fake"
            display_items.append((im, f"whole | {label_name} | {model_name}"))
        except Exception as e:
            print(f"Skipping whole {model_name}: {e}")

    if display_items:
        # Make a friendly grid, first a row for base/masks then the rest
        cols = 4
        rows = (len(display_items) + cols - 1) // cols
        plt.figure(figsize=(cols * 4, rows * 4))
        for i, (im, title) in enumerate(display_items):
            ax = plt.subplot(rows, cols, i + 1)
            # Resize large images for display
            disp = im
            if disp.width > 768 or disp.height > 768:
                disp = disp.resize((768, int(768 * disp.height / max(1, disp.width))), Image.BICUBIC)
            ax.imshow(disp if disp.mode == "RGB" else disp, cmap="gray" if disp.mode != "RGB" else None)
            ax.set_title(title, fontsize=8)
            ax.axis("off")
        # Show only image_number in the figure title (no description)
        plt.suptitle(f"image_number={imgnum}", fontsize=10)
        plt.tight_layout()
        plt.show()


if __name__ == "__main__":
    main()
