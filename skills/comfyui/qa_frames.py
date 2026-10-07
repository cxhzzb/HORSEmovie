#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qa_frames.py — flag keyframes the edit branch failed to repaint.

Failure modes seen in practice:
  * the model leaves part of the INPUT canvas untouched, so the light-grey base
    (237,237,237) survives as a solid block (shots 011/025/039);
  * it emits a flat, highly saturated block — a corrupted latent (shot 011);
  * it returns a studio portrait on a seamless white/grey backdrop instead of
    the shot's environment (shots 020/022).

Only the first two are unambiguous enough to test statistically: a dark
underwater plate legitimately has large near-black, low-variance regions, so
plain "low std" would false-positive on half the film. We therefore require the
flat tile to ALSO look like the base canvas, or to be a saturated solid colour,
or to be blown-out white.

  qa_frames.py --project tianlie
"""

from __future__ import annotations

import argparse
import glob
import json
import statistics
from pathlib import Path

from PIL import Image

PROJECTS = Path.home() / "comfy-projects"
GRID = 6                 # 6x6 tiles
FLAT_STD = 6.0           # a tile flatter than this counts as "solid"
BASE_GREY = 237          # the keyframe base canvas colour
GREY_TOL = 14
SAT_SPREAD = 40          # per-channel spread marking a saturated solid block
WHITE_LEVEL = 180   # any bright flat area: studio backdrop or unpainted canvas
MIN_BAD_TILES = 2        # need at least this many bad tiles to flag


def analyse(path: Path):
    img = Image.open(path).convert("RGB")
    w, h = img.size
    n = GRID * GRID
    base = sat = white = 0
    for r in range(GRID):
        for c in range(GRID):
            tile = img.crop((c * w // GRID, r * h // GRID,
                             (c + 1) * w // GRID, (r + 1) * h // GRID))
            px = list(tile.getdata())
            lum = [0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2] for p in px]
            if statistics.pstdev(lum) >= FLAT_STD:
                continue
            mean_l = statistics.mean(lum)
            if abs(mean_l - BASE_GREY) <= GREY_TOL:
                base += 1
            elif mean_l >= WHITE_LEVEL:
                white += 1
            else:
                spread = max(statistics.mean(p[i] for p in px) for i in range(3)) - \
                         min(statistics.mean(p[i] for p in px) for i in range(3))
                if spread >= SAT_SPREAD and mean_l >= 40:   # skip legit dark water/space
                    sat += 1
    return base / n, white / n, sat / n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project")
    ap.add_argument("--files")
    args = ap.parse_args()

    if args.files:
        targets = [(Path(f).stem, Path(f)) for f in args.files.split(",") if f.strip()]
    else:
        proj = PROJECTS / args.project
        plan = json.loads((proj / "shots.json").read_text("utf-8"))
        targets = []
        for s in plan["shots"]:
            hits = sorted(glob.glob(str(proj / "out" / f"{s['id']}_key_*.png")))
            if hits:
                targets.append((s["id"], Path(hits[-1])))

    flagged = []
    print(f"{'shot':<6}{'底板灰':>8}{'死白':>8}{'纯色块':>8}   verdict")
    for sid, path in targets:
        base, white, sat = analyse(path)
        bad = (base * GRID * GRID >= MIN_BAD_TILES
               or sat * GRID * GRID >= MIN_BAD_TILES
               or white * GRID * GRID >= MIN_BAD_TILES * 3)
        if bad:
            flagged.append(sid)
        print(f"{sid:<6}{base*100:>7.0f}%{white*100:>7.0f}%{sat*100:>7.0f}%   "
              f"{'✗ 没重绘/损坏' if bad else 'ok'}")
    print(f"\n需重出 {len(flagged)}/{len(targets)}：{','.join(flagged) if flagged else '无'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
