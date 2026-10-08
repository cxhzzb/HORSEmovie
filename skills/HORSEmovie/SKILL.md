---
name: horsemovie
description: Produce a finished AI micro-film end to end on the user's local ComfyUI + MiniMax H3 — turn a script into beats, lock characters with keyframes, generate dialogue-bearing shots with the official H3 prompt format, then verify every shot (identity, no ghosting, speech present, no BGM) and assemble a cut. Also the home of the user's locked production standard and the failure catalogue for this pipeline.
whenToUse: Use whenever the user wants a story turned into video (微电影 / AI 短片 / 短剧 / 剧集 / 一条片子), continues or revises 《归墟·天裂》, asks how to write MiniMax H3 video prompts, asks about 台词/配音/环境音/景别/关键帧/角色一致性 in video, or wants the film production standard applied or amended.
---

# HORSEmovie — AI 微电影生产线

这条 skill 是《归墟·天裂》整个项目沉淀下来的**可复用生产线**。它建立在 `comfyui` skill 之上
（`comfy.py` / `render_shots.py` / `run_film.py` / `qa_frames.py` / `contact_sheet.py` 都在 `~/.dsh/skills/comfyui/`）。

**先加载 `comfyui` skill**，再按本文执行。本 skill 只规定"怎么把片子做出来"，不重复 ComfyUI 的操作细节。

---

## 0. 一条铁律：先有戏，再有画面

这个项目踩过最大的坑不是技术，是**把剧本的脊梁骨丢了**。

第一版 45 镜全部用"纯视觉描述"写提示词——没有台词、没有立场交锋、没有反转铺垫。
提案里明明有主角成长曲线、反派主张、每个角色的关键台词，成片出来却是"45 个各自成立的画面"，
用户的原话是**"剧情乱七八糟"**。

**所以：写任何一条提示词之前，先回答这四个问题。**
答不出来，就不要开始写画面。

1. **这一场谁想要什么？** 谁挡着他？两人是什么关系？
2. **这一场开始和结束时，关系发生了什么移动？**（不是"画面上多了什么"，是"谁占了上风/谁改变了主意"）
3. **台词是什么？** 每一场都要有可说的东西。没有台词的场次必须是刻意设计（纯视觉奇观、悬念、留白），不是偷懒。
4. **这一场埋了什么、回收了什么？** 跨场次的伏笔要有台账。

> 判断标准：把提示词念给别人听，他能不能说出"这一场戏在讲什么"。
> 说不出，就是画面描述，不是戏。

---

## 1. 已锁定的生产标准（用户 2026-10-05 验收通过，不要再改）

用户原话：**"我觉得目前效果可以了，以后可以按照这个标准出视频。"**

| 项目 | 标准值 | 为什么 |
|---|---|---|
| 模式 | **I2VA（关键帧当首帧）** | Ref2VA 全参考直出**会换脸**（同一镜跑三次得到三张不同的脸、共工从 42 岁变年轻） |
| 关键帧 | 编辑分支 + 定妆照 | 编辑分支锁脸可靠；它是唯一能稳定复现同一张脸的环节 |
| 分辨率 | **0.8 档 = 1216×672** ⚠️硬件相关 | 工作流 Note #118 有对照表；**换机器必须重跑 `calibrate.py`**，结果写进那台机器的 `LOCAL.md`（本机实测值见文末附录） |
| 采样步数 | **8 步** | 4 步会发软；8 步是 turbo LoRA 的设计点 |
| 加速 LoRA 强度 | **1.0** | 0.75 会掉细节 |
| 加速度补丁 | **必须有 `MiniMaxH3MemoryEfficientSageAttentionPatch`** | 没有它慢 2.9 倍 |
| 单次时长 | **6–8 秒** ⚠️硬件相关 | H3 官方上限 15 秒；8G 显存只敢用 6–8 秒（12G 可上 8 秒，见附录） |
| 景别变化 | **用硬切（cut），不用推镜** | 推镜会让模型"再画一个人"（重影） |
| 台词期间的机位 | **固定机位（Static Shot）** | 官方指导；叠运镜会出伪影 |
| 人声 | 官方 `<d>[Chinese] …</d>` 写法 | 原生生成，口型同步 |
| 背景音乐 | **`non_diegetic_music: N/A`** | 用户要环境音，不要 BGM |
| 环境音 | 写 `overall_soundscape:` 段 | 材质要具体（"金属玻璃"式的裂缝声） |
| 声音格式 | 32kHz 立体声 | H3 原生规格 |

**标了 ⚠️ 的两项是在 RTX 4070 Laptop 8G 上标定的，换机器必须重测**：

```bash
$PY ~/.dsh/skills/HORSEmovie/scripts/calibrate.py --project <项目> --shot <镜号>
```

它由小到大试一组配置、遇到失败就停，最后给出新机器的推荐档位与全片排期
（顺带会读显卡算力：sm_120+ 的 Blackwell 原生支持 FP4，可以试工作流里已有的 nvfp4 文本编码器）。
详见 `references/pipeline.md` 第 8 节「换机器」。

**其余各项与硬件无关，换多好的卡都不许改**——它们是质量与可靠性的决定因素，不是性能妥协。

**单次生成上限 4–15 秒**（官方硬限制）。`#135.value` / `#292.value` 单位是**秒**，内部换算成帧：**帧数 = 5 + 17k**。

---

## 2. 生产线（四步）

```
剧本 → ① 分场与节拍 → ② 定妆/场景/关键帧（图像） → ③ I2VA 出视频 → ④ 逐镜验收 → 拼片
```

### ① 分场与节拍

- 把剧本拆成**场**，每场 4–15 秒（H3 上限）。超过 15 秒就拆。
- 每场写全：**人物关系 / 来意 / 交锋 / 台词往来 / 转折**。写到提示词里去，模型才知道该演什么。
- 台词分配：一个镜头内只让一个主体承担主要台词；台词量与时长匹配，宁可拆段不要加速。

### ② 图像层（关键帧是最重要的资产）

| 产物 | 命令 | 用途 |
|---|---|---|
| 定妆照 | `render_cast.py` | 角色一致性基准；也是 WAN3 等外部模型的参考图 |
| 四视图 | `render_fourview.py` | 转身参考、设计圣经 |
| 场景空镜 | `render_shots.py --stage scene` | `<Picture 2>`；**画面里不能有人** |
| **关键帧** | `render_shots.py --stage keyframe` | **就是 I2VA 的首帧 = 这一镜的构图** |

**关键帧决定一切。** 首帧的景别就是成片的景别——想要特写就出特写关键帧，
**不要指望在视频阶段"推"出来**（推 = 重影）。

**关键帧锚定谁**：`shots[].kf_chars`（列表取第一个）/ `kf_char` / `char`。
注意 `char` 是 `<Picture 1>` 的持有者，**不一定是画面主体**（013 就因此生成过"长庚身体 + 羲和头发"）。

**多人拼版会退化**：把 2 张以上定妆照并排当输入，编辑分支会把参考板**原样画出来**（灰底并排），
不合成场景。群像镜头目前只锚定主角，靠提示词把其他人带出来。

关键帧出来必须过质检：`qa_frames.py`（检出"编辑分支没重绘整块画布"的坏帧：底板灰残留、纯色块、死白）。

### ③ 视频层

```bash
# 用 load_workspace_dependencies 取 bundled python 的绝对路径；
# 在 DSH 默认安装下它就是 ~/.dsh/runtimes/source-launch/primary-runtime/dependencies/python/bin/python3
PY=<load_workspace_dependencies 返回的 python 路径>
cd ~/.dsh/skills/comfyui

# 单镜
$PY render_shots.py --project <项目> --shots 008 --stage motion --via i2v --megapixels 0.8

# 全片无人值守（自动拉起被 OOM 杀掉的 ComfyUI，幂等可续跑）
$PY run_film.py --project <项目> --stage motion --via i2v --shots all --retries 3
```

`--via i2v` 现在会自动走**加速工作流 + 质量档参数**（见 `references/pipeline.md`）。
提示词取自 `shots[].motion`。

### ④ 验收（每一镜都要过，不许跳）

见 `references/qa.md`。至少四项：**规格 / 身份 / 重影 / 声音**。

---

## 3. 提示词格式：照官方写，别自创

完整规范与模板见 **`references/prompt-format.md`**。三条最容易错的：

1. **重写段落一律英文**，只有 `<d>` 里的台词保留中文。
2. **I2VA 第一行必须是官方固定句**（一字不改），后面空一行再写三段式。
3. 台词只写在 `<d>[Chinese] …</d>` 内；**语气、音色、表演说明写在 `<d>` 外面**。

```
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] ... The camera does not move at all in this shot ...
[Shot 2] At 00:05.000, the shot cuts to ...

overall_soundscape: ...

non_diegetic_music: N/A
```

---

## 4. 失败目录：症状 → 原因 → 处理

完整版见 **`references/pitfalls.md`**。现场先查这张速查表：

| 症状 | 最可能的原因 |
|---|---|
| 换脸 / 变成另一个人 | 走了 **Ref2VA 全参考**。改走 I2VA（关键帧当首帧） |
| 画面里出现两个同一个人 | 提示词里写了**推镜/变焦**。改成固定机位，景别变化用硬切 |
| 画面发软、细节糊 | 工作流是**省时档**（4 步 / LoRA 0.75）。拉到 8 步 / 1.0 |
| 慢到离谱 | 工作流**缺 SageAttention 补丁**；或用了 NVFP4 文本编码器 |
| 画面出现纯灰/纯色方块 | 编辑分支**没重绘整块画布** → `qa_frames.py` 检出，换 seed 重出 |
| 结尾冒出英文字幕卡 | 提示词里出现了 `title` / `credit` / `text` 这类词。**连否定式也别写** |
| 画幅不对 | I2V 工作流默认存的是 `9:16` 甚至是 `1:1`，必须显式覆盖 |
| 没有台词 | 没写 `<d>`；或台词被写进了 soundscape 段（会重复朗读） |
| 莫名有 BGM | 没写 `non_diegetic_music: N/A` |

---

## 5. 排期基准（RTX 4070 Laptop 8G）

| 环节 | 单镜耗时 |
|---|---|
| 定妆照 / 关键帧（1.5MP / 30 步） | ≈1 分钟 |
| 场景空镜（1024×576 / 20 步） | ≈50 秒 |
| **I2V 0.8 档 / 8 步 / 6 秒** | **≈8 分钟** |
| **I2V 0.8 档 / 8 步 / 8 秒** | **≈10 分钟** |

**全片 45 镜 ≈ 7–8 小时**，分夜用 `run_film.py` 挂（它会探活并在 OOM 后 12 秒拉起 ComfyUI）。

---

## 6. 文件地图

```
~/.dsh/skills/HORSEmovie/
├── SKILL.md                      ← 你正在读的
├── references/
│   ├── prompt-format.md          ← H3 官方提示词规范、模板、范例
│   ├── pipeline.md               ← 工作流、节点 id、参数、脚本职责
│   ├── pitfalls.md               ← 失败目录（完整版）
│   └── qa.md                     ← 逐镜验收清单与方法
└── scripts/
    ├── qa_shot.py                ← 单镜验收：规格 + 声音（人声/BGM/环境声）
    └── calibrate.py              ← 换机器后跑一次，标定新的档位与排期
```

**执行层（另一个 skill）**：`~/.dsh/skills/comfyui/` —— `comfy.py` / `render_shots.py` /
`render_cast.py` / `render_fourview.py` / `run_film.py` / `qa_frames.py` / `assemble.py` /
`contact_sheet.py` / `pipelines.json`。

**范例项目**：`~/comfy-projects/tianlie/` —— 《归墟·天裂》，
`HANDOFF.md` 是它的现场记录，`wan3/` 是改投 WAN3 时写的 15 段规划（WAN3 规范与 H3 不同，勿混用）。

---

## 7. 开工前的自检

- [ ] 加载了 `comfyui` skill，`comfy.py status` 看过服务器和队列
- [ ] 同一时刻只有一个 GPU 任务
- [ ] 每一场都能回答第 0 节那四个问题
- [ ] 台词写进了 `<d>`，`non_diegetic_music` 写了 `N/A`
- [ ] 首帧景别 = 想要的景别；景别变化用 cut 不用推
- [ ] 关键帧过了 `qa_frames.py`
- [ ] 出片后过了 `qa_shot.py` 和人工看帧
- [ ] **换了机器/换了模型来源** → 先跑 `doctor.py` + `calibrate.py`，并重做一次身份验证

---

## 附：实机标定与加速审计（RTX 4070 SUPER 12G · Windows · 2026-10-08）

一台真实机器上的完整落地记录。**这些数字属于那台机器**，标准表里的 ⚠️ 两项仍以你自己
`calibrate.py` 的结果为准；方法论与坑是通用的。

### 标定结果（`calibrate.py`，8 步 / LoRA 1.0 / SageAttention 补丁）

| 档位 | 分辨率 | 时长 | 帧 | 单镜耗时（热机） |
|---|---|---|---|---|
| 0.8 | 1216×672 | 8s | 192 | 6.0 分钟 |
| **1.0** | **1376×768** | 8s | 192 | **8.6 分钟** ← 推荐 |

关键帧（Qwen 编辑分支 1.5MP / 30 步）≈ 12.7 分钟。**第一镜要额外付模型加载**
（那台机器冷启动第一镜连加载带出片 ≈ 44 分钟）。

### 效率口径：秒 /（MP·帧）

| 配置 | 秒/(MP·帧) |
|---|---|
| 0.6MP / 8s / 8 步 | 2.19–2.29 |
| 0.8MP / 8s / 8 步 | 2.35 |
| 1.0MP / 8s / 8 步 | 2.68 |

→ 速度几乎只由**分辨率 × 帧数**决定，跟工作流大小（22 节点 API 图 vs 169 节点 UI 图）
无关：同一设置下两者 4.2 vs 4.4 分钟。想快就降像素/帧数。

### 加速手段审计（都实测过）

| 手段 | 结论 |
|---|---|
| `--fast-disk` 启动 ComfyUI | **必需**。默认把 20GB+25GB 权重拷进内存，内存不够时换页，实测卡 2 小时进不到采样；改 mmap 后 33 分钟出片 |
| **`EasyCache`**（ComfyUI 自带 `comfy_extras`） | **可用，1.28×**：`reuse_threshold=0.2` 时 8 步跳 2 步（自报 1.33×）。0.6MP/8s 4.4→3.43 分钟；0.8MP/8s 6.0→4.69 分钟。抽帧复验身份/重影正常 |
| `TE-Speed-MiniMaxH3`（第三方缓存插件） | **不可用**：ComfyUI 0.37 上 `TypeError: FinalLayer.forward() missing 3 required positional arguments: 'sigma', 'sample_sigmas', 'shifts'`。上游 2026-09-06 的"适配新版 H3 接口"版本同样报错，3 种接线都一样 → 插件与版本签名不匹配 |
| `BlockSparseAttention`（H3 专用） | 序列 < `min_tokens`(12288) 时保持 dense → 0.6MP/8s 只有约 2565 token，**用不上**；15 秒长镜 / 1.2MP 以上再考虑，且只有 `sol-attn` 免训练 |
| 4 步 + lightx2v LoRA | ≈2× 但**发软**（标准表已经因为画质放弃了它） |

### Windows / ComfyUI Desktop 四个坑（都会让"装好了"变成"跑不出片"）

1. `--fast-disk`（见上）。写进 Comfy Desktop 的 `installations.json → launchArgs`，并把
   完整命令行写进 `local.json` 的 `server_argv`（`run_film.py` 看门狗按它拉起，否则冷启动又变慢）。
2. **`SaveVideo` 不被当输出节点**：ComfyUI 0.37 的 `validate_prompt` 用 `OUTPUT_NODE is True`
   （身份比较）收集输出，V3 节点 `SaveVideo` 走不到 → 图里只要有别的 output_node，
   提交会"成功"却不出片。`comfy.py` 现在显式传 `partial_execution_targets`，并按 `--project/--shot`
   只点名本次那条分支（点名太宽会把没启用的分支拉进来校验 → 400）。
3. **模型名分隔符**：Linux 写 `Qwen/x`、Windows 清单是 `Qwen\x`，combo 是严格字符串比较，
   报 `Value not in list` 看着像缺模型。`comfy.py` 会拿服务器清单自动对齐，对不上的报出来。
4. **GBK**：控制台打印 `▶▷` 会 `UnicodeEncodeError`；父进程用 `subprocess.run(text=True)`
   捕获子进程输出也按 GBK 解码（`calibrate.py` 因此把成功的渲染判成失败）→ 都要显式
   `encoding="utf-8"`。

### 身份验证（换了机器/模型来源必做）

定妆照 → 关键帧（编辑分支）→ I2VA → 抽帧对比：三帧（1.0s / 2.58s / 4.67s）脸型、发型、
发簪、旗袍一致，无重影，台词有声、环境音在、无 BGM —— 通过。之后才开正式生产。
