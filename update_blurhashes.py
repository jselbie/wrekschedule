import json
import requests
import blurhash
import numpy as np
from PIL import Image
from io import BytesIO

MAPPING_FILE = "mapping.json"
COMPONENTS_X = 5
COMPONENTS_Y = 5

with open(MAPPING_FILE, "r") as f:
    mapping = json.load(f)

for key, entry in mapping.items():
    url = entry.get("logoUrl")
    if not url:
        print(f"  SKIP {key}: no logoUrl")
        continue

    print(f"Processing {key} ...", end=" ", flush=True)
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content)).convert("RGB")
        pixels = np.array(img)
        hash_str = blurhash.encode(pixels, components_x=COMPONENTS_X, components_y=COMPONENTS_Y)
        entry["logoBlurHash"] = hash_str
        print(f"OK -> {hash_str}")
    except Exception as e:
        print(f"ERROR: {e}")

with open(MAPPING_FILE, "w") as f:
    json.dump(mapping, f, indent=2)

print("\nDone. mapping.json updated.")
