import json

with open("fingerprints_55570.json", "r") as f:
    fps = json.load(f)
    p28_images = [f for f in fps if "Notes_P28" in f['original_name']]
    for img in p28_images:
        print(f"Label: {img['original_name']} | Anchor: {img['anchor']} | Page: {img['page']}")
