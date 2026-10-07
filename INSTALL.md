# HORSEmovie 安装说明

把《归墟·天裂》这套 AI 微电影生产线搬到另一台电脑。

**一句话**：skill 只有 128 KB，五分钟装完；花时间的是 **ComfyUI + 65 GB 模型 + 一堆自定义节点**。

---

## 总览：三条命令

```bash
# ① 装前置（DSH 和 ComfyUI 本身，见第 0 步）
# ② 把迁移包拷到新机器，然后：
cd HORSEmovie-bundle && ./install.sh
# ③ 补模型、补自定义节点，最后：
python3 doctor.py && python3 skills/HORSEmovie/scripts/calibrate.py --project <项目> --shot <镜号>
```

`install.sh` 会把 skill、工作流、管线图都装好，并检查模型和节点缺什么。
**它不会动你已有的工作流**（同名的先备份成 `.bak-时间戳`）。

---

## 第 0 步：装前置（新机器上还什么都没有）

### 0.1 装 DSH

**skill 是 DSH 的 skill，没有 DSH 就无从加载。** 两种装法：

**方式 A：npm（最省事，需要 Node.js）**
```bash
npx @deepseek-ai/dsh web
```

**方式 B：源码（本机就是这种）**
```bash
git clone https://github.com/deepseek-ai/deepseek-harness.git
cd deepseek-harness
pnpm install
pnpm run build
pnpm dsh web
```

跑起来后 Web UI 在 `http://127.0.0.1:3080`。

> ⚠️ **必须至少启动一次 DSH**，它才会创建 `~/.dsh/` 目录（skill 要放进 `~/.dsh/skills/`）。
> 本机 DSH 是 git 仓库：`https://github.com/deepseek-ai/deepseek-harness.git`

### 0.2 装 ComfyUI

```bash
git clone https://github.com/comfyanonymous/ComfyUI.git ~/ComfyUI
cd ~/ComfyUI
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

本机版本：**ComfyUI 0.37.0，venv Python 3.12.13**。

> 建议照抄本机的版本号。工作流文件是 0.37 存的，**不同版本的 core 节点名可能不一样**，
> 加载时会对不上。

### 0.3 硬件门槛

| 项目 | 本机基准 | 说明 |
|---|---|---|
| 显卡 | RTX 4070 Laptop **8G 显存** | 低于 8G 要重新标定，可能跑不动 |
| 内存 | **16G** | 视频任务吃到 13G |

**新机器更好的话是好事** —— 但包里的分辨率/时长数字是 8G 上量的，
装完要跑 `calibrate.py` 重新标定（见第 5 步）。

---

## 第 1 步：把迁移包拷过去

`HORSEmovie-bundle.tar.gz`（128 KB）—— 邮件、U 盘、`scp` 都行。

```bash
scp HORSEmovie-bundle.tar.gz 新机器:~/
ssh 新机器
tar xzf HORSEmovie-bundle.tar.gz && cd HORSEmovie-bundle
```

**模型另外搬**（65 GB，见第 3 步），最快的办法是把本机的 `~/ComfyUI/models/` 整个拷过去：

```bash
rsync -avh --progress ~/ComfyUI/models/ 新机器:~/ComfyUI/models/
```

---

## 第 2 步：跑安装脚本

```bash
cd HORSEmovie-bundle
./install.sh              # 默认装到 ~/ComfyUI 和 ~/.dsh/skills
./install.sh --dry-run    # 先看它要做什么，不改任何东西
./install.sh --comfy ~/别的路径/ComfyUI
./install.sh --force      # 覆盖已存在的 skill
```

它做的事：

| 步骤 | 内容 |
|---|---|
| 1 | 检查前置（`~/.dsh`、ComfyUI、启动脚本） |
| 2 | 把 `HORSEmovie` **和** `comfyui` 两个 skill 装到 `~/.dsh/skills/` |
| 3 | 三个必需工作流装到 `~/ComfyUI/user/default/workflows/`（同名先备份） |
| 4 | `txt2img-qwen.api.json` 装到 `~/comfy-projects/_pipelines/` |
| 5 | 逐个核对 8 个必需模型 |
| 6 | 逐个核对自定义节点包 |
| 7 | 调用 `doctor.py` 出完整报告 |

> **两个 skill 都要装。** `HORSEmovie` 是"怎么拍"，`comfyui` 是"怎么驱动 ComfyUI"——
> 前者调用后者的 `comfy.py` / `render_shots.py` / `run_film.py`。只装一个跑不起来。

---

## 第 3 步：补模型（65 GB）

见 **`models.txt`**。脚本第 5 步会告诉你缺哪几个。

**推荐直接整目录 rsync**（比逐个下载快得多）。如果拿不到同名文件，`models.txt` 里
标了 ⚠️ 的那 4 个是量化版，官方仓库给的是 fp16/nvfp4，**文件名对不上**，要改工作流里的模型名。

---

## 第 4 步：补自定义节点

用 ComfyUI-Manager 装，或逐个 `git clone` 到 `~/ComfyUI/custom_nodes/`。

**必需**（缺了跑不起来）：

| 节点包 | 提供什么 |
|---|---|
| `ComfyUI-MiniMax-H3` | H3 视频节点 + **SageAttention 加速补丁** |
| `rgthree-comfy` | Seed / Fast Groups Bypasser / Image Comparer |
| `ComfyUI-KJNodes` | `ImageResizeKJv2`（首帧规格化） |
| `Comfyui-Memory_Cleanup` | VRAMCleanup / RAMCleanup（8G 显存必需） |

**建议**：`ComfyUI-GGUF`、`H3PromptPolish`、`comfyUI-llama-TE`、`TE_MAN`、`ComfyUI-VOSR2`、`ComfyUI_VNCCS_Utils`

**装完重启 ComfyUI。**

---

## 第 5 步：自检 → 标定 → 冒烟测试

```bash
# ① 环境自检（只读）
python3 doctor.py

# ② 起服务器
~/ComfyUI/启动ComfyUI.sh

# ③ 拿 python 路径
#    在 DSH 里调 load_workspace_dependencies；或直接用本机那个：
PY=~/.dsh/runtimes/source-launch/primary-runtime/dependencies/python/bin/python3

# ④ 标定新机器（新机器配置更好的话，这一步很重要）
$PY skills/HORSEmovie/scripts/calibrate.py --project <项目> --shot <镜号>

# ⑤ 确认接线
$PY ~/.dsh/skills/comfyui/comfy.py status
$PY ~/.dsh/skills/comfyui/comfy.py plan "▶▷MiniMaxH3-加速视频流整合"
```

### 新机器配置更好？先标定，别套用旧数字

包里的 **0.8 档 / 8 秒 / 单镜 8 分钟** 是在 **8G 显存**上量的。换更强的卡，这些数字**两个方向都会错**：
更大的显存能上更高分辨率、更长镜头；而 8G 上"不能用"的模型（fp16 / nvfp4）可能反而变成正确选择。

`calibrate.py` 会：
- 报出显卡型号、显存、**算力**（sm_120+ 的 Blackwell 原生支持 FP4）
- 由小到大试一组（分辨率 × 时长），**遇到失败就停**，说明顶到了上限
- 给出推荐档位和全片排期，可直接填回 `SKILL.md` 的标准表

用 `--budget 分钟` 控制花多少时间（默认 90 分钟）。**更大的显存会解锁三件事**：

| 解锁 | 说明 |
|---|---|
| 更高分辨率 | 1.5–2.0 档（1664×928 / 1920×1088），接近 1080p |
| 更长单镜 | H3 官方上限 **15 秒**；之前为了显存只敢用 6–8 秒 |
| 音色克隆 | 节点的 `ref_audios` 本来就支持参考音频，可让同一角色跨镜音色一致 |

如果是 **RTX 50 系（Blackwell）**：工作流里本来就有 `qwen3vl_32b_minimax_h3_nvfp4_awq` 这个文本编码器。
在 40 系上它很慢（没有 FP4 硬件，实测换与不换耗时都是 1009 秒），但 50 系**原生支持 FP4**，可能又快又准。

### ⚠️ 换机器有一件事必须重做

**换了量化版本或模型来源后，锁脸能力可能变。**
装完先拿一镜做身份验证：关键帧 → I2VA → 抽帧对比 `refs/character_*.png`，**过了再开正式生产**。

产出应当满足：**分辨率与档位对应 / 24fps / 帧数 = 5+17k / 音频 32000Hz 立体声**。
一键核对：

```bash
$PY ~/.dsh/skills/HORSEmovie/scripts/qa_shot.py <产出的.mp4> --megapixels 0.8
```

---

## 第 6 步（可选）：把《归墟·天裂》连素材搬过去

```bash
rsync -avh ~/comfy-projects/tianlie/ 新机器:~/comfy-projects/tianlie/
```

`refs/`（角色与场景参考图）、`out/`（产物）、`shots.json`、`HANDOFF.md` 都在里面。
**参考图必须一起搬**，`shots.json` 引用的是它们。

---

## 常见坑

| 现象 | 原因 |
|---|---|
| DSH 认不出 skill | 目录层级错了。必须 `~/.dsh/skills/HORSEmovie/SKILL.md` |
| `~/.dsh` 不存在 | DSH 还没跑过。第一次启动会创建 |
| 工作流加载报红 | 自定义节点没装全（第 4 步） |
| 报 `Value not in list: unet_name` | 模型文件名对不上（`models.txt` 标 ⚠️ 的那几个） |
| 跑起来没声音 | 提示词里没写 `<d>`；或 `non_diegetic_music` 没写 `N/A` |
| 慢得离谱 | 用错工作流——**必须**是 `▶▷MiniMaxH3-加速视频流整合`（带 SageAttention 补丁那条） |
| 输出是正方形 | 工作流里存的是 `1:1`，要显式覆盖画幅 |
| 换脸 | 走了 Ref2VA。必须用 I2VA（关键帧当首帧） |
| 出现两个同一个人 | 提示词里让镜头"推"了。改成固定机位 + 硬切换景别 |

**每一条的完整"症状→原因→处理"在 `skills/HORSEmovie/references/pitfalls.md`。**
