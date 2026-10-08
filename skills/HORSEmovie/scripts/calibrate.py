#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""calibrate.py — measure what THIS machine can actually do, and print a spec table.

The numbers in SKILL.md (0.8 MP, 8 s, 8 steps, ~8 min/shot) were calibrated on an
RTX 4070 Laptop with 8 GB VRAM. On different hardware they are wrong in both
directions: a bigger card affords more resolution and longer takes, and the
model files that were a bad idea on 8 GB (fp16 / nvfp4) may become the right
ones. Run this once on the new machine and let it rewrite the table.

It renders a real shot at increasing cost, timing each attempt, and stops when a
config fails, the budget runs out, or the node refuses the duration.

  calibrate.py --project tianlie --shot 008
  calibrate.py --project tianlie --shot 008 --mp 0.4,0.8,1.0,1.5 --dur 5,8,12
  calibrate.py --project tianlie --shot 008 --budget 60      # 最多花 60 分钟
  calibrate.py --project tianlie --shot 008 --dry-run        # 只打印计划
"""

from __future__ import annotations

import argparse
import glob
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


# --- Windows: the console codepage (GBK/cp936) cannot encode '▶' or Chinese
# text, which every workflow name here contains. Force UTF-8 on stdout/stderr;
# errors="replace" so a report never dies half-printed.
import sys as _sys

for _stream in (_sys.stdout, _sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = Path(__file__).resolve().parent
COMFY_SKILL = Path.home() / ".dsh/skills/comfyui"
RENDER = COMFY_SKILL / "render_shots.py"
PROJECTS = Path.home() / "comfy-projects"
HOST = "127.0.0.1:8188"

# 合法帧数 = 5 + 17k，24 fps
VALID_SECONDS = [5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]

# 工作流 Note #118 的对照表（multiple=32）；新机器上 megapixels 仍按同公式换算
MP_TABLE = {0.2: (608, 352), 0.3: (736, 416), 0.4: (864, 480), 0.5: (960, 544),
            0.6: (1056, 608), 0.7: (1152, 640), 0.8: (1216, 672), 0.9: (1280, 736),
            1.0: (1376, 768), 1.2: (1504, 832), 1.5: (1664, 928), 1.8: (1824, 1024),
            2.0: (1920, 1088)}


def gpu_report() -> dict:
    """Ask ComfyUI what the device is; fall back to nvidia-smi."""
    out = {}
    try:
        with urllib.request.urlopen(f"http://{HOST}/system_stats", timeout=6) as r:
            d = json.loads(r.read().decode())
            dev = (d.get("devices") or [{}])[0]
            out["name"] = dev.get("name", "?")
            out["vram_total_gb"] = round(dev.get("vram_total", 0) / 1024 ** 3, 1)
            out["torch"] = (d.get("system") or {}).get("pytorch_version", "?")
    except (urllib.error.URLError, OSError, TimeoutError):
        pass
    try:
        q = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,compute_cap",
             "--format=csv,noheader"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10).stdout.strip()
        if q:
            name, mem, cap = [x.strip() for x in q.split(",")]
            out.setdefault("name", name)
            out["sm"] = cap
            if "vram_total_gb" not in out:
                out["vram_total_gb"] = round(float(mem.split()[0]) / 1024, 1)
            # Blackwell (sm_120 / 12.x) 原生支持 FP4
            try:
                out["native_fp4"] = float(cap.split(".")[0]) >= 12
            except ValueError:
                pass
    except Exception:
        pass
    return out


def run_one(project: str, shot: str, mp: float, dur: int, seed: int,
            dry: bool, timeout: float) -> tuple[bool, float, str]:
    cmd = [sys.executable, str(RENDER), "--project", project, "--shots", shot,
           "--stage", "motion", "--via", "i2v",
           "--megapixels", str(mp), "--seed", str(seed), "--timeout", str(timeout)]
    if dry:
        cmd.append("--dry-run")
        print("   $ " + " ".join(cmd))
        return True, 0.0, "(dry-run)"
    # 时长不在 CLI 上，临时改 shots.json 里的 dur
    sj = PROJECTS / project / "shots.json"
    plan = json.loads(sj.read_text("utf-8"))
    target = next((s for s in plan["shots"] if s["id"] == shot), None)
    if target is None:
        return False, 0.0, f"shots.json 里没有 {shot}"
    old = target.get("dur")
    target["dur"] = dur
    sj.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", "utf-8")

    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    el = time.time() - t0

    target["dur"] = old                       # 还原
    sj.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", "utf-8")

    ok = r.returncode == 0 and "ok ->" in (r.stdout or "")
    tail = (r.stderr or "").strip().splitlines()
    msg = tail[-1][:110] if not ok and tail else ("ok" if ok else "失败")
    return ok, el, msg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--shot", required=True, help="用哪一镜做基准（要有 key_*.png 或场景图）")
    ap.add_argument("--mp", default="0.4,0.6,0.8,1.0",
                    help="要试的 megapixels 档位，逗号分隔（递增）")
    ap.add_argument("--dur", default="5,8,11",
                    help="要试的时长（秒，必须是 5..15 的整数）")
    ap.add_argument("--budget", type=float, default=90, help="总时间预算（分钟）")
    ap.add_argument("--timeout", type=float, default=3600, help="单次渲染超时（秒）")
    ap.add_argument("--out", help="把结果 JSON 写到哪")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    mps = [float(x) for x in args.mp.split(",") if x.strip()]
    durs = [int(x) for x in args.dur.split(",") if x.strip()]
    for d in durs:
        if d not in VALID_SECONDS:
            print(f"!! {d} 秒不合法。合法值: {VALID_SECONDS}")
            return 2

    gpu = gpu_report()
    print("=" * 68)
    print("HORSEmovie 硬件标定")
    print("=" * 68)
    print(f"设备      : {gpu.get('name', '未知')}")
    print(f"显存      : {gpu.get('vram_total_gb', '?')} GB")
    if "sm" in gpu:
        print(f"算力      : sm_{gpu['sm']}" +
              ("（原生支持 FP4 —— nvfp4 模型可能比 int8 更快）" if gpu.get("native_fp4")
               else "（无原生 FP4，继续用 int8 量化版）"))
    if gpu.get("torch"):
        print(f"torch     : {gpu['torch']}")
    print(f"基准镜    : {args.project} / {args.shot}")
    print(f"预算      : {args.budget:.0f} 分钟")
    print()

    # 由小到大排：先确认最省的配置能跑通，再往上顶
    matrix = sorted(((mp, d) for mp in mps for d in durs),
                    key=lambda x: (x[0] * x[1], x[0], x[1]))
    print(f"计划 {len(matrix)} 个配置，由小到大：")
    for mp, d in matrix:
        wh = MP_TABLE.get(mp)
        size = f"{wh[0]}×{wh[1]}" if wh else "?"
        frames = 5 + 17 * round(d * 24 / 17)
        print(f"   {mp} MP  {size:>9}  {d:>2}s → {frames:>3} 帧")
    print()

    results, spent, seed = [], 0.0, 90000
    for mp, d in matrix:
        if spent >= args.budget * 60:
            print(f"— 预算用尽（已花 {spent/60:.1f} 分钟），停止 —")
            break
        wh = MP_TABLE.get(mp)
        size = f"{wh[0]}×{wh[1]}" if wh else "?"
        print(f"▶ {mp} MP / {size} / {d}s ...", flush=True)
        ok, el, msg = run_one(args.project, args.shot, mp, d, seed, args.dry_run, args.timeout)
        seed += 1
        spent += el
        results.append({"mp": mp, "size": size, "dur": d, "ok": ok,
                        "seconds": round(el, 1), "note": msg})
        if args.dry_run:
            print("   (dry-run)")
            continue
        print(f"   {'✓ 通过' if ok else '✗ 失败'}  {el/60:.1f} 分钟  {msg}")
        if not ok:
            print(f"   → {mp} MP @ {d}s 是这台机器的上限附近，更贵的配置不再尝试")

    if args.dry_run:
        return 0

    good = [r for r in results if r["ok"]]
    print("\n" + "=" * 68)
    print("标定结果")
    print("=" * 68)
    if not good:
        print("没有任何配置通过 —— 先检查服务器、模型和工作流，再重跑。")
        return 1

    print(f"{'档位':>6}{'分辨率':>12}{'时长':>6}{'帧':>6}{'耗时':>10}")
    for r in good:
        frames = 5 + 17 * round(r["dur"] * 24 / 17)
        print(f"{r['mp']:>6}{r['size']:>12}{r['dur']:>5}s{frames:>6}{r['seconds']/60:>9.1f}分")

    # 推荐：在"单镜 ≤ 12 分钟"的约束下取最贵的一档
    pick = None
    for r in sorted(good, key=lambda x: (x["mp"] * x["dur"], x["mp"]), reverse=True):
        if r["seconds"] <= 12 * 60:
            pick = r
            break
    if pick is None:
        pick = min(good, key=lambda x: x["seconds"])

    print("\n推荐标准：")
    print(f"   --megapixels {pick['mp']}   时长 {pick['dur']} 秒（{pick['size']}）")
    print(f"   单镜约 {pick['seconds']/60:.1f} 分钟 → 45 镜约 "
          f"{pick['seconds']*45/3600:.1f} 小时")
    failed = [r for r in results if not r["ok"]]
    if failed:
        worst = min(failed, key=lambda x: x["mp"] * x["dur"])
        print(f"   上限附近：{worst['mp']} MP @ {worst['dur']}s 失败 —— 不要超过它")

    print("\n把上面两行填回 SKILL.md 的「已锁定的生产标准」表。")
    print("注意：分辨率/时长是硬件相关的，steps=8 与 LoRA 强度 1.0 是质量相关的，"
          "换机器也要保持。")

    if args.out:
        Path(args.out).expanduser().write_text(
            json.dumps({"gpu": gpu, "results": results, "recommended": pick},
                       ensure_ascii=False, indent=2) + "\n", "utf-8")
        print(f"\n明细已写到 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
