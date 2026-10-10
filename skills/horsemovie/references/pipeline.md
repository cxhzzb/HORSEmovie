# 管线、节点与参数（本机实测）

> 节点 id 会随用户改工作流而变。**动手前永远先跑 `comfy.py plan <工作流>`**，
> 它的输出优先于本文。本文记录的是 2026-10-05 验证过的接线与参数。

```bash
# 用 load_workspace_dependencies 取 bundled python 的绝对路径；
# 在 DSH 默认安装下它就是 ~/.dsh/runtimes/source-launch/primary-runtime/dependencies/python/bin/python3
PY=<load_workspace_dependencies 返回的 python 路径>
C=~/.dsh/skills/comfyui/comfy.py
```

---

## 1. 三条视频工作流 —— 差异是关键

用户机上有三条 MiniMax H3 视频工作流，名字很像，**用途和性能完全不同**：

| 工作流 | 视频节点 | Sage 补丁 | 默认步数 / LoRA 强度 | 用途 |
|---|---|---|---|---|
| **`▶▷MiniMaxH3-加速视频流整合`** | `ImageToVideo` #133 | ✅ #138 | 4 / 0.75（省时档） | **本项目主力**：首帧锁脸 |
| `▶▷MiniMaxH3-加速视频流整合 有4个提示词` | `ReferenceToVideo` #316 | ✅ | 8 / 1.0 | 用户推荐的质量档，但**全参考会换脸** |
| `MiniMax_H3_I2V_Turbo8步` | `ImageToVideo` | ❌ **无** | 8 / 1.0 | **别用**，慢 2.9 倍 |

**实测（030 镜，0.8MP / 8 秒）：**

| 配置 | 耗时 | 结果 |
|---|---|---|
| `MiniMax_H3_I2V_Turbo8步` | 1009 s | 慢；无 Sage 补丁 |
| 同上但换 int8 文本编码器 | 1009 s | **换编码器没用**，瓶颈不是它 |
| `加速视频流整合 有4个提示词`（Ref2VA） | 617 s | **换脸** |
| **`加速视频流整合`（I2V）** | **345 s** | 快，但默认 4 步 / 0.75 → **画面发软** |
| **`加速视频流整合` + 8 步 / 1.0** | **595 s** | ✅ **本项目标准** |

**结论**：走 `ImageToVideo` 那个文件（锁脸）+ 把步数和 LoRA 强度拉回质量档。

### 1.1 主力工作流的节点图（`▶▷MiniMaxH3-加速视频流整合`）

| 语义 | 选择器 | 说明 |
|---|---|---|
| 首帧 | `#114.image` | LoadImage，填上传后的文件名 |
| 提示词 | `#369.text` | H3PromptEdit |
| 画幅 | `#115.aspect_ratio` | **默认存的是 `9:16 (Portrait Widescreen)`，跑 16:9 必须显式覆盖** |
| 分辨率 | `#115.megapixels` | 0.8 = 1216×672 |
| 时长 | `#135.value` | **单位秒**，内部换算成 `5+17k` 帧 |
| 种子 | `#186.seed` | |
| 输出 | `#189.filename_prefix` | 必须覆盖成 `dsh/<project>/<shot>`，否则收不进项目目录 |
| **步数** | **`#126.steps`** | 默认 **4** → 质量档设 **8** |
| **LoRA 强度** | **`#140.strength_model`** | 默认 **0.75** → 质量档设 **1.0** |

模型：unet `Minimax_H3/minimax_h3_ref2va_pruned_int8_convrot.safetensors`（#129）、
LoRA `minimax_h3_turbo_v4_step600_ema_pruned_comfyui.safetensors`（#140）、
Sage 补丁 `MiniMaxH3MemoryEfficientSageAttentionPatch`（#138）、
采样 `res_multistep` + `simple`（#125 / #126）。

**这四个参数已固化在 `pipelines.json` 的 `i2v-minimax.fixed` 里，`render_shots.py --via i2v` 会自动带上。**

### 1.2 用户推荐的 Ref2VA 工作流节点图（备查）

| 语义 | 选择器 |
|---|---|
| 提示词 | `#365.text` |
| 种子 | `#303.seed` |
| 画幅 / 分辨率 | `#290.aspect_ratio` / `#290.megapixels` |
| 时长 | `#292.value`（秒） |
| 参考图1 / 参考图2 | `#308.image` / `#295.image` |
| 输出 | `#285.filename_prefix` |
| 步数 / LoRA | `#288.steps`（8）/ `#300.strength_model`（1.0） |

`H3PromptPolish`（#364/#366/#368/#370）是 `output_node=true`，**一定会执行**，靠 `auto_drop` 丢掉。

### 1.3 分辨率对照表（工作流 Note #118）

| megapixels | 16:9 输出 |
|---|---|
| 0.2 | 608×352 |
| 0.4 | 864×480 |
| 0.6 | 1056×608 |
| **0.8** | **1216×672** |
| 1.0 | 1376×768 |
| 1.5 | 1664×928 |
| 2.0 | 1920×1088 |

---

## 2. 图像层工作流（`▶▷Qwen-image21-图像编辑+生图流（整合）`）

两条并行分支，**默认都跑 → 时间翻倍**，按用途砍掉一条。

| 语义 | 选择器 |
|---|---|
| 生图提示词 / 负面 | `#517.value` / `#522.negative_prompt` |
| 生图画幅 / 分辨率 / 步数 / 种子 / 输出 | `#512.aspect_ratio` / `#512.megapixels` / `#515.steps` / `#524.seed` / `#508.filename_prefix` |
| 编辑提示词 / 负面 | `#571.value` / `#497.negative_prompt` |
| 编辑输入图 | `#475.image` |
| 编辑步数 / 种子 / 输出 | `#481.steps` / `#502.seed` / `#487.filename_prefix` |
| 编辑输入规格化 / resolution | `#532.megapixels` / `#497.resolution` |

| 用途 | 丢弃 |
|---|---|
| 只出图 | `--drop '#487' --drop 'Image Comparer (rgthree)'` |
| 只做编辑（关键帧、四视图） | `--drop '#508' --drop 'Image Comparer (rgthree)'` |

**已验证参数**：编辑分支 `denoise` 必须 = 1（降到 0.62 会与 TE-Speed 缓存节点冲突，画面崩成重复纹理）；
steps 30 / megapixels 1.5 / `#497.resolution` 1024；
**编辑分支输出尺寸跟随输入图宽高比** → 要横版就先拼横版底图。

---

## 3. 脚本职责

全部在 `~/.dsh/skills/comfyui/`：

| 脚本 | 职责 |
|---|---|
| `comfy.py` | 核心桥接：`status` / `plan` / `describe` / `convert` / `run` / `watch` / `upload` / `history` / `cancel` |
| `render_cast.py` | 出角色定妆照（生图分支） |
| `render_fourview.py` | 出四视图（编辑分支 + 横版底图） |
| `render_shots.py` | `--stage scene` 场景空镜 / `--stage keyframe` 关键帧 / `--stage still` 静帧 / `--stage motion --via i2v\|main` 视频 |
| `run_film.py` | **无人值守批量出片**：逐镜探活，ComfyUI 掉线自动无头拉起，失败重试，幂等续跑 |
| `qa_frames.py` | 关键帧质检：检出"编辑分支没重绘整块画布"（底板灰残留 / 纯色块 / 死白） |
| `assemble.py` | 规格核对 + 逐镜审阅表 + 粗剪拼接 |
| `contact_sheet.py` | 拼对比图（取最新一张要**按 mtime**，不能按字典序） |
| `pipelines.json` | 管线注册表：语义旋钮 → 真实节点 id |

**HORSEmovie 自带**：`scripts/qa_shot.py`（单镜验收：规格 + 声音）。

---

## 4. 无人值守与容错

8G 显存 / 16G 内存，长批次会被 OOM 杀掉。`run_film.py` 的处理方式：

1. 每镜开跑前探活 `http://127.0.0.1:8188/system_stats`
2. 掉线则按 `~/ComfyUI/启动ComfyUI.sh` 的逻辑**无头拉起**（venv python + 把 `LD_LIBRARY_PATH`
   指向 `.venv/lib/python3.12/site-packages/llama_cpp/lib`），**不弹 Konsole、不开浏览器**
3. 实测恢复时间 **12 秒**
4. 每镜失败重试 3 次
5. **幂等**：成片比首帧新的镜头自动跳过，断电后直接重跑即续

日志：`~/comfy-projects/_run_film.log`、`~/comfy-projects/_comfyui_autostart.log`。

```bash
# 全片无人值守
$PY run_film.py --project <项目> --stage motion --via i2v --shots all --retries 3
# 只看打算跑什么
$PY run_film.py --project <项目> --stage motion --via i2v --shots all --dry-run
```

---

## 5. 项目数据结构

```
~/comfy-projects/<项目>/
├── brief.md          设定与生成约定
├── cast.json         角色定妆提示词（含 hard_requirements / style / negative）
├── shots.json        镜头清单
├── refs/             character_<id>.png（角色参考）、<镜号>.png（场景参考）、character.png（默认主角）
├── out/              产物 + 审阅图表
├── HANDOFF.md        现场记录（新会话从这里读）
└── wan3/             若改投 WAN3 的规划（格式不同，勿混用）
```

`shots.json` 每镜的字段：

| 字段 | 含义 |
|---|---|
| `id` / `t` / `dur` / `scene` / `cn` | 镜号 / 时间码 / 时长(秒) / 场次 / 中文画面 |
| `still` / `scene_prompt` | 静帧提示词 / 场景空镜提示词 |
| **`motion`** | **I2VA 提示词（视频阶段实际使用的字段）** |
| `final` | H3 Ref2VA 格式提示词（历史字段，本地出片不用） |
| `char` | `<Picture 1>` 归属的角色 id |
| `kf_char` / `kf_chars` | 关键帧要锁定的角色（**与 `char` 不一定相同**） |

---

## 6. 显存与排期纪律

- **同一时刻只跑一个 GPU 任务。** 视频任务会打满 7.5G/7.7G、内存 13G/15G。
- 定妆照 / 关键帧 ≈1 分钟（1.5MP / 30 步）
- 场景空镜 ≈50 秒（1024×576 / 20 步）
- **I2V 0.8 档 / 8 步**：6 秒镜头 ≈7.9 分钟；8 秒镜头 ≈9.9 分钟
- 全片 45 镜 ≈ **7–8 小时** → 分夜挂 `run_film.py`

**不要动这些节点**（是真实控制/性能节点，不是猜提示词）：
`MiniMaxH3MemoryEfficientSageAttentionPatch`、`TESpeedQwenImage21`、`TESpeedVOSR2*`、
`ImageScaleToTotalPixels`、`VRAMCleanup` / `RAMCleanup`、`VNCCS_PoseStudio`。

---

## 8. 换机器：什么自动变好、什么必须重测

现有数字（0.8 档 / 8 秒 / 单镜 8 分钟）标定于 **RTX 4070 Laptop 8G / 16G 内存**。
换更强的机器时，**不要直接套用**。

### 8.1 硬件相关 —— 必须重测

| 项目 | 为什么 | 怎么测 |
|---|---|---|
| **可用 megapixels 上限** | 显存够就能上 1.0 / 1.5 / 2.0 档 | `calibrate.py` 自动扫 |
| **单次可用时长** | 帧数 = 5+17k，越长 latent 越大；节点允许到 3600 帧（150s），但显存先撑不住 | 同上 |
| **单镜耗时** | 决定排期 | 同上 |
| **fp16 / nvfp4 模型是否放得下** | 官方 fp16 的 32B 文本编码器在 8G 上根本进不去 | 见 8.2 |

```bash
PY=<load_workspace_dependencies 返回的 python 路径>
$PY ~/.dsh/skills/horsemovie/scripts/calibrate.py --project <项目> --shot <镜号>
```

它由小到大试一组配置，**遇到失败就停**（说明顶到上限），最后给出推荐档位与全片排期。
用 `--budget 分钟` 控制花费。

### 8.2 显卡架构带来的额外选择

| 算力 | 说明 | 该用什么量化 |
|---|---|---|
| **sm_120+（Blackwell / RTX 50 系）** | **原生支持 FP4** | 可以试 `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` —— 工作流里本来就有这个 loader，在 40 系上它慢（无 FP4 硬件），在 50 系上可能**又快又准** |
| sm_8.9（Ada / RTX 40 系） | 无原生 FP4 | 继续用 `_int8_convrot` |

`calibrate.py` 会自动读算力并给出提示。

**更大的显存还解锁三件事**：
1. 更高的 megapixels（1.5–2.0 档，接近 1080p）
2. 更长的单次时长（H3 官方上限 15 秒，之前为了显存只敢用 6–8 秒）
3. 参考音频（`ref_audios`，节点本来就支持）——**可以做音色克隆**，让同一角色的台词跨镜音色一致

### 8.3 硬件无关 —— 换机器也不许改

这些是**质量与可靠性**的决定因素，不是性能妥协，换多好的卡都保持：

- **模式必须是 I2VA**（关键帧当首帧）——Ref2VA 会换脸
- **`#126.steps = 8`**
- **`#140.strength_model = 1.0`**
- **必须有 `MiniMaxH3MemoryEfficientSageAttentionPatch`**（工作流选型）
- **景别变化用硬切，不用推镜**
- **台词期间固定机位**
- **官方 I2VA 三段式 + 固定首行 + `<d>` 台词**
- **`non_diegetic_music: N/A`**（要环境音不要 BGM）
- **帧数 = 5 + 17k**（这是节点约束，不是硬件约束）
- **音频 32 kHz 立体声**

### 8.4 换机器后的重测顺序

1. `doctor.py` —— 环境齐不齐
2. `calibrate.py` —— 新机器的档位与排期
3. **拿一镜做身份验证** —— 关键帧 → I2V，抽帧对比 `refs/character_*.png`。
   换了量化版本或模型来源时，这一步**必须重做**，锁脸能力可能变。
4. 把新数字填回 `SKILL.md` 第 1 节的「已锁定的生产标准」表

---

## 9. 文件与凭据

- **绝不修改或删除 `~/ComfyUI/user/**`**（用户的工作流，只读）。
- 需要临时改参数时用 `comfy.py run ... --set`，**不要改文件**。
- 只往 `~/comfy-projects/**` 写。
- 不要安装自定义节点、不要改 ComfyUI 的 venv、不要重启服务器（除非用户要求，或用 `run_film.py` 的看门狗）。
