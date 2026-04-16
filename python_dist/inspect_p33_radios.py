import json

with open("fingerprints_55570.json", "r") as f:
    fps = json.load(f)
    p33_radios = [f for f in fps if f['page'] == 32 and f['is_radio']]
    for r in p33_radios[:10]:
        print(f"Label: {r['original_name']} | Anchor: {r['anchor']} | Rect: {r['abs_rect']}")
