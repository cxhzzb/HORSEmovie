# -*- coding: utf-8 -*-
"""装完之后的本机补丁：跨平台路径归一化 + 工作流注册表指向本机副本。

为什么需要单独一步：安装会从仓库【整体重拷】comfy.py 和 pipelines.json，
手工改的会被覆盖，所以这些改动必须脚本化、可重复执行。

    python patch_local.py [--skills <skill根>] [--dry-run]

幂等：重复执行不会叠加，也不会报错。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


class P:
    def __init__(self, dry: bool):
        self.dry = dry
        self.changed = 0
        self.skipped = 0

    def log(self, mark: str, msg: str) -> None:
        print(f"  {mark} {msg}")

    def sub(self, path: Path, old: str, new: str, tag: str,
            marker: str | None = None) -> bool:
        text = path.read_text(encoding="utf-8")
        if marker and marker in text:
            self.log("·", f"{path.name}: {tag} 已应用，跳过")
            self.skipped += 1
            return False
        if old not in text:
            self.log("✗", f"{path.name}: {tag} 锚点未找到")
            return False
        if not self.dry:
            path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
        self.changed += 1
        self.log("✓", f"{path.name}: {tag}")
        return True


PATCH_COMFY = [
    # 1) 路径分隔符按平台归一（原实现把 \\ 一律转成 /，那是 Linux 的假设；
    #    Windows 版 ComfyUI 暴露的模型名用反斜杠）
    ('''def norm_value(v):
    """Models shared from Windows carry backslash paths; the server wants '/'."""
    if isinstance(v, str) and "\\\\" in v and _PATHISH.search(v):
        return v.replace("\\\\", "/")
    return v''',
     '''def norm_value(v):
    """路径分隔符按服务器平台归一。

    ComfyUI 在 Windows 上暴露的模型名用反斜杠（Minimax_H3\\\\xxx.safetensors），
    Linux 上用正斜杠。工作流是从别的机器存下来的，两边都可能出现，所以这里
    按当前平台统一，否则服务器会判 value_not_in_list。
    """
    if isinstance(v, str) and _PATHISH.search(v) and ("\\\\" in v or "/" in v):
        if os.name == "nt":
            return v.replace("/", "\\\\")
        return v.replace("\\\\", "/")
    return v


def _combo_option_index(object_info: dict) -> dict:
    """{(class_type, field): [可选值...]}，只收 COMBO 输入。"""
    idx: dict[tuple[str, str], list] = {}
    for cls, spec in (object_info or {}).items():
        req = ((spec or {}).get("input") or {})
        for sect in ("required", "optional"):
            for field, val in (req.get(sect) or {}).items():
                if isinstance(val, list) and val and isinstance(val[0], list):
                    idx[(cls, field)] = val[0]
    return idx


def resolve_combo_values(api: dict, object_info: dict) -> list[tuple[str, str, str, str]]:
    """把模型名对齐到服务器【实际】提供的选项。

    跨机器时同一个模型常因量化版命名不同而对不上（如
    qwen3vl_32b_minimax_h3_int8_convrot vs qwen3vl_32b_h3_ultra_uncensored_heretic_int8_convrot）。
    这里先按平台归一化分隔符，再按带目录的相对路径 / 纯文件名匹配；能唯一对上就替换。
    """
    idx = _combo_option_index(object_info)
    fixes: list[tuple[str, str, str, str]] = []
    for nid, node in (api or {}).items():
        cls = node.get("class_type")
        for field, val in list((node.get("inputs") or {}).items()):
            if not isinstance(val, str):
                continue
            raw_opts = idx.get((cls, field))
            if not raw_opts:
                continue
            opts = [norm_value(o) for o in raw_opts]
            want = norm_value(val)
            if want in opts:
                if want != val:
                    node["inputs"][field] = want
                    fixes.append((nid, field, val, want))
                continue
            if not _PATHISH.search(val):
                continue
            wnorm = want.replace("\\\\", "/").lower()
            wleaf = wnorm.split("/")[-1]
            hits = [o for o in opts if o.replace("\\\\", "/").lower() == wnorm]
            if not hits:
                hits = [o for o in opts if o.replace("\\\\", "/").lower().endswith("/" + wnorm)]
            if not hits:
                hits = [o for o in opts if o.replace("\\\\", "/").lower().split("/")[-1] == wleaf]
            if len(hits) == 1:
                node["inputs"][field] = hits[0]
                fixes.append((nid, field, val, hits[0]))
    return fixes''',
     "平台路径归一 + 模型名对齐", "def resolve_combo_values"),

    # 2) 自动丢弃要容错：注册表写了某节点类型，而本机工作流副本可能已经不含它
    ('''def drop_nodes(api: dict, selectors):
    """Remove nodes from the prompt and unlink anything that pointed at them."""
    removed = []
    for sel in selectors or []:
        for nid in resolve_selector(api, sel):
            if nid in api:
                removed.append((nid, api[nid].get("class_type")))
                api.pop(nid)''',
     '''def drop_nodes(api: dict, selectors, tolerant: bool = False):
    """Remove nodes from the prompt and unlink anything that pointed at them.

    tolerant=True 时匹配不到的 selector 只报告不终止 —— 注册表里的 auto_drop
    写了某个节点类型，而本机工作流副本可能已经不含它（幽灵节点已被摘掉），
    那属于"已经不需要丢"，不该算失败。
    """
    removed = []
    for sel in selectors or []:
        try:
            ids = resolve_selector(api, sel)
        except SystemExit:
            if tolerant:
                print(f"drop: {sel!r} matched no node (nothing to drop)")
                continue
            raise
        for nid in ids:
            if nid in api:
                removed.append((nid, api[nid].get("class_type")))
                api.pop(nid)''',
     "自动丢弃容错", "tolerant: bool = False"),

    # 3) cmd_run：显式 --drop 严格、注册表 auto_drop 容错
    ('''    drops = list(args.drop or [])
    if entry and not args.no_auto_drop:
        auto = list(entry.get("auto_drop") or [])
        if auto:
            print(f"registry {key}: auto-dropping {auto} (DSH writes the final prompt itself)")
            drops += auto
    for nid, cls in drop_nodes(api, drops):
        print(f"dropped #{nid} ({cls})")''',
     '''    # 显式 --drop 写错要报错；注册表的 auto_drop 允许"本机已经没有这个节点"
    for nid, cls in drop_nodes(api, list(args.drop or []), tolerant=False):
        print(f"dropped #{nid} ({cls})")
    if entry and not args.no_auto_drop:
        auto = list(entry.get("auto_drop") or [])
        if auto:
            print(f"registry {key}: auto-dropping {auto} (DSH writes the final prompt itself)")
            for nid, cls in drop_nodes(api, auto, tolerant=True):
                print(f"dropped #{nid} ({cls})")''',
     "auto_drop 容错接线", "tolerant=False"),

    # 4) 提交前做模型名对齐
    ('''    if args.dry_run:
        print(json.dumps(api, ensure_ascii=False, indent=2)[:4000])
        return
    try:
        res = submit(api)''',
     '''    if args.dry_run:
        print(json.dumps(api, ensure_ascii=False, indent=2)[:4000])
        return
    # 提交前把模型名对齐到本机服务器实际提供的选项（跨机器量化版命名不同）
    for nid, field, old, new in resolve_combo_values(api, object_info()):
        print(f"resolve #{nid}.{field}: {old} -> {new}")
    try:
        res = submit(api)''',
     "提交前模型名对齐", "resolve #{nid}.{field}"),
]


VIDEO_MAIN = {
    "_local": ("⚠ 2026-10-08 移植到本机（Windows）：本机没有 H3PromptPolish / "
               "H3PromptEdit 的提供方，原始两个『加速视频流整合』文件在这台机器上"
               "出不了视频（提示词通路被幽灵节点断开）。video-main 与 i2v-minimax "
               "都指向摘掉幽灵节点后的本机副本，提示词直写 #133.prompt。原文件保持不动。"),
    "primary": True,
    "kind": "video",
    "workflow": "★本机-H3视频流整合",
    "aliases": [],
    "verified": "2026-10-08 本机（Windows + RTX 5060 Ti 16G）conversion + server validation OK，已实跑出片",
    "live_wiring": ("image-to-video #133 (MiniMaxH3ImageToVideo)，首帧 = #114，"
                    "模型 Minimax_H3/minimax_h3_ref2va_pruned_int8_convrot，8 steps，"
                    "SageAttention 补丁 #138"),
    "modes": {
        "i2v": "首帧图生 I2VA —— 本机副本只保留这一条在用（#133 mode=0）",
        "reference-to-video": "全参考模式 #316，本机副本里是 bypass（mode=4）",
        "t2v": "文生视频 T2VA",
        "fl2v": "首尾帧 FL2VA",
        "l2v": "尾帧图生 L2VA",
    },
    "patch": {
        "prompt_final": "#133.prompt",
        "seed": "#186.seed",
        "aspect_ratio": "#115.aspect_ratio",
        "megapixels": "#115.megapixels",
        "duration_s": "#135.value",
        "first_frame": "#114.image",
        "output": "#189.filename_prefix",
    },
    "notes": [
        "本机副本删掉了 8 个幽灵节点（364/365/366/367/368/369/370/371），提示词直接写 #133.prompt。",
        "原来指向的 #365/#369.text 已不存在，别再往那些字段写值。",
        "auto_drop 里的 H3PromptPolish 在本机副本里已不存在，容错逻辑会跳过（换到有节点的机器上仍能生效）。",
        "auto_set 会把文本编码器换成本机实际存在的替代件。",
        "#115 默认存的是 9:16 (Portrait Widescreen)，跑 16:9 必须显式覆盖。",
    ],
    "auto_drop": ["H3PromptPolish"],
    "auto_set": {
        "#130.clip_name": "qwen3vl_32b_h3_ultra_uncensored_heretic_int8_convrot.safetensors"
    },
    "prompt_field": "#133.prompt",
}

I2V_MINIMAX = {
    "_why": ("改用『加速视频流整合』这条（它本身就是 MiniMaxH3ImageToVideo），"
             "因为它带 MiniMaxH3MemoryEfficientSageAttentionPatch。朴素的 "
             "MiniMax_H3_I2V_Turbo8步 没有这个补丁，同一镜同参数实测 "
             "16.8 分钟 vs 5.8 分钟（2.9×）。"),
    "_local": ("⚠ 2026-10-08 移植到本机（Windows）：原文件 #368 H3PromptPolish / "
               "#369 H3PromptEdit 的节点包这台机器没有提供方，提示词通路断开。"
               "已从副本里摘掉这 8 个幽灵节点，提示词改由 DSH 直接写 #133.prompt。"
               "副本生成脚本：C:\\DEEPHARNESS\\HORSEmovie-port\\make_local_h3_workflow.py"),
    "kind": "video",
    "workflow": "★本机-H3视频流整合",
    "verified": "2026-10-08 本机：validation OK + 实跑出片（864×480 / 24fps / 124帧 / 32kHz 立体声）",
    "auto_set": {
        "#130.clip_name": "qwen3vl_32b_h3_ultra_uncensored_heretic_int8_convrot.safetensors"
    },
    "patch": {
        "first_frame": "#114.image",
        "prompt": "#133.prompt",
        "seed": "#186.seed",
        "aspect_ratio": "#115.aspect_ratio",
        "megapixels": "#115.megapixels",
        "duration_s": "#135.value",
        "output": "#189.filename_prefix",
    },
    "notes": [
        "该文件是 I2V（MiniMaxH3ImageToVideo #133）。",
        "#115 默认存的是 9:16 (Portrait Widescreen)，跑 16:9 必须显式覆盖，否则画幅是竖的。",
        "首帧用关键帧（编辑分支产物）→ 人物锁定正确；Ref2VA 全参考直出会换脸。",
        "auto_set 里的文本编码器是本机替代件：官方 qwen3vl_32b_minimax_h3_int8_convrot 全盘不存在，"
        "本机只有这个 24.55G 的同架构 abliterated 版本。",
    ],
    "fixed": {"#126.steps": 8, "#140.strength_model": 1.0},
    "_quality": ("这条无后缀的工作流默认是省时档（steps=4、LoRA 强度 0.75），画面偏软；"
                 "你推荐的『有4个提示词』用的是 steps=8、强度 1.0。这里把两个参数拉回质量档，"
                 "既保留首帧锁脸（ImageToVideo），又用满画质。"),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skills", default=str(Path.home() / ".dsh" / "skills"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    skills = Path(args.skills)
    comfy = skills / "comfyui"
    p = P(args.dry_run)

    print("=" * 70)
    print("  本机补丁（幂等，可重复执行）")
    print("=" * 70)

    f = comfy / "comfy.py"
    if not f.is_file():
        print(f"✗ 找不到 {f}")
        return 2
    print("\n[comfy.py 平台适配]")
    for old, new, tag, marker in PATCH_COMFY:
        p.sub(f, old, new, tag, marker)

    print("\n[pipelines.json 注册表]")
    j = comfy / "pipelines.json"
    data = json.loads(j.read_text(encoding="utf-8"))
    for key, payload in (("video-main", VIDEO_MAIN), ("i2v-minimax", I2V_MINIMAX)):
        if data.get(key, {}).get("workflow") == payload["workflow"]:
            p.log("·", f"pipelines.json: {key} 已指向本机副本，跳过")
            p.skipped += 1
            continue
        data[key] = payload
        p.changed += 1
        p.log("✓", f"pipelines.json: {key} -> {payload['workflow']}")
    if not args.dry_run and p.changed:
        bak = j.with_name(j.name + f".bak-{datetime.now():%Y%m%d-%H%M%S}")
        shutil.copy2(j, bak)
        j.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n改动 {p.changed} 处，跳过 {p.skipped} 处"
          + ("（dry-run，未写入）" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
