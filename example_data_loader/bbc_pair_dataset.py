"""
BBC-PAIR PyTorch Dataset
========================

This module provides a starting-point PyTorch Dataset for the BBC-PAIR dataset,
based on the metadata structure described in `content/dataset_information.md`.

It expects a JSON file with entries like the example in the docs, where each top-level
record includes fields such as:

- image_number (int)
- base_image_location (str)
- base_image_description (str)
- image_shape { image_height, image_width, num_of_channels }
- masks: list of mask dicts that can include 'inpainters' and sometimes 'painters' (whole gens)
- Original_NSFW (bool)

Paths in the JSON appear to be relative (e.g. "/030d.../ori.png"). This dataset joins them
with an `images_root` directory you provide.

Usage (see examples/pytorch_dataloader_example.py):

    from bbc_pair_dataset import BbcPairImageDataset
    ds = BbcPairImageDataset(
        metadata_json="/path/to/BBC_PAIR.json",
        images_root="/path/to/images/root",
        mode="inpaint",  # or "base" | "whole" | "all"
    )

    from torch.utils.data import DataLoader
    dl = DataLoader(ds, batch_size=8, shuffle=True, num_workers=4)
    batch = next(iter(dl))
    imgs, targets = batch["image"], batch["label"]

Notes:
- This is a starter implementation. The real dataset is large; you may want to
  filter/partition before training at scale.
- Whole images can appear under a mask's 'painters' mapping; we scan masks to collect them.
- Missing files are skipped by default (skip_missing=True). Set skip_missing=False to raise.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional, Tuple

from PIL import Image

try:
    import torch
    from torch.utils.data import Dataset
except Exception as exc:  # pragma: no cover - import-time guard
    raise RuntimeError(
        "PyTorch is required to use BbcPairImageDataset. Please install torch."
    ) from exc

try:
    from torchvision import transforms as T
except Exception:
    T = None  # Transforms are optional; users can pass a PIL->Tensor callable.


Mode = Literal["base", "inpaint", "whole", "all"]


@dataclass
class Sample:
    """Internal representation of one sample to load.

    Attributes:
        kind: "base" | "inpaint" | "whole"
        image_path: Absolute path to the image to load
        label: 0 for real, 1 for generated
        model: Model name string for generated samples (or "Real_Whole" for real whole refs)
        caption: KOSMOS-2 caption associated with the base image
        meta: Additional metadata dict (mask_number, masked_object, etc.)
    """

    kind: Literal["base", "inpaint", "whole"]
    image_path: Path
    label: int
    model: Optional[str]
    caption: Optional[str]
    meta: Dict[str, Any]


class BbcPairImageDataset(Dataset):
    """PyTorch Dataset for BBC-PAIR images using the BBC_PAIR.json metadata.

    Parameters
    ----------
    metadata_json : str | os.PathLike
        Path to the BBC_PAIR.json metadata file.
    images_root : str | os.PathLike
        Root directory to which relative image paths in the JSON are joined.
    mode : {"base", "inpaint", "whole", "all"}
        Which samples to expose. "all" returns a union of base, inpainted, and whole.
    transform : callable, optional
        Callable applied to a PIL image and returning a tensor. If None, a safe default
        resize+ToTensor is used when torchvision is available; otherwise a simple PIL->Tensor
        conversion is used.
    skip_missing : bool
        If True, silently skip entries whose image file does not exist. If False, raise.
    return_dict : bool
        If True (default), __getitem__ returns a dict with image tensor and metadata. If False,
        it returns (image_tensor, label).
    """

    def __init__(
        self,
        metadata_json: os.PathLike | str,
        images_root: os.PathLike | str,
        mode: Mode = "inpaint",
        transform: Optional[Any] = None,
        skip_missing: bool = True,
        return_dict: bool = True,
    ) -> None:
        super().__init__()
        self.metadata_json = Path(metadata_json)
        self.images_root = Path(images_root)
        self.mode: Mode = mode
        self.skip_missing = skip_missing
        self.return_dict = return_dict

        if not self.metadata_json.exists():
            raise FileNotFoundError(f"Metadata JSON not found: {self.metadata_json}")

        # Default transform keeps memory reasonable; users can override for training.
        if transform is None:
            if T is not None:
                self.transform = T.Compose(
                    [T.Resize(256), T.CenterCrop(256), T.ToTensor()]
                )
            else:
                # Minimal PIL->Tensor conversion if torchvision is not available.
                def pil_to_tensor(img: Image.Image):
                    arr = torch.ByteTensor(torch.ByteStorage.from_buffer(img.tobytes()))
                    arr = arr.view(img.size[1], img.size[0], len(img.getbands()))
                    return arr.permute(2, 0, 1).float() / 255.0

                self.transform = pil_to_tensor
        else:
            self.transform = transform

        # Load metadata
        with self.metadata_json.open("r", encoding="utf-8") as f:
            data = json.load(f)

        # Data can be list[entries] or dict with a top-level key; normalize to list
        if isinstance(data, dict):
            # Common pattern: {"images": [...]} or single entry
            if "images" in data and isinstance(data["images"], list):
                records = data["images"]
            else:
                # Assume the dict itself is one record (fallback)
                records = [data]
        elif isinstance(data, list):
            records = data
        else:
            raise ValueError("Unsupported JSON structure; expected list or dict with 'images' key.")

        self._samples: List[Sample] = []
        for rec in records:
            self._add_record(rec)

        # If we skipped many missing files, at least expose that info
        if len(self._samples) == 0:
            raise RuntimeError(
                "No samples were collected. Verify images_root and JSON paths. "
                "If your images are not available locally, consider running with a subset "
                "or ensure 'skip_missing' is False to catch path issues early."
            )

    # ------------------------------
    # Building sample index
    # ------------------------------
    def _add_record(self, rec: Dict[str, Any]) -> None:
        caption = rec.get("base_image_description")
        base_rel = rec.get("base_image_location")
        masks = rec.get("masks", []) or []

        # Base sample (real)
        if self.mode in ("base", "all") and base_rel:
            p = self._join_rel(base_rel)
            self._maybe_add_sample(
                Sample(
                    kind="base",
                    image_path=p,
                    label=0,
                    model=None,
                    caption=caption,
                    meta={
                        "image_number": rec.get("image_number"),
                        "image_shape": rec.get("image_shape"),
                        "Original_NSFW": rec.get("Original_NSFW"),
                    },
                )
            )

        # Inpainted samples (fake)
        if self.mode in ("inpaint", "all"):
            for m in masks:
                inp = m.get("inpainters", {}) or {}
                for model_name, rel_path in inp.items():
                    p = self._join_rel(rel_path)
                    meta = {
                        "mask_number": m.get("mask_number"),
                        "masked_object": m.get("masked_object"),
                        "masked_object_detailed": m.get("masked_object_detailed"),
                        "mask_area_percent": m.get("mask_area_percent"),
                        "centre_of_mask": m.get("centre_of_mask"),
                        "NSFW": (m.get("inpainters_NSFW", {}) or {}).get(model_name),
                        "image_number": rec.get("image_number"),
                        "image_shape": rec.get("image_shape"),
                    }
                    self._maybe_add_sample(
                        Sample(
                            kind="inpaint",
                            image_path=p,
                            label=1,
                            model=str(model_name),
                            caption=caption,
                            meta=meta,
                        )
                    )

        # Whole image samples (mostly fake, except possibly 'Real_Whole')
        if self.mode in ("whole", "all"):
            # Whole gens sometimes live under a mask's 'painters'
            for m in masks:
                painters = m.get("painters", {}) or {}
                painters_nsfw = m.get("painters_NSFW", {}) or {}
                for model_name, rel_path in painters.items():
                    p = self._join_rel(rel_path)
                    label = 0 if str(model_name).lower().startswith("real_whole") else 1
                    meta = {
                        "mask_number": m.get("mask_number"),
                        "NSFW": painters_nsfw.get(model_name),
                        "image_number": rec.get("image_number"),
                        "image_shape": rec.get("image_shape"),
                    }
                    self._maybe_add_sample(
                        Sample(
                            kind="whole",
                            image_path=p,
                            label=label,
                            model=str(model_name),
                            caption=caption,
                            meta=meta,
                        )
                    )

    def _join_rel(self, rel_path: str | os.PathLike) -> Path:
        # JSON paths may have leading '/' - strip to avoid absolute path resolution
        rel = str(rel_path).lstrip("/\\")
        return self.images_root / rel

    def _maybe_add_sample(self, sample: Sample) -> None:
        if sample.image_path.exists():
            self._samples.append(sample)
        else:
            if self.skip_missing:
                return
            raise FileNotFoundError(f"Image not found: {sample.image_path}")

    # ------------------------------
    # Dataset protocol
    # ------------------------------
    def __len__(self) -> int:
        return len(self._samples)

    def __getitem__(self, idx: int):
        s = self._samples[idx]
        img = Image.open(s.image_path).convert("RGB")
        img_t = self.transform(img) if self.transform else img

        if self.return_dict:
            return {
                "image": img_t,
                "label": s.label,
                "kind": s.kind,
                "model": s.model,
                "caption": s.caption,
                "image_path": str(s.image_path),
                "meta": s.meta,
            }
        else:
            return img_t, s.label


__all__ = ["BbcPairImageDataset"]
