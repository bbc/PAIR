#!/usr/bin/env python3
import argparse
import json
import os

def extract_image_id(path):
    """
    Given a path like "/02b6e3289f4525e1/ori.png",
    return "02b6e3289f4525e1".
    """
    # strip leading slash, split on "/"
    parts = path.lstrip('/').split('/')
    return parts[0]

def make_list(dataset_specific_json, output_txt):
    # load JSON
    with open(dataset_specific_json, 'r') as f:
        data = json.load(f)

    ids = set()
    for item in data:
        base_loc = item.get('base_image_location', '')
        if not base_loc:
            continue
        img_id = extract_image_id(base_loc)
        ids.add(img_id)

    # write out sorted list
    with open(output_txt, 'w') as f:
        for img_id in sorted(ids):
            f.write(f"train/{img_id}\n")

    print(f"Wrote {len(ids)} entries to {output_txt}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate a download list for OpenImages downloader.py"
    )
    parser.add_argument('--dataset-specific-json', default='tars/PAIR_V1.trimmed.json',
                        help='JSON file for this specific dataset being created')
    parser.add_argument("--download-list", default="tars/download_list.txt",
                        help="Output text file for downloads (default: download_list.txt)"
    )
    args = parser.parse_args()
    make_list(args.dataset_specific_json, args.download_list)

#python make_download_list.py images_info.json \
#    --output=train_images.txt