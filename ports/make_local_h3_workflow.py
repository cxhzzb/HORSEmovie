# -*- coding: utf-8 -*-
"""为这台机器生成一个不依赖 H3PromptEdit / H3PromptPolish 的 H3 视频工作流副本。

背景：`▶▷MiniMaxH3-加速视频流整合` 的提示词通路是
    #368 H3PromptPolish -> #369 H3PromptEdit -> #133.prompt
但这两个节点在本机【没有任何提供方】（全盘搜索只在这两个工作流 JSON 里出现），
所以原文件在这台机器上无法出视频。

修法：把这两个节点从副本里摘掉，提示词由 DSH 直接写进视频节点的 prompt 输入。
原文件不动。

    python make_local_h3_workflow.py [--dry-run]
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

WF_DIR = Path(r"C:\COMFYUI\Desktop\ComfyUI-Installs\ComfyUI\ComfyUI"
              r"\user\default\workflows")
SRC = WF_DIR / "▶▷MiniMaxH3-加速视频流整合.json"
DST = WF_DIR / "★本机-H3视频流整合（无H3Prompt节点）.json"

# 本机没有提供方的节点，摘掉
GHOST = {"H3PromptPolish", "H3PromptEdit"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not SRC.is_file():
        print(f"✗ 找不到源工作流: {SRC}")
        return 2

    doc = json.loads(SRC.read_text(encoding="utf-8"))
    nodes = doc.get("nodes", [])
    print(f"源文件: {SRC.name}")
    print(f"  节点 {len(nodes)}")

    ghost_ids = {n["id"] for n in nodes if n.get("type") in GHOST}
    print(f"  待摘除的幽灵节点 {len(ghost_ids)} 个: {sorted(ghost_ids)}")
    if not ghost_ids:
        print("  已经是干净的，无需处理")
        return 0

    # --- 摘掉幽灵节点，以及所有指向它们的连线 -----------------------------
    links = doc.get("links", [])
    kept_links, dropped_links = [], 0
    for l in links:
        if isinstance(l, list) and len(l) >= 5:
            if l[1] in ghost_ids or l[3] in ghost_ids:      # 源或目标中招
                dropped_links += 1
                continue
        kept_links.append(l)
    doc["links"] = kept_links
    print(f"  连线 {len(links)} -> {len(kept_links)}（删 {dropped_links}）")

    kept_nodes = []
    for n in nodes:
        if n.get("id") in ghost_ids:
            continue
        # 清理残留的输入/输出槽内连线引用
        for key in ("inputs", "outputs"):
            for slot in n.get(key) or []:
                if isinstance(slot, dict) and slot.get("link") in {None}:
                    continue
                if isinstance(slot, dict) and isinstance(slot.get("links"), list):
                    slot["links"] = [x for x in slot["links"] if x not in ghost_ids]
        kept_nodes.append(n)
    doc["nodes"] = kept_nodes

    # --- 让视频节点的 prompt 回到可控状态 ---------------------------------
    # H3PromptEdit 把字符串喂给 video node 的 prompt 输入；节点没了就该由
    # bridge 直接写这个输入，所以把转换后的 widget 值清空，避免和 --set 打架。
    video_nodes = [n for n in kept_nodes
                   if n.get("type") in ("MiniMaxH3ImageToVideo",
                                        "MiniMaxH3ReferenceToVideo",
                                        "MiniMaxH3T2V")]
    for n in video_nodes:
        mode = n.get("mode", 0)
        wv = n.get("widgets_values")
        if isinstance(wv, list) and wv:
            old = wv[0]
            wv[0] = ""
            print(f"  视频节点 #{n['id']} {n['type']} mode={mode} "
                  f"prompt 清空（原 {str(old)[:40]!r}）")

    # --- 记录来源，方便日后溯源 ------------------------------------------
    doc.setdefault("extra", {})
    if isinstance(doc.get("extra"), dict):
        doc["extra"]["horse_local_patch"] = {
            "generated_by": "make_local_h3_workflow.py",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "source": SRC.name,
            "removed_node_types": sorted(GHOST),
            "removed_node_ids": sorted(ghost_ids),
            "why": "本机没有 H3PromptPolish / H3PromptEdit 的提供方；"
                   "提示词改由 DSH 直接写视频节点的 prompt 输入。",
        }

    text = json.dumps(doc, ensure_ascii=False, indent=2)
    if args.dry_run:
        print(f"\n[dry-run] 将写出 {DST.name}（{len(text)} 字符）")
        return 0

    if DST.exists():
        bak = DST.with_name(DST.name + f".bak-{datetime.now():%Y%m%d-%H%M%S}")
        shutil.copy2(DST, bak)
        print(f"  旧的副本已备份为 {bak.name}")
    DST.write_text(text, encoding="utf-8")
    print(f"\n✓ 写出 {DST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
