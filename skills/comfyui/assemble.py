#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""assemble.py — spec-check the shot outputs, then build a rough cut and a review sheet.

  assemble.py --project tianlie --check            # 只核对规格，不写文件
  assemble.py --project tianlie --sheet            # 逐镜抽帧的审阅联系表
  assemble.py --project tianlie --concat           # 按镜号顺序拼成粗剪
  assemble.py --project tianlie --sheet --concat   # 两样都做

The concat step tries a stream copy first (fast, lossless) and falls back to a
re-encode that normalises size and frame rate when the shots disagree.
"""

from __future__ import annotations

import argparse
import glob
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PROJECTS = Path.home() / "comfy-projects"


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate,nb_frames",
         "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    st = d["streams"][0]
    num, _, den = st["r_frame_rate"].partition("/")
    fps = float(num) / float(den or 1)
    return {"w": st["width"], "h": st["height"], "fps": round(fps, 3),
            "frames": int(st.get("nb_frames") or 0),
            "dur": round(float(d["format"]["duration"]), 2)}


def newest_shot(sid: str, out_dir: Path) -> Path | None:
    hits = glob.glob(str(out_dir / f"{sid}_motion_*.mp4"))
    if not hits:
        return None
    return Path(max(hits, key=lambda p: Path(p).stat().st_mtime))


def collect(project: Path):
    plan = json.loads((project / "shots.json").read_text("utf-8"))
    out_dir = project / "out"
    rows = []
    for s in plan["shots"]:
        f = newest_shot(s["id"], out_dir)
        rows.append((s, f, probe(f) if f else None))
    return rows


def do_check(rows) -> int:
    missing = [s["id"] for s, f, _ in rows if f is None]
    print(f"{'shot':<6}{'size':>12}{'fps':>8}{'frames':>8}{'dur':>8}  {'plan dur':>9}  note")
    bad = []
    for s, f, info in rows:
        if not info:
            print(f"{s['id']:<6}{'-':>12}{'-':>8}{'-':>8}{'-':>8}  {s.get('dur', '?'):>9}  缺失")
            continue
        note = ""
        if (info["w"], info["h"]) != (864, 480):
            note += "画幅异常 "
            bad.append(s["id"])
        plan_dur = s.get("dur")
        if plan_dur and abs(info["dur"] - plan_dur) > 1.4:
            note += f"时长偏差{info['dur'] - plan_dur:+.1f}s "
            bad.append(s["id"])
        print(f"{s['id']:<6}{info['w']}x{info['h']:<7}{info['fps']:>8}{info['frames']:>8}"
              f"{info['dur']:>8}  {plan_dur if plan_dur else '?':>9}  {note}")
    total = sum(i["dur"] for _, _, i in rows if i)
    print(f"\n已有成片 {len(rows) - len(missing)}/{len(rows)}，总时长 {total / 60:.1f} 分钟")
    if missing:
        print("缺失：" + ",".join(missing))
    if bad:
        print("规格异常：" + ",".join(sorted(set(bad))))
    return 0 if not missing and not bad else 1


def do_sheet(rows, dest: Path, cell_w: int = 320) -> Path:
    tiles = []
    for s, f, info in rows:
        if not f:
            continue
        tmp = Path(tempfile.mkdtemp()) / f"{s['id']}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-ss", str(max(0.5, (info["dur"] or 2) / 2)),
                        "-i", str(f), "-frames:v", "1", str(tmp), "-y"], check=True)
        im = Image.open(tmp).convert("RGB")
        im = im.resize((cell_w, int(cell_w * im.height / im.width)), Image.LANCZOS)
        tiles.append((s["id"], im))
        shutil.rmtree(tmp.parent, ignore_errors=True)
    if not tiles:
        raise SystemExit("没有可用的成片")
    cols = 5
    lab = 30
    cw, ch = tiles[0][1].size
    rows_n = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (cw + 8) + 8, rows_n * (ch + lab + 8) + 8), (250, 250, 252))
    draw = ImageDraw.Draw(sheet)
    font = None
    for cand in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"):
        if Path(cand).exists():
            font = ImageFont.truetype(cand, 20)
            break
    for k, (sid, im) in enumerate(tiles):
        r, c = divmod(k, cols)
        x, y = 8 + c * (cw + 8), 8 + r * (ch + lab + 8)
        sheet.paste(im, (x, y))
        draw.text((x + 2, y + ch + 4), f"{sid}  mid-frame", fill=(30, 30, 34),
                  font=font or ImageFont.load_default())
    sheet.save(dest)
    print(f"wrote {dest}  ({sheet.width}x{sheet.height}, {len(tiles)} 镜)")
    return dest


def do_concat(rows, dest: Path) -> Path:
    parts = [f for _, f, _ in rows if f]
    if not parts:
        raise SystemExit("没有可用的成片")
    specs = {tuple(sorted(probe(p).items() - [("dur", 0), ("frames", 0)])) for p in parts}
    listing = Path(tempfile.mkdtemp()) / "list.txt"
    listing.write_text("".join(f"file '{p.resolve()}'\n" for p in parts), "utf-8")

    copy_cmd = ["ffmpeg", "-v", "error", "-f", "concat", "-safe", "0",
                "-i", str(listing), "-c", "copy", str(dest), "-y"]
    if len(specs) == 1 and subprocess.call(copy_cmd) == 0:
        print(f"wrote {dest}  (流拷贝，{len(parts)} 镜)")
        return dest

    print("规格不一致，改用重编码拼接 ...")
    subprocess.check_call([
        "ffmpeg", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
        "-vf", "scale=864:480:force_original_aspect_ratio=decrease,"
               "pad=864:480:(ow-iw)/2:(oh-ih)/2,fps=24",
        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-c:a", "aac", "-b:a", "192k", str(dest), "-y"])
    print(f"wrote {dest}  (重编码，{len(parts)} 镜)")
    return dest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--sheet", action="store_true")
    ap.add_argument("--concat", action="store_true")
    ap.add_argument("--out", help="粗剪输出名（默认 <project>/out/<project>_rough_cut.mp4）")
    args = ap.parse_args()
    if not (args.check or args.sheet or args.concat):
        args.check = True

    project = PROJECTS / args.project
    rows = collect(project)
    rc = 0
    if args.check:
        rc = do_check(rows)
    if args.sheet:
        do_sheet(rows, project / "out" / "shots_review_sheet.png")
    if args.concat:
        dest = Path(args.out) if args.out else project / "out" / f"{args.project}_rough_cut.mp4"
        do_concat(rows, dest)
    return rc


if __name__ == "__main__":
    sys.exit(main())
