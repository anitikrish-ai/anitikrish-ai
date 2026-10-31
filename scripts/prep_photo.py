#!/usr/bin/env python3
"""Prepare a portrait for ASCII conversion (one-time, local).

Reads your photo from assets/source/ (or --input), removes the background
when appropriate, improves contrast, and writes two files:

  assets/generated/portrait_gray.png   grayscale subject on a clean white background
  assets/generated/portrait_mask.png   white = subject, black = background

Background modes (--bg):
  auto   use existing transparency if present; otherwise flood-fill from the
         corners when the corners look like one plain colour; otherwise keep
         the whole frame (with a warning)
  alpha  use the image's own alpha channel
  flood  flood-fill from the four corners (no extra dependencies)
  rembg  neural removal (needs the optional rembg packages)
  none   keep the whole frame

Nothing is downloaded or fetched; the output is derived only from your file.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / "assets" / "source"
GEN_DIR = ROOT / "assets" / "generated"
MARKER = (255, 0, 255)


def find_default_input() -> Path | None:
    for ext in ("png", "jpg", "jpeg", "webp"):
        found = sorted(SOURCE_DIR.glob(f"*.{ext}"))
        if found:
            return found[0]
    return None


def has_transparency(img: Image.Image) -> bool:
    if img.mode != "RGBA":
        return False
    alpha = np.asarray(img.getchannel("A"))
    return bool((alpha < 250).any())


def corners_look_plain(rgb: Image.Image, tolerance: int = 60) -> bool:
    w, h = rgb.size
    pts = [rgb.getpixel(p) for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    for a in pts:
        for b in pts:
            if sum(abs(x - y) for x, y in zip(a, b)) > tolerance:
                return False
    return True


def mask_from_flood(rgb: Image.Image, thresh: int) -> Image.Image:
    work = rgb.copy()
    w, h = work.size
    for xy in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        if work.getpixel(xy) != MARKER:
            ImageDraw.floodfill(work, xy, MARKER, thresh=thresh)
    arr = np.asarray(work)
    background = np.all(arr == np.array(MARKER, dtype=arr.dtype), axis=-1)
    mask = Image.fromarray(((~background) * 255).astype("uint8"))
    return mask.filter(ImageFilter.MedianFilter(3))


def mask_from_rembg(rgb: Image.Image) -> Image.Image:
    try:
        from rembg import remove  # type: ignore
    except ImportError:
        sys.exit(
            "rembg is not installed. Uncomment rembg/onnxruntime in "
            "scripts/requirements.txt, install them, or use --bg flood."
        )
    cut = remove(rgb)
    return cut.convert("RGBA").getchannel("A")


def build_mask(img: Image.Image, mode: str, thresh: int) -> Image.Image:
    rgb = img.convert("RGB")
    if mode == "auto":
        if has_transparency(img):
            mode = "alpha"
        elif corners_look_plain(rgb):
            mode = "flood"
        else:
            print(
                "warning: corners are not one plain colour; keeping the whole "
                "frame. Try --bg rembg or --bg flood.",
                file=sys.stderr,
            )
            mode = "none"
        print(f"background mode: {mode}")
    if mode == "alpha":
        if img.mode != "RGBA":
            sys.exit("--bg alpha needs an image with an alpha channel.")
        return img.getchannel("A")
    if mode == "flood":
        return mask_from_flood(rgb, thresh)
    if mode == "rembg":
        return mask_from_rembg(rgb)
    return Image.new("L", img.size, 255)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--input", type=Path, default=None, help="source photo (default: first image in assets/source/)")
    ap.add_argument("--out-dir", type=Path, default=GEN_DIR)
    ap.add_argument("--bg", choices=["auto", "alpha", "flood", "rembg", "none"], default="auto")
    ap.add_argument("--flood-thresh", type=int, default=36, help="flood-fill colour tolerance (sum of channel differences)")
    ap.add_argument("--equalize", type=float, default=0.5, help="0..1 blend of masked histogram equalisation")
    ap.add_argument("--sharpen", type=int, default=140, help="unsharp-mask percent (0 disables)")
    ap.add_argument("--padding", type=float, default=0.04, help="crop padding as a fraction of the subject size")
    ap.add_argument("--max-size", type=int, default=1200, help="longest side in pixels")
    args = ap.parse_args()

    src = args.input or find_default_input()
    if src is None or not Path(src).is_file():
        print(
            "No portrait found. Put your photo in assets/source/ "
            "(png/jpg/jpeg/webp) or pass --input PATH.",
            file=sys.stderr,
        )
        return 2

    img = Image.open(src)
    img = ImageOps.exif_transpose(img)
    img.load()
    rgba = img.convert("RGBA")
    mask = build_mask(rgba, args.bg, args.flood_thresh)

    bbox = mask.point(lambda p: 255 if p > 128 else 0).getbbox()
    if bbox is None:
        print("The subject mask is empty; try a different --bg mode.", file=sys.stderr)
        return 1
    l, t, r, b = bbox
    pad_x = int((r - l) * args.padding)
    pad_y = int((b - t) * args.padding)
    box = (max(0, l - pad_x), max(0, t - pad_y), min(rgba.width, r + pad_x), min(rgba.height, b + pad_y))

    rgb = rgba.convert("RGB").crop(box)
    mask = mask.crop(box)

    canvas = Image.new("RGB", rgb.size, (255, 255, 255))
    canvas.paste(rgb, mask=mask)

    longest = max(canvas.size)
    if longest > args.max_size:
        scale = args.max_size / longest
        new_size = (max(1, round(canvas.width * scale)), max(1, round(canvas.height * scale)))
        canvas = canvas.resize(new_size, Image.Resampling.LANCZOS)
        mask = mask.resize(new_size, Image.Resampling.LANCZOS)

    gray = ImageOps.grayscale(canvas)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    if args.equalize > 0:
        eq = ImageOps.equalize(gray, mask=mask.point(lambda p: 255 if p > 128 else 0))
        gray = Image.blend(gray, eq, min(1.0, args.equalize))
    if args.sharpen > 0:
        gray = gray.filter(ImageFilter.UnsharpMask(radius=1.6, percent=args.sharpen, threshold=2))

    # Keep the background pure white.
    white = Image.new("L", gray.size, 255)
    gray = Image.composite(gray, white, mask)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    gray_path = args.out_dir / "portrait_gray.png"
    mask_path = args.out_dir / "portrait_mask.png"
    gray.save(gray_path)
    mask.save(mask_path)
    print(f"wrote {gray_path}")
    print(f"wrote {mask_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
