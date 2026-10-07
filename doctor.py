#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""doctor.py — check whether this machine can run the HORSEmovie pipeline.

Read-only: it reports, it never installs or modifies anything.

  python3 doctor.py
  python3 doctor.py --comfy ~/ComfyUI      # 非默认位置的 ComfyUI
  python3 doctor.py --quiet                # 只打印问题

Exit code 0 = ready, 1 = something is missing.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

HOME = Path.home()

# ---- 期望存在的东西（体积单位 GB，None = 不校验大小）-----------------------
MODELS = [
    ("diffusion_models/Minimax_H3/minimax_h3_ref2va_pruned_int8_convrot.safetensors", 19.5, True),
    ("text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors", 25.3, True),
    ("vae/minimax_h3_video_vae_fp16.safetensors", 4.8, True),
    ("vae/minimax_h3_audio_vae_fp32.safetensors", 0.5, True),
    ("loras/minimax_h3_turbo_v4_step600_ema_pruned_comfyui.safetensors", 0.5, True),
    ("diffusion_models/qwen-image-2.1-UC-int8_convrot.safetensors", 6.7, True),
    ("text_encoders/qwen3vl_8b_int8_convrot.safetensors", 8.7, True),
    ("vae/qwen_image_2.1_vae_bf16.safetensors", 0.6, True),
    # 可选
    ("diffusion_models/Minimax_H3/minimax_h3_fl2va_pruned_int8_convrot.safetensors", None, False),
    ("loras/minimax_h3/minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors", None, False),
]

WORKFLOWS = [
    "▶▷MiniMaxH3-加速视频流整合.json",
    "▶▷MiniMaxH3-加速视频流整合 有4个提示词.json",
    "▶▷Qwen-image21-图像编辑+生图流（整合）.json",
]

CUSTOM_NODES = [
    ("ComfyUI-MiniMax-H3", True, "H3 视频节点 + SageAttention 加速补丁"),
    ("rgthree-comfy", True, "Seed / Fast Groups Bypasser / Image Comparer"),
    ("ComfyUI-KJNodes", True, "ImageResizeKJv2（首帧规格化）"),
    ("Comfyui-Memory_Cleanup", True, "VRAMCleanup / RAMCleanup（8G 显存必需）"),
    ("ComfyUI-GGUF", False, "GGUF 模型加载"),
    ("H3PromptPolish", False, "H3PromptPolish 节点（bridge 会 auto_drop，但要能加载）"),
    ("comfyUI-llama-TE", False, "文本编码器加速"),
    ("TE_MAN", False, "文本编码器加速"),
    ("ComfyUI-VOSR2", False, "超分管线"),
    ("ComfyUI_VNCCS_Utils", False, "VNCCS_PoseStudio（姿态控制）"),
]

SKILLS = ["HORSEmovie", "comfyui"]

OK, BAD, WARN = "  ✓", "  ✗", "  ⚠"


class Report:
    def __init__(self, quiet: bool):
        self.quiet = quiet
        self.problems: list[str] = []
        self.warnings: list[str] = []

    def head(self, text: str) -> None:
        if not self.quiet:
            print(f"\n{text}")

    def ok(self, text: str) -> None:
        if not self.quiet:
            print(f"{OK} {text}")

    def bad(self, text: str, fix: str = "") -> None:
        print(f"{BAD} {text}")
        if fix:
            print(f"      → {fix}")
        self.problems.append(text)

    def warn(self, text: str, fix: str = "") -> None:
        if not self.quiet:
            print(f"{WARN} {text}")
            if fix:
                print(f"      → {fix}")
        self.warnings.append(text)


def check_python(rep: Report) -> None:
    rep.head("【Python 依赖】")
    v = sys.version_info
    if v >= (3, 10):
        rep.ok(f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        rep.bad(f"Python {v.major}.{v.minor} 过低", "需要 3.10+")
    for mod, why in (("numpy", "qa_shot.py 的音频分析"),
                     ("PIL", "qa_shot.py / contact_sheet.py 出图")):
        try:
            __import__(mod)
            rep.ok(f"{mod} 已安装（{why}）")
        except ImportError:
            rep.bad(f"缺少 {mod}", f"pip install {mod} —— {why} 需要它")


def check_skills(rep: Report) -> None:
    rep.head("【DSH skills】")
    root = HOME / ".dsh/skills"
    if not root.is_dir():
        rep.bad(f"没有 {root}", "这台机器可能没装 DSH；skill 必须在 DSH 里才能用")
        return
    for name in SKILLS:
        p = root / name / "SKILL.md"
        if p.is_file():
            rep.ok(f"{name}")
        elif (root / name).is_dir():
            rep.bad(f"{name} 目录在，但没有 SKILL.md",
                    f"层级错了，应该是 {root}/{name}/SKILL.md")
        else:
            rep.bad(f"缺少 skill: {name}",
                    f"cp -r skills/{name} {root}/")
    if not (root / "HORSEmovie/scripts/qa_shot.py").is_file():
        rep.warn("HORSEmovie/scripts/qa_shot.py 缺失", "逐镜验收脚本，建议补上")


def check_comfy(rep: Report, comfy: Path) -> None:
    rep.head("【ComfyUI】")
    if not comfy.is_dir():
        rep.bad(f"没有 {comfy}", "先装 ComfyUI，或用 --comfy 指定位置")
        return
    rep.ok(f"ComfyUI 目录: {comfy}")
    launcher = comfy / "启动ComfyUI.sh"
    if launcher.exists():
        rep.ok("启动脚本 启动ComfyUI.sh 在")
    else:
        rep.warn("没找到 启动ComfyUI.sh", "run_film.py 的看门狗按这个脚本的逻辑拉起服务；"
                                          "没有它就得自己保证服务器常开")

    rep.head("【工作流文件】")
    wf_dir = comfy / "user/default/workflows"
    if not wf_dir.is_dir():
        rep.bad(f"没有 {wf_dir}", "ComfyUI 跑过一次才会生成这个目录")
    else:
        for w in WORKFLOWS:
            if (wf_dir / w).is_file():
                rep.ok(w)
            else:
                rep.bad(f"缺少工作流 {w}", "cp comfyui-workflows/*.json 到这里")

    rep.head("【自定义节点】")
    cn = comfy / "custom_nodes"
    for name, required, why in CUSTOM_NODES:
        if (cn / name).is_dir():
            rep.ok(f"{name}（{why}）")
        elif required:
            rep.bad(f"缺少必需节点包 {name}", f"{why} —— 用 ComfyUI-Manager 装")
        else:
            rep.warn(f"没有 {name}", why)


def check_models(rep: Report, comfy: Path) -> None:
    rep.head("【模型】")
    mroot = comfy / "models"
    total = 0.0
    missing_required = 0
    for rel, gb, required in MODELS:
        p = mroot / rel
        if p.is_file():
            size = p.stat().st_size / 1024 ** 3
            total += size
            flag = "" if gb is None or abs(size - gb) < max(0.3, gb * 0.15) else f"  ⚠ 体积异常（预期 {gb} GB）"
            rep.ok(f"{size:5.1f} GB  {rel}{flag}")
        elif required:
            rep.bad(f"缺失  {rel}", "见 models.txt")
            missing_required += 1
        else:
            rep.warn(f"没有  {rel}（可选）", "本项目未用到，可不装")
    if total:
        rep.head(f"  已就位模型合计 {total:.1f} GB（完整约 65 GB）")
    if missing_required:
        rep.head(f"  ⚠ 还缺 {missing_required} 个必需模型")


def check_server(rep: Report) -> None:
    rep.head("【ComfyUI 服务】")
    host = os.environ.get("COMFY_HOST", "127.0.0.1:8188")
    try:
        with urllib.request.urlopen(f"http://{host}/system_stats", timeout=4) as r:
            import json
            d = json.loads(r.read().decode())
            dev = (d.get("devices") or [{}])[0]
            rep.ok(f"{host} 在线 — {dev.get('name', '?')}，"
                   f"显存 {(dev.get('vram_total', 0) - dev.get('vram_free', 0)) / 1024**3:.1f} / "
                   f"{dev.get('vram_total', 0) / 1024**3:.1f} GB 已用")
    except (urllib.error.URLError, OSError, TimeoutError):
        rep.warn(f"{host} 没在跑", "出片前先 ~/ComfyUI/启动ComfyUI.sh；"
                                   "run_film.py 也能自动拉起（需 启动ComfyUI.sh 在场）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--comfy", default=str(HOME / "ComfyUI"))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    comfy = Path(args.comfy).expanduser()
    rep = Report(args.quiet)

    if not args.quiet:
        print("=" * 64)
        print("HORSEmovie 环境自检（只读，不会改动任何东西）")
        print("=" * 64)

    check_python(rep)
    check_skills(rep)
    check_comfy(rep, comfy)
    check_models(rep, comfy)
    check_server(rep)

    print("\n" + "=" * 64)
    if rep.problems:
        print(f"结论：还不能跑 —— {len(rep.problems)} 个问题待解决")
        for p in rep.problems:
            print(f"  ✗ {p}")
        if rep.warnings:
            print(f"（另有 {len(rep.warnings)} 条提醒，不影响出片）")
        return 1
    print("结论：环境就绪 ✓")
    if rep.warnings:
        print(f"（{len(rep.warnings)} 条提醒，不影响出片）")
    print("下一步：按 INSTALL.md 第 6 节跑一次冒烟测试，再开正式生产。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
