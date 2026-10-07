#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render_cast.py — render character design sheets through the user's main image pipeline.

Reads ~/comfy-projects/<project>/cast.json and drives comfy.py against the
"生图主管线" (▶▷Qwen-image21-图像编辑+生图流（整合）) txt2img branch, dropping the
edit branch so only one sampler runs.

  render_cast.py --project tianlie                 # all cast members
  render_cast.py --project tianlie --only changgeng,xihe
  render_cast.py --project tianlie --only ajiu --seed 77 --megapixels 1.5
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMFY = HERE / "comfy.py"
PIPELINES = HERE / "pipelines.json"
PROJECTS = Path.home() / "comfy-projects"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--only", help="comma-separated cast ids")
    ap.add_argument("--aspect", default="3:4 (Portrait Standard)")
    ap.add_argument("--megapixels", type=float, default=1.2)
    ap.add_argument("--steps", type=int, default=28)
    ap.add_argument("--seed", type=int, help="override every seed (for A/B on one character)")
    ap.add_argument("--timeout", type=float, default=1800)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    project = PROJECTS / args.project
    cast = json.loads((project / "cast.json").read_text("utf-8"))
    pipes = json.loads(PIPELINES.read_text("utf-8"))
    entry = pipes["image-main"]
    p = entry["patch"]
    style = cast.get("style", "")
    want = {s.strip() for s in args.only.split(",")} if args.only else None

    results = []
    for member in cast["cast"]:
        if want and member["id"] not in want:
            continue
        prompt = ", ".join(x for x in (member["prompt"], style) if x)
        seed = args.seed if args.seed is not None else member.get("seed", 1000)
        negative = cast.get("negative", "")
        argv = [
            sys.executable, str(COMFY), "run", entry["workflow"],
            "--set", f"{p['txt2img_prompt']}={json.dumps(prompt, ensure_ascii=False)}",
            "--set", f"{p['txt2img_negative']}={json.dumps(negative, ensure_ascii=False)}",
            "--set", f"{p['txt2img_aspect_ratio']}={args.aspect}",
            "--set", f"{p['txt2img_megapixels']}={args.megapixels}",
            "--set", f"{p['txt2img_steps']}={args.steps}",
            "--set", f"{p['txt2img_seed']}={seed}",
            "--set", f"{p['txt2img_output']}=dsh/{args.project}/cast_{member['id']}",
            "--wait", "--project", args.project, "--shot", f"cast_{member['id']}",
            "--timeout", str(args.timeout),
        ]
        for sel in entry.get("branch_drops", {}).get("只出图（跳过编辑分支）", []):
            argv += ["--drop", sel]
        print(f"\n=== {member['id']}  {member['name']}  seed={seed}")
        print("$ " + " ".join(argv[:6]) + " ...")
        code = 0 if args.dry_run else subprocess.call(argv)
        results.append({"id": member["id"], "seed": seed, "exit": code})

    failed = [r for r in results if r["exit"]]
    print(f"\n{len(results) - len(failed)}/{len(results)} ok -> {project / 'out'}")
    if failed:
        print("failed:", ", ".join(r["id"] for r in failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
