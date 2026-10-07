#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""contact_sheet.py — build one reviewable grid image from a project's renders.

For casting review it picks the newest render per cast id and lays them out in a
grid with the character's Chinese name burned in, so the whole cast can be judged
side by side in one glance.

  contact_sheet.py --project tianlie --kind cast
  contact_sheet.py --project tianlie --kind cast --out out/cast_v8.png
  contact_sheet.py --project tianlie --files a.png,b.png --cols 2
"""

from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PROJECTS = Path.home() / "comfy-projects"
LABEL_H = 58
PAD = 16
BG = (245, 245, 247)


def load_font(size: int):
    """Prefer a CJK-capable font; fall back to whatever Pillow ships."""
    for cand in (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if Path(cand).exists():
            try:
                return ImageFont.truetype(cand, size)
            except OSError:
                continue
    return ImageFont.load_default()


def newest_per_cast(project: Path):
    """Newest `cast_<id>_*.png` for every id declared in cast.json, in cast order."""
    cast = json.loads((project / "cast.json").read_text("utf-8"))
    picks = []
    for member in cast["cast"]:
        files = sorted(glob.glob(str(project / "out" / f"cast_{member['id']}_*.png")))
        if not files:
            print(f"[{member['id']}] no render found; skipped")
            continue
        picks.append((member["name"].split("（")[0], Path(files[-1])))
    return picks


def build(picks, out: Path, cols: int, cell_w: int):
    if not picks:
        raise SystemExit("nothing to lay out")
    rows = (len(picks) + cols - 1) // cols

    tiles = []
    for label, path in picks:
        img = Image.open(path).convert("RGB")
        cell_h = int(cell_w * img.height / img.width)
        tiles.append((label, img.resize((cell_w, cell_h), Image.LANCZOS), cell_h))
    cell_h = max(t[2] for t in tiles)

    W = PAD + cols * (cell_w + PAD)
    H = PAD + rows * (cell_h + LABEL_H + PAD)
    sheet = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(sheet)
    font = load_font(30)

    for i, (label, img, h) in enumerate(tiles):
        r, c = divmod(i, cols)
        x = PAD + c * (cell_w + PAD)
        y = PAD + r * (cell_h + LABEL_H + PAD)
        sheet.paste(img, (x, y + (cell_h - h) // 2))
        draw.text((x + 4, y + cell_h + 12), label, fill=(20, 20, 24), font=font)

    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    print(f"wrote {out}  ({sheet.width}x{sheet.height}, {len(tiles)} tiles)")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project")
    ap.add_argument("--kind", choices=["cast"], default="cast")
    ap.add_argument("--files", help="comma-separated explicit images instead of cast picks")
    ap.add_argument("--out")
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--cell-width", type=int, default=460)
    args = ap.parse_args()

    if args.files:
        picks = [(Path(f).stem, Path(f)) for f in args.files.split(",") if f.strip()]
        out = Path(args.out or "contact_sheet.png")
    else:
        if not args.project:
            raise SystemExit("--project or --files is required")
        project = PROJECTS / args.project
        picks = newest_per_cast(project)
        out = Path(args.out) if args.out else project / "out" / "contact_cast.png"
        if args.out and not out.is_absolute():
            out = project / args.out
    build(picks, out, args.cols, args.cell_width)


if __name__ == "__main__":
    main()
