# HORSEmovie

一套在**本地 ComfyUI + MiniMax H3** 上生产 AI 微电影的完整流水线 —— 方法论、提示词规范、踩坑目录、验收脚本。

> 从《归墟·天裂》(5 分钟 / 45 镜) 这个项目里沉淀出来的。片子本身不在这里，这里是**怎么把它做出来**。

---

## 它解决什么问题

用视频模型做短片，最难的**从来不是生成一段好看的画面**，而是：

1. **剧情连不起来** —— 45 个各自成立的镜头，拼不成一部电影
2. **角色每个镜头长得都不一样** —— 换脸
3. **画面里有莫名其妙的重影、双头**
4. **不知道模型到底吃哪套提示词格式**，凭感觉写
5. **出了问题不知道是哪一环** —— 是模型、是工作流、还是提示词

这套东西把上面每一条都定位到了根因，并给出了可复现的处理。

---

## 三条最重要的结论

### ① 先有戏，再有画面

写任何提示词之前先回答四个问题：**谁要什么 / 关系怎么移动 / 台词是什么 / 埋了什么回收了什么**。
答不出来就不要开始写画面。

> 判断标准：把提示词念给别人听，他能不能说出"这一场戏在讲什么"。
> 说不出，那就是画面描述，不是戏。

### ② 锁脸只能靠首帧，不能靠"全参考"

| 模式 | 结果 |
|---|---|
| Ref2VA（全参考，喂定妆照） | ✗ **换脸** —— 同一镜跑三次得到三张不同的脸 |
| **I2VA（关键帧当首帧）** | ✓ **锁脸** |

生产流程必须是两段式：**关键帧（编辑分支 + 定妆照）→ I2VA**。

### ③ 景别变化用"硬切"，不能用"推镜"

首帧已经把构图钉死了。让模型把全景"推"成特写，它没有缩放的概念，**会再画一个人出来**（重影/双头）。
想要特写就在关键帧阶段出特写；成片里要换景别就用 cut。

---

## 里面有什么

```
skills/
├── horsemovie/           生产线总纲
│   ├── SKILL.md              已锁定的生产标准 + 开工自检
│   ├── references/
│   │   ├── prompt-format.md  H3 官方提示词规范、模板、禁用词
│   │   ├── pipeline.md       工作流差异、节点 id、参数、换机器
│   │   ├── pitfalls.md       失败目录：症状→原因→处理
│   │   └── qa.md             逐镜五道验收关
│   └── scripts/
│       ├── qa_shot.py        单镜验收：规格 + 声音 + 审阅图
│       └── calibrate.py      换机器后标定档位与排期
└── comfyui/              执行层（驱动 ComfyUI 的桥接）
    ├── comfy.py              UI 工作流 → API → 提交 → 收件
    ├── render_shots.py       场景空镜 / 关键帧 / 视频
    ├── run_film.py           无人值守批量出片（OOM 自动拉起）
    ├── qa_frames.py          关键帧质检
    ├── assemble.py           规格核对 / 审阅表 / 粗剪
    └── contact_sheet.py      拼对比图

comfyui-workflows/        三个必需工作流
pipelines/                quick-txt2img 的 API 图
install.sh                一键安装
doctor.py                 环境自检（只读）
calibrate.py              硬件标定
models.txt                模型清单（约 65 GB）
INSTALL.md                安装说明
```

---

## 几个具体的坑（都是实测出来的）

| 症状 | 根因 |
|---|---|
| 结尾莫名出现英文字幕卡 | 提示词里有 `title card`。**连 `no text` 这种否定式也会触发** |
| 画面出现两个同一个人 | 提示词让镜头推近了 |
| 画面发软、没有质感 | 工作流跑在省时档（4 步 / LoRA 0.75）；质量档是 **8 步 / 1.0** |
| 慢 2.9 倍 | 工作流缺 `MiniMaxH3MemoryEfficientSageAttentionPatch` |
| 输出变成正方形 | 工作流里存的是 `1:1`，必须显式覆盖画幅 |
| 画面出现纯灰方块 | 编辑分支没重绘整块画布，换 seed 重出 |
| 时长换算对不上 | 帧数 = **5 + 17k**（24fps） |

完整版在 [`skills/horsemovie/references/pitfalls.md`](skills/horsemovie/references/pitfalls.md)。

---

## 环境要求

| 项目 | 基准 |
|---|---|
| DSH | skill 是 DSH 的 skill，没有 DSH 无从加载 |
| ComfyUI | **0.37.0**（工作流按此版本存的） |
| 显卡 | RTX 4070 Laptop **8G 显存** / 16G 内存 |
| 模型 | 约 **65 GB** |

**显存更大是好事** —— 但分辨率/时长基准要在新机器上用 `calibrate.py` 重新标定。
`steps=8`、`LoRA 强度 1.0`、I2VA 模式这些与硬件无关，换多好的卡都不改。

---

## 快速开始

```bash
./install.sh --dry-run     # 先看它要做什么
./install.sh               # 装 skill + 工作流
python3 doctor.py          # 自检缺什么
```

Windows（ComfyUI Desktop）用 PowerShell 版本，做的是同一件事：

```powershell
.\install.ps1 -DryRun      # 自动探测 ComfyUI / 共享模型目录 / 启动脚本
.\install.ps1              # 装 skills + 工作流 + API 图，写 --fast-disk，跑 doctor.py
& <bundled python> doctor.py
```

详见 [`INSTALL.md`](INSTALL.md)。

---

## 实机落地记录（RTX 4070 SUPER 12G / Windows）

一台真实机器上的完整标定与加速审计，结论都带实测数字：

- **标定**：0.8 档 / 8 秒 = 6.0 分钟；1.0 档 / 8 秒 = 8.6 分钟 → 推荐 1.0 档，45 镜 ≈ 6.4 小时
- **`--fast-disk` 是必需项**：不带的话 20GB+25GB 权重会往内存里拷，实测卡 2 小时进不到采样
- **`EasyCache`（ComfyUI 自带）实测 1.28×**：0.8MP/8s 从 6.0 → 4.69 分钟，抽帧复验无问题
- **第三方 `TE-Speed-MiniMaxH3` 在 ComfyUI 0.37 上跑不起来**（`FinalLayer` 签名不匹配，上新版也报错）
- **`BlockSparseAttention` 对短视频无效**：序列 < `min_tokens` 12288 时保持 dense
- **Windows / Desktop 四个坑**：`--fast-disk`、`SaveVideo` 不算输出节点、模型名分隔符、控制台 GBK

细节见 [`skills/horsemovie/SKILL.md`](skills/horsemovie/SKILL.md) 文末附录、[`INSTALL.md`](INSTALL.md)
的「Windows 实战补充」与「加速」两节。

---

## 已验证的能力

| 项目 | 状态 |
|---|---|
| 中文台词 | ✓ 原生生成，口型同步 |
| 环境音效 | ✓ `overall_soundscape` 写材质 |
| 无背景音乐 | ✓ `non_diegetic_music: N/A` |
| 角色跨镜一致 | ✓ 关键帧 → I2VA，抽帧验证过 9 个镜头 |
| 多人同框 | ⚠️ 只稳到"锚定主角 + 文字带出其他人" |
| 音色克隆 | ✓ 节点支持 `ref_audios`（尚未使用） |

---

## 适用与不适用

**适用**：有剧本、要出成片的短片/剧集项目；需要角色跨镜一致的叙事视频。

**不适用**：单张图生视频的零散需求；不需要连续性的素材生成。

---

## 许可

[MIT](LICENSE) © 2026 cxhzzb —— 随意使用、修改、商用，保留版权声明即可。

仓库里的 **skill、脚本、文档、工作流** 都可以直接拿走用。
注意：**模型权重不在仓库里**（65 GB，各自按 `models.txt` 从官方渠道获取），
它们的许可归各自发布方；本仓库只提供使用它们的流程与工作流。

> 里面的示例项目《归墟·天裂》的**成片与素材不在本仓库**，仓库只沉淀"怎么把它做出来"的方法。
