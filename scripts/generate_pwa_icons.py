#!/usr/bin/env python3
"""Generate PWA icons: infinity artwork centered on #020617 (see static/infinity-pwa-source.png)."""
from __future__ import annotations

import os
import sys

try:
    from PIL import Image
except ImportError:
    print("Install Pillow: pip install Pillow", file=sys.stderr)
    sys.exit(1)

BG = (2, 6, 23)  # #020617
SOURCE_NAME = "infinity-pwa-source.png"


def composite_icon(source: Image.Image, size: int, pad_ratio: float = 0.12) -> Image.Image:
    src = source.convert("RGBA")
    canvas = Image.new("RGB", (size, size), BG)
    margin = int(size * pad_ratio)
    max_inner = max(1, size - 2 * margin)
    w, h = src.size
    scale = min(max_inner / w, max_inner / h)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    resized = src.resize((nw, nh), Image.Resampling.LANCZOS)
    x, y = (size - nw) // 2, (size - nh) // 2
    canvas.paste(resized, (x, y), resized)
    return canvas


def main() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    static_dir = os.path.join(root, "static")
    src_path = os.path.join(static_dir, SOURCE_NAME)
    if not os.path.isfile(src_path):
        print(f"Missing {src_path}", file=sys.stderr)
        sys.exit(1)
    with Image.open(src_path) as source:
        for dim in (512, 192):
            out = os.path.join(static_dir, f"icon-{dim}.png")
            composite_icon(source, dim).save(out, "PNG", optimize=True)
            print(f"Wrote {out}")


if __name__ == "__main__":
    main()
