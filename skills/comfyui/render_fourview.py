#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render_fourview.py — character turnaround sheets with Qwen-Image 2.1 (edit branch).

Recipe that works (verified 2026-10-05 on the user's 生图主管线):
  1. build a landscape base canvas with the approved portrait pasted on the left,
  2. feed it to the EDIT branch of ▶▷Qwen-image21-图像编辑+生图流（整合）,
  3. ask for an explicit four-column layout: face close-up | front | side | back,
  4. keep denoise at 1 (the TE-Speed cache nodes collapse the image below 1).

The edit branch's output size follows the INPUT image's aspect ratio, so the
landscape base is what makes the sheet landscape. Do not use the Krea2
four-view workflow — the user rejected that model's quality.

  render_fourview.py --project tianlie
  render_fourview.py --project tianlie --only xihe,ajiu
"""

from __future__ import annotations

import argparse
import glob
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
COMFY = HERE / "comfy.py"
PIPELINES = HERE / "pipelines.json"
PROJECTS = Path.home() / "comfy-projects"

LAYOUT_PROMPT = (
    "把这张图处理成一张标准的角色设定板，画面从左到右横向分成四个等宽的格子："
    "第1格是角色的面部特写肖像（头部与肩部），第2格是同一个角色的正面全身视图，"
    "第3格是侧面全身视图，第4格是背面全身视图。四个格子里的角色必须是同一个人："
    "长相、脸型、发型、肤色、服装款式与材质细节完全一致。"
    "第2、3、4格的三个全身视图高度一致、脚底在同一水平线上。"
    "整体是纯浅灰色背景，均匀柔和的影棚打光，无地面阴影，写实真人电影质感。"
    "画面内不出现任何文字、字母、标签或水印。"
)


def build_base(portrait: Path, dest: Path, w: int, h: int, height_ratio: float, bg=(237, 237, 237)) -> Path:
    """Landscape canvas with the portrait occupying the left third."""
    img = Image.open(portrait).convert("RGB")
    canvas = Image.new("RGB", (w, h), bg)
    ph = int(h * height_ratio)
    pw = int(img.width * ph / img.height)
    canvas.paste(img.resize((pw, ph), Image.LANCZOS), (int(w * 0.025), (h - ph) // 2))
    canvas.save(dest)
    return dest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--only", help="comma-separated cast ids")
    ap.add_argument("--prompt", default=LAYOUT_PROMPT)
    ap.add_argument("--width", type=int, default=1536)
    ap.add_argument("--height", type=int, default=1024)
    ap.add_argument("--portrait-height-ratio", type=float, default=0.92)
    ap.add_argument("--megapixels", type=float, default=1.5)
    ap.add_argument("--steps", type=int, default=30)
    ap.add_argument("--seed-base", type=int, default=4400)
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    sys.path.insert(0, str(HERE))
    import comfy  # noqa: E402

    project = PROJECTS / args.project
    cast = json.loads((project / "cast.json").read_text("utf-8"))
    pipes = json.loads(PIPELINES.read_text("utf-8"))
    entry = pipes["qwen-fourview"]
    p = entry["patch"]
    want = {s.strip() for s in args.only.split(",")} if args.only else None

    results = []
    for index, member in enumerate(cast["cast"]):
        cid = member["id"]
        if want and cid not in want:
            continue
        portraits = sorted(glob.glob(str(project / "out" / f"cast_{cid}_*.png")))
        if not portraits:
            print(f"[{cid}] no portrait in {project/'out'}; render the cast first")
            continue
        base = project / "out" / f"_sheet_base_{cid}.png"
        build_base(Path(portraits[-1]), base, args.width, args.height, args.portrait_height_ratio)
        seed = args.seed_base + index + 1
        if args.dry_run:
            name = f"dry/{base.name}"
        else:
            resp = comfy.upload_image(str(base), "tianlie_sheets")
            sub = resp.get("subfolder") or ""
            name = f"{sub}/{resp['name']}" if sub else resp["name"]
        argv = [
            sys.executable, str(COMFY), "run", entry["workflow"],
            "--set", f"{p['image']}={json.dumps(name)}",
            "--set", f"{p['prompt']}={json.dumps(args.prompt, ensure_ascii=False)}",
            "--set", f"{p['negative']}={json.dumps(cast.get('negative', ''), ensure_ascii=False)}",
            "--set", f"{p['megapixels']}={args.megapixels}",
            "--set", f"{p['resolution']}=1024",
            "--set", f"{p['steps']}={args.steps}",
            "--set", f"{p['seed']}={seed}",
            "--set", f"{p['output']}=dsh/{args.project}/fourview_{cid}",
        ]
        for sel in entry.get("drop", []):
            argv += ["--drop", sel]
        argv += ["--wait", "--project", args.project, "--shot", f"fourview_{cid}",
                 "--timeout", str(args.timeout)]
        print(f"\n=== fourview {cid}  {member['name']}  base={base.name}  seed={seed}")
        code = 0 if args.dry_run else subprocess.call(argv)
        results.append({"id": cid, "seed": seed, "exit": code})

    failed = [r for r in results if r["exit"]]
    print(f"\n{len(results) - len(failed)}/{len(results)} ok -> {project / 'out'}")
    if failed:
        print("failed:", ", ".join(r["id"] for r in failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
