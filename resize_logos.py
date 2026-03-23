import os
from PIL import Image

src_dir = r"D:\projects\wrekonline\scripts2\original"
dst_dir = r"D:\projects\wrekonline\scripts2\updated"
max_width = 600

for filename in os.listdir(src_dir):
    src_path = os.path.join(src_dir, filename)
    stem = os.path.splitext(filename)[0]
    dst_filename = stem + ".jpg"
    dst_path = os.path.join(dst_dir, dst_filename)

    img = Image.open(src_path).convert("RGB")
    w, h = img.size

    if w > max_width:
        new_w = max_width
        new_h = int(h * max_width / w)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        resized = True
    else:
        resized = False

    img.save(dst_path, "JPEG", quality=80)
    new_size = os.path.getsize(dst_path)
    label = "Resized" if resized else "Kept   "
    print(f"{label} {filename} ({w}x{h}) -> {dst_filename} ({img.size[0]}x{img.size[1]}) [{new_size:,} bytes]")

print("\nDone.")
