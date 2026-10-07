---
name: comfyui
description: Drive the user's local ComfyUI server to turn written settings into images and video — convert their saved UI workflows into the ComfyUI API format, patch prompt/seed/size/model/duration, queue the job, wait for it, and pull the results into a project folder for review. Also lists their models, workflows, and queue, and validates a graph before a long run.
whenToUse: Use whenever the user wants to generate, re-generate, vary, upscale, animate, or edit images/video with ComfyUI (生图/出图/生视频/放大/重绘/四视图/擦除修改), or when a script, storyboard, or shot plan needs to become renders, or when they ask about their ComfyUI models, workflows, queue, or VRAM.
---

# ComfyUI bridge

The user runs ComfyUI locally. This skill turns "written setting -> rendered file" into a repeatable loop.

- Server: `http://127.0.0.1:8188` (override with `COMFY_HOST`), ComfyUI 0.37, RTX 4070 Laptop, **8 GB VRAM**, 16 GB RAM.
- Bridge: `comfy.py` in this skill's directory. Run it with the bundled Python:
  `PY=$(load_workspace_dependencies -> python)` then `"$PY" <skill-dir>/comfy.py <command>`.
  It is stdlib-only, so any Python 3 works, but prefer the bundled interpreter.
- Projects: `~/comfy-projects/<project>/` — `brief.md`, `shots.json`, `refs/`, `out/`, `manifest.json`.

## Before anything

1. Check the server: `comfy.py status`. If it is down, tell the user to run `~/ComfyUI/启动ComfyUI.sh` (do not start it yourself unless they ask).
2. **One GPU job at a time.** 8 GB VRAM. If `status` shows a running job, do not queue another; wait or ask.
3. Their working files are precious: never edit or delete anything under `~/ComfyUI/user/**`. Read only. Write only into `~/comfy-projects/**`.

## 两条主力管线（用户 2026-10-05 明确指定，优先用它们）

| | 生图 | 生视频 |
|---|---|---|
| 工作流 | `▶▷Qwen-image21-图像编辑+生图流（整合）` | `▶▷MiniMaxH3-加速视频流整合`（三个变体接线不同，先跑 `plan`）|
| 模型 | Qwen-Image 2.1 UC int8 + qwen3vl_8b + qwen_image_2.1 vae | `Minimax_H3/minimax_h3_ref2va_pruned_int8_convrot`（全参考）/ `minimax_h3_fl2va`（首尾帧）+ turbo LoRA |
| 默认参数 | 30 steps, cfg 1, euler/simple | 8 steps, res_multistep |
| 结构 | 两条并行分支：编辑（`#482.switch=false`）与文生图（`switch=true`） | Reference-to-Video，最多 4 张参考图 |
| 提示词写哪 | 生图 `#517.value` / 编辑 `#571.value`（**本来就没有 LLM 节点，手写**） | `#365.text`（H3 格式），LLM 润色节点已 auto_drop |

**生图侧的额外纪律（2026-10-05 查明）**：`#487`(编辑 SaveImage)、`#508`(生图 SaveImage)、`#530`(Image Comparer) 都是 `output_node=true`，所以**两条分支默认都会执行，每次出图跑两遍采样**。按用途砍掉不要的那条：

```
只出图      --drop '#487' --drop 'Image Comparer (rgthree)'   # 实测通过
只做编辑    --drop '#508'                                     # 实测通过
```

**不要动的节点**（不是猜提示词，是真实控制/性能）：`VNCCS_PoseStudio`（姿态控制）、`TESpeedVOSR2*`（放大，草稿期可临时 drop）、`TESpeedQwenImage21`（加速）、`ImageScaleToTotalPixels`（输入图规格化）、`VRAMCleanup/RAMCleanup`（8G 显存下有用）。

`大圣Ai之Qwen+image+2.1-12大应用全能生图` 的 `TE_Qwen_Image_2_1_Prompt_Enhancer` 是**死代码**（输出只接到一个 VRAMCleanup），已在 `pipelines.json` 的 `image-alt` 里 auto_drop；那个文件另有 4 个 Cleanup + 3 个预览类 output 节点各自保活一条上游链，比"整合"那条慢得多。

**开工前永远先做这一步：`comfy.py plan <工作流>`。** 它打印当前实际接线（哪个视频节点、提示词落到哪个节点、尺寸/时长/种子/输出/参考图各是哪一号）。**用户会改工作流，节点 id 会变；`plan` 的输出优先于 `pipelines.json`。**

已知要点：
- **提示词由 DSH 写，润色节点一律关掉。** `H3PromptPolish` 在 `object_info` 里是 `output_node=true` —— 它**一定会执行**，每个都要加载本地 Qwen3.5-4B GGUF。`pipelines.json` 的 `video-main` 已声明 `auto_drop: ["H3PromptPolish"]`，`run` 会自动跳过全部 4 个（要恢复内置润色加 `--no-auto-drop`）。**不要**因为"提示词质量"再把它们打开；直接写最终提示词。
- 最终提示词写 `#365.text`，格式：`subject_definitions:`（可复用的角色/场景块，用 `<Picture 1>`/`<Picture 2>` 指代参考图）+ `Shot:` 动作、运镜、光线、质感。
- 参考图约定：`<Picture 1>` = 角色定妆，`<Picture 2>` = 该镜场景/构图。项目里放 `refs/character.png` 与 `refs/<镜号>.png`，`render_shots.py --stage motion --via main` 会自动 upload 并接到对应节点。
- 生图管线的两个 SaveImage 前缀默认是 `QW21-%date:...%` —— 必须覆盖成 `dsh/<project>/<shot>`，否则产物收不进项目目录。
- 时长/尺寸/种子/输出这些旋钮的当前 id 见 `pipelines.json`。

## 这部片子的选角硬要求（用户明确过，别跑偏）

- **全部角色 = 中国面孔（东亚人种）**，不要西方人/白人/混血感。`casts` 的 negative 里已经放了"西方人，白人，欧美面孔，高加索人种，深眼窝，高鼻梁，混血感"。
- **主要角色 20–29 岁，年轻**；反派 42 岁但紧实不老态。
- **去 AI 感靠肤质**（毛孔、油光、肤色不均、小痣、左右不对称、碎发、服装磨损），**不靠皱纹**。皱纹/眼袋/法令纹/老年斑/松弛/中年/老年都在 negative 里。
- 定妆图与四视图的提示词模板见 `~/comfy-projects/tianlie/cast.json` 与 `render_fourview.py` 的 LAYOUT_PROMPT。

## Commands

| Command | What it does |
|---|---|
| `status` | server, device, VRAM, queue |
| `models [kind]` | available checkpoint / unet / clip / vae / lora / controlnet / upscale |
| `workflows` | every saved workflow with node count and format (UI/API, subgraphs) |
| `describe <wf> [--filter text]` | the **converted API graph**: node ids, class types, current inputs — use this to find what to patch |
| `plan <wf>` | **run this first**: the live wiring as ready-to-use `--set` selectors (video node + mode, prompt/size/duration/seed/output/reference targets, edit-vs-txt2img switch) |
| `convert <wf> [--out f.json]` | write the API-format graph to a file |
| `run <wf> [--set NODE.FIELD=V]... [--drop SELECTOR]... [--wait] [--check] [--project P] [--shot S] [--timeout T]` | patch, submit, optionally wait and download |
| `watch <prompt_id> --project P` | attach to an already-queued job and download when it finishes |
| `history [id]` / `cancel [id]` | past runs, outputs; interrupt the running job |
| `upload <file> [--subfolder s]` | put a reference image into ComfyUI's input dir (needed before LoadImage) |

`--set` selector forms: `#<node id>` (`#105/104` for a node inside subgraph instance 105), a unique `ClassType` (`KSampler`), or a node title. Value is JSON if it parses (`0.4`, `1344`, `true`), otherwise a string; `@path` reads a file. Prompts with quotes/newlines: pass a JSON string or `@file`.

## The loop to use

1. **Read the intent**, then look up the pipeline in `pipelines.json` (next to this file). It maps semantic knobs to real node ids for every verified pipeline.
2. **Validate cheaply before burning GPU time**: `run <wf> --set ... --check`. It submits and immediately cancels — ComfyUI's own validation reports missing inputs, bad combo values, wrong types. Fix and repeat. Only then do the real run.
3. **Run**: `run <wf> --set ... --wait --project <p> --shot <s> --timeout 2400`. For long video jobs use a generous timeout or run without `--wait` and collect with `watch`.
4. **Look at the result** with `read_image` on the downloaded PNG (or a rendered frame for video, via the LibreOffice/ffmpeg route). Judge composition, character consistency, text errors, anatomy. Never claim a render looks right without looking.
5. **Iterate in natural language**: change the prompt text, bump `steps`, move the `seed`, resize, or switch T2V -> I2V with a reference frame. Keep the previous output and say which knob changed.

## Pipelines (see `pipelines.json` for the full patch map)

| Name | Use | Notes |
|---|---|---|
| `txt2img-qwen` | single image, fastest | verified: 768x432, 8 steps, ~20 s. Bump steps to 20-30 for a real frame, 1024-ish for quality |
| `t2v-minimax` | text -> video | MiniMax H3 turbo, 8 steps; duration via `#105/111.value` seconds |
| `i2v-minimax` | first frame -> video | upload the frame, then `--set '#114.image=<name>'` |
| `video-accelerated` | the user's main video pipeline (127 nodes) | prompt nodes `#365`-`#371` (T2V/I2V/FL2V variants), duration `#292.value` |
| `four-view` | character four-view sheet | needs a reference image uploaded first |
| `qwen-edit` | image edit / pose transfer | needs input images |
| `upscale-vosr2`, `upscale-4x` | upscale a still | needs an input image |

Models on this machine: image = `qwen-image-2.1-UC-int8_convrot` + `qwen3vl_8b_int8_convrot` clip + `qwen_image_2.1_vae_bf16`; video = `minimax_h3_fl2va_pruned_int8_convrot` + `qwen3vl_32b_minimax_h3_nvfp4_awq` clip + `minimax_h3_video_vae_fp16` + audio VAE + `minimax_h3_turbo_v4_step600` LoRA; upscalers = VOSR2, 4x-UltraSharp.

## How conversion works (so failures are diagnosable)

ComfyUI's `/prompt` wants the **API format**; the user's files are **UI format**. `comfy.py` converts: flattens subgraph instances (`#105/104`), follows bypassed nodes (mode 4) through the matching input, drops muted nodes (mode 2), skips frontend-only nodes (`MarkdownNote`, `Note`, `Label`) and prunes to the ancestor closure of `output_node` nodes, then maps `widgets_values` positionally onto the node's widgets (using the frontend's widget names where present, else the server's `object_info` order, skipping the `control_after_generate` companion value). Windows backslash model paths are normalized to `/`.

Symptoms -> cause:
- `Value not in list: unet_name 'a\\b.safetensors'` -> path normalization missed it; report, do not silently rename models.
- `Required input is missing: model` -> a bypassed/muted node upstream; check `mode` on that source node.
- `Invalid image file: x.png` -> the workflow needs an input image; `upload` one and set the `LoadImage.image` widget.
- `consumed n/m widgets` warnings from `describe`/`run` -> alignment may be off for that node class; verify with `--check` before trusting the run.

## Limits

- Do not install custom nodes or Python packages, change the ComfyUI venv, or restart their server.
- Do not run two generations at once; 8 GB VRAM will thrash or OOM, and 16 GB RAM is already tight with these int8 models.
- Preview renders are drafts: a lucky seed is not a locked character. For consistency, prefer I2V from a fixed reference frame and reuse the same LoRA/settings across shots.
- The bridge reports what ComfyUI reports. If validation passes but the render is wrong, the fix is in the workflow or the prompt, not in the bridge.
