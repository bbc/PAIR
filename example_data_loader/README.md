# BBC-PAIR PyTorch starter

This folder contains a small PyTorch Dataset to load BBC-PAIR images using the JSON metadata described in `content/dataset_information.md`.

## Install

Create/activate your environment then install dependencies:

```
pip install torch torchvision pillow
```

## Usage

You need two paths:
- `BBC_PAIR.json` — the metadata file (see the dataset docs for its structure)
- `images_root` — the directory that contains the image files referenced by relative paths in the JSON

Quick example:

```python
from pathlib import Path
from torch.utils.data import DataLoader
from bbc_pair_dataset import BbcPairImageDataset

json_path = Path(r"C:\\path\\to\\BBC_PAIR.json")
images_root = Path(r"C:\\path\\to\\BBC_PAIR_images")

ds = BbcPairImageDataset(
    metadata_json=json_path,
    images_root=images_root,
    mode="inpaint",  # or "base" | "whole" | "all"
)

dl = DataLoader(ds, batch_size=8, shuffle=True)
for batch in dl:
    images = batch["image"]  # float tensor [B, C, H, W]
    labels = batch["label"]  # 0 = real, 1 = generated
    # ... train ...
```

Alternatively, run the minimal example:

```
# Optionally set env vars for paths
setx BBC_PAIR_JSON "C:\\path\\to\\BBC_PAIR.json"
setx BBC_PAIR_IMAGES "D:\\datasets\\BBC_PAIR_images"

# Then run the example once your shell has reloaded its env
python projects/datasets/BBC-PAIR/pytorch/examples/pytorch_dataloader_example.py
```

## Notes

- JSON paths are often like `/abcdef/ori.png` — the loader strips the leading `/` and joins with `images_root`.
- Missing files are skipped by default (`skip_missing=True`). Set `skip_missing=False` to error on missing files.
- Whole-image generations, when present, are collected from mask entries under the optional `painters` key.
- Default transforms do a light resize/crop to 256 and ToTensor; override via the `transform` parameter for training.
