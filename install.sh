#!/usr/bin/env bash
# HORSEmovie 一键安装：把能自动化的都做掉，剩下的（模型/自定义节点）列清单给你。
#
#   ./install.sh                 # 装到默认位置
#   ./install.sh --comfy ~/ComfyUI
#   ./install.sh --dry-run       # 只打印要做什么
#   ./install.sh --force         # 覆盖已存在的 skill
#
# 只读检查 + 拷贝，不会碰 ~/ComfyUI/user/** 里已有的工作流（同名才覆盖，且会先备份）。

set -euo pipefail

BUNDLE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMFY="${HOME}/ComfyUI"
SKILLS_DIR="${HOME}/.dsh/skills"
PROJ_DIR="${HOME}/comfy-projects"
DRY=0
FORCE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --comfy)   COMFY="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --force)   FORCE=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "未知参数: $1"; exit 2 ;;
  esac
done

c_ok=$'\033[32m'; c_bad=$'\033[31m'; c_warn=$'\033[33m'; c_dim=$'\033[2m'; c_end=$'\033[0m'
step=0
say()  { printf '\n%s[%d] %s%s\n' "$c_dim" "$((++step))" "$1" "$c_end"; }
ok()   { printf '    %s✓%s %s\n' "$c_ok" "$c_end" "$1"; }
bad()  { printf '    %s✗%s %s\n' "$c_bad" "$c_end" "$1"; }
warn() { printf '    %s⚠%s %s\n' "$c_warn" "$c_end" "$1"; }
run()  { if [[ $DRY -eq 1 ]]; then printf '    %s$ %s%s\n' "$c_dim" "$*" "$c_end"; else "$@"; fi; }

echo "================================================================"
echo " HORSEmovie 安装"
echo "================================================================"
echo "  包目录   : $BUNDLE"
echo "  ComfyUI  : $COMFY"
echo "  skills   : $SKILLS_DIR"
[[ $DRY -eq 1 ]] && echo "  模式     : DRY-RUN（不会真的改动）"

# ---------------------------------------------------------------- 前置
say "检查前置"
[[ -d "${HOME}/.dsh" ]] && ok "~/.dsh 存在（DSH 已跑过）" \
  || bad "没有 ~/.dsh —— 先装 DSH 并至少启动一次（见 INSTALL.md 第 0 步）"
[[ -d "$COMFY" ]] && ok "ComfyUI: $COMFY" \
  || bad "没有 $COMFY —— 先装 ComfyUI（见 INSTALL.md 第 0 步），或用 --comfy 指定"
[[ -f "$COMFY/启动ComfyUI.sh" ]] && ok "启动脚本在" \
  || warn "没找到 启动ComfyUI.sh —— run_film.py 的看门狗要靠它拉起服务"

# ---------------------------------------------------------------- skills
say "安装 DSH skills"
mkdir -p "$SKILLS_DIR"
# DSH 只认 kebab-case 的 skill 名（^[a-z0-9]+(?:-[a-z0-9]+)*$），
# 不合法（如 HORSEmovie）会被**静默忽略**（只写一条 warn 日志）。
# 仓库里目录名与 frontmatter 的 name 都已经是 kebab-case，原样落地即可。
for s in horsemovie comfyui; do
  src="$BUNDLE/skills/$s"; dst="$SKILLS_DIR/$s"
  if [[ ! -d "$src" ]]; then bad "包里没有 $s"; continue; fi
  if [[ -e "$dst" && $FORCE -eq 0 ]]; then
    warn "$s 已存在，跳过（要覆盖加 --force）"
    continue
  fi
  [[ -e "$dst" && $FORCE -eq 1 ]] && run rm -rf "$dst"
  run cp -r "$src" "$dst"
  ok "$s → $dst"
done
find "$SKILLS_DIR/horsemovie" "$SKILLS_DIR/comfyui" -name __pycache__ -type d \
     -exec rm -rf {} + 2>/dev/null || true

# ---------------------------------------------------------------- 工作流
say "安装 ComfyUI 工作流"
WF="$COMFY/user/default/workflows"
mkdir -p "$WF"
shopt -s nullglob
for f in "$BUNDLE"/comfyui-workflows/*.json; do
  name="$(basename "$f")"; dst="$WF/$name"
  if [[ -e "$dst" ]]; then
    run cp "$dst" "$dst.bak-$(date +%Y%m%d-%H%M%S)"
    warn "$name 已存在，原文件已备份为 .bak-*"
  fi
  run cp "$f" "$dst"
  ok "$name"
done
shopt -u nullglob

# ---------------------------------------------------------------- 管线
say "安装 quick-txt2img 的 API 图"
mkdir -p "$PROJ_DIR/_pipelines"
for f in "$BUNDLE"/pipelines/*.json; do
  run cp "$f" "$PROJ_DIR/_pipelines/"
  ok "$(basename "$f")"
done

# ---------------------------------------------------------------- 模型
say "模型检查"
MODEL_ROOT="$COMFY/models"
missing=0
check_model() {  # $1=相对路径 $2=说明
  if [[ -f "$MODEL_ROOT/$1" ]]; then
    ok "$(du -h "$MODEL_ROOT/$1" | cut -f1)  $1"
  else
    bad "缺 $1  ($2)"; missing=$((missing+1))
  fi
}
check_model "diffusion_models/Minimax_H3/minimax_h3_ref2va_pruned_int8_convrot.safetensors" "19.5G 视频主模型"
check_model "text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors" "25.3G 文本编码器"
check_model "vae/minimax_h3_video_vae_fp16.safetensors" "4.9G"
check_model "vae/minimax_h3_audio_vae_fp32.safetensors" "0.6G 音频"
check_model "loras/minimax_h3_turbo_v4_step600_ema_pruned_comfyui.safetensors" "0.6G 加速LoRA"
check_model "diffusion_models/qwen-image-2.1-UC-int8_convrot.safetensors" "6.8G 生图"
check_model "text_encoders/qwen3vl_8b_int8_convrot.safetensors" "8.7G"
check_model "vae/qwen_image_2.1_vae_bf16.safetensors" "0.6G"
if [[ $missing -gt 0 ]]; then
  echo
  warn "还缺 $missing 个模型（约 $(echo "scale=0; $missing*8" | bc) GB 量级）—— 详见 models.txt"
fi

# ---------------------------------------------------------------- 自定义节点
say "自定义节点检查"
CN="$COMFY/custom_nodes"
for n in ComfyUI-MiniMax-H3 rgthree-comfy ComfyUI-KJNodes Comfyui-Memory_Cleanup; do
  [[ -d "$CN/$n" ]] && ok "$n" || bad "$n （必需，用 ComfyUI-Manager 装）"
done
for n in ComfyUI-GGUF H3PromptPolish comfyUI-llama-TE TE_MAN; do
  [[ -d "$CN/$n" ]] && ok "$n" || warn "$n （影响功能/速度，建议装）"
done

# ---------------------------------------------------------------- 自检
say "环境自检"
if [[ $DRY -eq 0 ]]; then
  PY="${PY:-}"
  if [[ -z "$PY" ]]; then
    for cand in \
      "${HOME}/.dsh/runtimes/source-launch/primary-runtime/dependencies/python/bin/python3" \
      "$(command -v python3 || true)"; do
      [[ -x "$cand" ]] && { PY="$cand"; break; }
    done
  fi
  if [[ -n "$PY" ]]; then
    "$PY" "$BUNDLE/doctor.py" --comfy "$COMFY" || true
  else
    warn "找不到可用的 python3，跳过自检"
  fi
else
  echo "    \$ python3 doctor.py --comfy $COMFY"
fi

echo
echo "================================================================"
echo " 装完了。接下来："
echo "================================================================"
echo "  1. 补齐上面标 ✗ 的模型和自定义节点"
echo "  2. 起 ComfyUI：  $COMFY/启动ComfyUI.sh"
echo "  3. 标定新机器：  python3 $SKILLS_DIR/horsemovie/scripts/calibrate.py \\"
echo "                     --project <项目> --shot <镜号>"
echo "  4. 拿一镜做身份验证（关键帧 → I2VA → 抽帧对比 refs/character_*.png）"
echo
echo " 详细说明见 INSTALL.md"
