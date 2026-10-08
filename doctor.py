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
import json
import os
import re
import shutil
import sys
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


# --- 本机配置：两条路都认 -------------------------------------------------
# ① 同目录的 local.json（本仓库默认路线：install.ps1 / install.sh 写，机器相关，不入库）
# ② 可选的 `_horse/config.py` 配置层（ports/port_and_install.py 在目标机器生成）
#    有这个就用它，没有就退回 local.json / HOME —— 保证**刚 clone 下来也能直接跑**。
def _load_local() -> dict:
    f = Path(__file__).resolve().parent / "local.json"
    if f.is_file():
        try:
            # utf-8-sig：PowerShell 写出来的 JSON 可能带 BOM
            return json.loads(f.read_text("utf-8-sig"))
        except Exception:
            return {}
    return {}


def _load_horse_config():
    for p in (2, 1, 3):
        try:
            cand = Path(__file__).resolve().parents[p] / "_horse"
        except IndexError:
            continue
        if (cand / "config.py").is_file():
            _sys.path.insert(0, str(cand))
            try:
                import config as horse_config  # noqa: E402
                return horse_config
            except Exception:
                return None
    return None


LOCAL = _load_local()
HORSE = _load_horse_config()
HOME = Path(getattr(HORSE, "HOME", None) or LOCAL.get("home") or Path.home())
SKILLS_ROOT = Path(getattr(HORSE, "SKILLS", None) or LOCAL.get("skills_dir") or (HOME / ".dsh/skills"))

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
    root = SKILLS_ROOT
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
    # DSH 只认 kebab-case 的 skill 名：/^[a-z0-9]+(?:-[a-z0-9]+)*$/
    # frontmatter 写成 HORSEmovie 这种驼峰名会被**静默忽略**（目录在、文件在，但目录里看不到它）。
    for name in SKILLS:
        f = root / name / "SKILL.md"
        if not f.is_file():
            continue
        m = re.search(r"^name:\s*['\"]?([^'\"\s]+)", f.read_text("utf-8"), re.M)
        if m and not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", m.group(1)):
            rep.bad(f"{name} 的 frontmatter name 不是 kebab-case：{m.group(1)}",
                    "DSH 会静默忽略它；改成全小写连字符名（如 horsemovie）")
    if not (root / "HORSEmovie/scripts/qa_shot.py").is_file():
        rep.warn("HORSEmovie/scripts/qa_shot.py 缺失", "逐镜验收脚本，建议补上")


def check_comfy(rep: Report, comfy: Path) -> None:
    rep.head("【ComfyUI】")
    if not comfy.is_dir():
        rep.bad(f"没有 {comfy}", "先装 ComfyUI，或用 --comfy 指定位置")
        return
    rep.ok(f"ComfyUI 目录: {comfy}")
    # 启动脚本：local.json 里写的优先，其次按平台猜（Windows 常见 start_comfyui.bat）
    launcher = None
    cands = [LOCAL.get("launcher"),
             comfy / ("启动ComfyUI.bat" if os.name == "nt" else "启动ComfyUI.sh"),
             comfy / "start_comfyui.bat",
             comfy / "启动ComfyUI.sh"]
    for c in cands:
        if c and Path(c).exists():
            launcher = Path(c)
            break
    if launcher is not None:
        rep.ok(f"启动脚本 {launcher.name} 在（{launcher.parent}）")
    else:
        rep.warn("没找到启动脚本", "run_film.py 的看门狗按 local.json 的 server_argv / launcher "
                                   "拉起服务；没有它就得自己保证服务器常开")

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


def check_models(rep: Report, comfy: Path, extra_roots: list[Path] | None = None,
                 aliases: dict[str, str] | None = None) -> None:
    # ComfyUI Desktop 之类会把 models/ 拆到多个根（共享目录），
    # 用 --extra-models 追加，避免把已就位的模型报成缺失。
    roots = [comfy / "models"] + list(extra_roots or [])
    aliases = aliases or {}
    total = 0.0
    missing_required = 0
    for rel, gb, required in MODELS:
        # ① 若这台机器是 ports/port_and_install.py 装的，走它的 _horse 配置层
        p = HORSE.find_model(rel) if (HORSE is not None and hasattr(HORSE, "find_model")) else None
        # ② 默认路线：多个模型根 + 别名（量化版改名）
        if p is None:
            cands = [rel, aliases.get(rel, rel)]
            p = next((r / c for c in cands for r in roots if (r / c).is_file()), None)
        # ③ 实在没有就按同名文件在模型根里找（名字没改、只是换了子目录）
        if p is None:
            base = Path(rel).name
            p = next((f for r in roots if r.is_dir() for f in r.rglob(base)), None)
        if p is not None:
            size = p.stat().st_size / 1024 ** 3
            total += size
            note = "" if Path(rel).name == p.name else f"  ← 本机叫 {p.name}"
            flag = "" if gb is None or abs(size - gb) < max(0.3, gb * 0.15) else f"  ⚠ 体积异常（预期 {gb} GB）"
            where = "" if p.parent == comfy / "models" else f"   [{p.parent}]"
            rep.ok(f"{size:5.1f} GB  {rel}{flag}{note}{where}")
        elif required:
            rep.bad(f"缺失  {rel}",
                    "见 models.txt（也可能是量化版文件名不同，改工作流里的名字即可）")
            missing_required += 1
        else:
            rep.warn(f"没有  {rel}（可选）", "本项目未用到，可不装")
    if total:
        rep.head(f"  已就位模型合计 {total:.1f} GB（完整约 65 GB）")
    if missing_required:
        rep.head(f"  ⚠ 还缺 {missing_required} 个必需模型")


def check_server(rep: Report, launcher: str | None = None) -> None:
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
        default_start = getattr(HORSE, "COMFY_DIR", None) or "~/ComfyUI"
        start = launcher or str(default_start)
        rep.warn(f"{host} 没在跑", f"出片前先起 ComfyUI：{start}；"
                                   "run_film.py 也能自动拉起（需启动脚本在场）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--comfy", default=None,
                    help="ComfyUI 根目录（默认读同目录 local.json 的 comfy_base，或 ~/ComfyUI）")
    ap.add_argument("--extra-models", action="append", default=[], metavar="DIR",
                    help="额外的 models 根（ComfyUI Desktop 的共享模型目录等），可重复")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    # 本机覆盖：同目录的 local.json（部署时写入，上游仓库里没有）
    local: dict = {}
    lf = Path(__file__).resolve().parent / "local.json"
    if lf.is_file():
        try:
            # utf-8-sig：PowerShell 写出来的 JSON 可能带 BOM
            local = json.loads(lf.read_text("utf-8-sig"))
        except Exception:
            local = {}

    comfy = Path(args.comfy or local.get("comfy_base") or HOME / "ComfyUI").expanduser()
    extra = [Path(p).expanduser() for p in args.extra_models] \
        or [Path(p).expanduser() for p in local.get("extra_model_roots", [])]
    rep = Report(args.quiet)

    if not args.quiet:
        print("=" * 64)
        print("HORSEmovie 环境自检（只读，不会改动任何东西）")
        print("=" * 64)

    check_python(rep)
    check_skills(rep)
    check_comfy(rep, comfy)
    check_models(rep, comfy, extra, local.get("model_aliases"))
    check_server(rep, local.get("launcher"))

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
