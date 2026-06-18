"""
resize_icons.py
----------------
Batch-resizes oversized icon/logo images flagged by the PageSpeed audit.
Run this from inside your Django project, pointing STATIC_IMAGES_DIR at
your actual static/images folder.

What it does:
- Makes a backup copy of each original (suffix _original) before touching it
- Resizes each image to its target display size x2 (for retina sharpness)
- Re-saves with optimization to shrink file size further
- Skips any file it can't find, with a warning, so partial runs are safe

Usage:
    python resize_icons.py
"""

import os
from PIL import Image

# ── EDIT THIS to point at your real static/images folder ──
STATIC_IMAGES_DIR = "static/images/offer"

# (relative_path, target_display_width_px) — height matches aspect ratio
# Target = 2x the actual displayed size on the page, per the Lighthouse report.
TARGETS = {
    "machine_1.webp":              160,   # displayed at 77x74 -> 2x ~154-160
    "machine_2.webp":     160,   # displayed at 22x22 -> 2x ~44-64 (safe pad)
    "trainer.webp":     300,
}

def resize_image(path, target_width):
    if not os.path.exists(path):
        print(f"  SKIP (not found): {path}")
        return

    backup_path = path.replace(".webp", "_original.webp").replace(".webp", "_original.webp")
    if not os.path.exists(backup_path):
        Image.open(path).save(backup_path)
        print(f"  Backed up -> {backup_path}")

    img = Image.open(path)
    orig_w, orig_h = img.size

    if orig_w <= target_width:
        print(f"  SKIP (already small enough): {path} ({orig_w}x{orig_h})")
        return

    ratio = target_width / orig_w
    new_size = (target_width, max(1, round(orig_h * ratio)))

    if img.mode not in ("RGBA", "RGB"):
        img = img.convert("RGBA")

    resized = img.resize(new_size, Image.LANCZOS)
    resized.save(path, optimize=True)

    orig_bytes = os.path.getsize(backup_path)
    new_bytes = os.path.getsize(path)
    saved_kb = (orig_bytes - new_bytes) / 1024
    print(f"  {path}: {orig_w}x{orig_h} -> {new_size[0]}x{new_size[1]}  "
          f"({orig_bytes/1024:.1f}KB -> {new_bytes/1024:.1f}KB, saved {saved_kb:.1f}KB)")


def main():
    print(f"Resizing icons under: {STATIC_IMAGES_DIR}\n")
    for rel_path, target_width in TARGETS.items():
        full_path = os.path.join(STATIC_IMAGES_DIR, rel_path)
        resize_image(full_path, target_width)
    print("\nDone. Originals backed up as *_original.* alongside each file.")
    print("Verify the site looks correct, then you can delete the _original backups.")


if __name__ == "__main__":
    main()