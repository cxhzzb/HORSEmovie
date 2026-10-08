# HORSEmovie 安装说明

把《归墟·天裂》这套 AI 微电影生产线搬到另一台电脑。

**一句话**：skill 只有 128 KB，五分钟装完；花时间的是 **ComfyUI + 65 GB 模型 + 一堆自定义节点**。

> **Windows 部署**：`install.sh` 是 bash 脚本，Windows 上请改用
> [`ports/INSTALL-windows.md`](ports/INSTALL-windows.md)。它记录了 2026-10-08 在
> Windows 11 + RTX 5060 Ti 上完成部署的全部改动，含三个必须处理的坑：
> ① 文件系统 skill 发现机制失效 → 改用 `ports/dsh-plugin-horsemovie` 插件注册；
> ② H3 加速工作流依赖的 `H3PromptEdit` / `H3PromptPolish` 节点包缺失 → 用本机副本绕过；
> ③ 两个官方量化版模型不存在 → 用同架构替代件（`pipelines.json` 的 `auto_set` 自动替换）。

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

## Windows（ComfyUI Desktop）：用 `install.ps1`

Windows 上不用 `install.sh`（那是给 Linux 的）。同一个包里有 PowerShell 版本：

```powershell
.\install.ps1 -DryRun        # 先看它要做什么，不改任何东西
.\install.ps1                # 自动探测 CompyUI（含 ComfyUI Desktop）、装 skills + 工作流 + API 图、跑 doctor.py
.\install.ps1 -Comfy 'F:\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI'
.\install.ps1 -Force         # 同名工作流覆盖（先备份 .bak-时间戳；默认是不覆盖，另存 "(bundle)" 副本）
```

它和 `install.sh` 的差别（都是 Windows / Desktop 逼出来的）：

| 事情 | Windows 上的实际情况 |
|---|---|
| ComfyUI 位置 | 常见是 ComfyUI Desktop：根目录是 `...\ComfyUI-Installs\ComfyUI\ComfyUI`，`install.ps1` 会读 `%APPDATA%\Comfy Desktop\installations.json` 自动找 |
| 模型分两处 | Desktop 把 `models/` 拆成「安装目录」+ 一个共享目录。`install.ps1` 会从 `shared_model_paths.yaml` 读出来，写进 `local.json` 的 `extra_model_roots`，doctor.py 认它 |
| 模型改名 | 同一台机器上 H3 文本编码器可能是 `qwen3vl_32b_h3_ultra_uncensored_heretic_int8_convrot`（而不是包里的 `..._minimax_h3_int8_convrot`），生图模型可能在 `Qwen\` 子目录里。用 `-ModelAlias @{'原名'='本机名'}` 告诉脚本，或写进 `local.json` 的 `model_aliases` |
| 启动方式 | 没有 `启动ComfyUI.sh`。Desktop 的 `start_comfyui.bat` 才是无头启动入口，`install.ps1` 会把路径写进 `local.json`，`run_film.py` 的看门狗用它 |
| 控制台编码 | Windows 控制台是 GBK，打印带 `▶▷` 的工作流名会 `UnicodeEncodeError`。包里的脚本已加 UTF-8 兜底 |
| 本机覆盖文件 | `local.json`（路径）和 `pipelines.local.json`（节点 id）是**每台机器一份**的部署产物，不进仓库 |

### ⚠️ DSH 只认 kebab-case 的 skill 名

`/^[a-z0-9]+(?:-[a-z0-9]+)*$/` —— frontmatter 写成 `name: HORSEmovie` 这种驼峰/大写名，
DSH **不报错、直接静默忽略整条 skill**（目录在、文件在，但会话里看不到，`skill` 工具也调不到）。
包里已经改成 `name: horsemovie`（目录名保留 `HORSEmovie`）。`doctor.py` 现在会查这一条。

---

## Windows 实战补充（ComfyUI Desktop，实测踩出来的四条）

在 Windows + ComfyUI Desktop 上部署时，下面四件事会让"看起来装好了"变成"跑不出片"：

### 1. 用 `--fast-disk` 启动 ComfyUI

H3 视频链路要 **20GB 主模型 + 25GB 文本编码器 + 5GB VAE**。ComfyUI 默认把权重**拷进内存**
（日志 `Model storage policy: fast_disk=False`）。内存不够时（32GB 也够呛）Windows 会疯狂换页：
实测**文本编码器阶段卡了 2 小时都没进到采样**，GPU 空转 99%；改成 `fast_disk=True`
（权重直接从 NVMe mmap）后，**加载+采样+VAE 解码 ≈ 33 分钟出片**。

ComfyUI Desktop 的启动参数写在 `%APPDATA%\Comfy Desktop\installations.json` 的
`launchArgs` 里，加上 `--fast-disk` 即可（改完重启 ComfyUI；在 UI 里点 Restart 也会带上）。
`./install.ps1` 会自动把 `--fast-disk` 写进去。

### 2. `SaveVideo` 不被当输出节点 —— 会"成功"但不出片

ComfyUI 0.37 的 `validate_prompt` 用 `class_.OUTPUT_NODE is True`（**身份**比较）收集输出节点，
而 `SaveVideo` 是新式 comfy_api(V3) 节点，走不到这条路径；`object_info` 里它却是 `output_node=true`
（那边用 `== True` 判）。后果：只要图里还有别的 output_node（`TE_text_display`、各种 prompt
enhancer 都是），提交会 **542ms 返回 success 但一个视频都不产出**。

`comfy.py` 现在提交时显式带 `partial_execution_targets`，并按 `--project/--shot` 只点名本次真正要的
那个保存节点 —— 服务器的校验只看被点名节点的上游，点名太宽会把没在用的分支拉进来一起校验，
而那些分支常常引用着早就删掉的输入图，直接 400。

### 3. 模型名的分隔符要按服务器清单对齐

同一份工作流在 Linux 上写 `Qwen/x.safetensors`，Windows 的清单里是 `Qwen\x.safetensors`，
而 ComfyUI 的 combo 校验是**严格字符串比对**，于是报 `Value not in list`，看着像缺模型。
`comfy.py` 现在拿服务器的 `object_info` 逐个核对，能在两种分隔符之间换的就换，换完还不在清单里的**报出来**。

### 4. Windows 控制台是 GBK

打印带 `▶▷` 的工作流名会 `UnicodeEncodeError: 'gbk'`。包里的脚本已强制 UTF-8 兜底；
自己写脚本时记得 `sys.stdout.reconfigure(encoding="utf-8")`。

**另一半的坑**：父进程捕获子进程输出时，`subprocess.run(..., text=True)` 在 Windows 上按**本地代码页（GBK）**
解码 —— 而子进程（已经带 UTF-8 兜底）打印的是 UTF-8，于是 reader 线程抛
`UnicodeDecodeError: 'gbk' codec can't decode byte 0xa4`，调用方只看到"失败"。
`calibrate.py` 就因此把两次成功的渲染判成了 ✗。包里所有捕获输出的地方都补上了：

```python
subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
```

---

## 加速：换机器后先做一次"加速审计"（2026-10-08 实测）

12G 显存跑 20GB 主模型 + 25GB 文本编码器，速度几乎完全由**分辨率 × 帧数**决定，
跟工作流大小、分支多少没关系。实测（热机、`--fast-disk`，ComfyUI 0.37 + H3 int8）：

| 配置 | 耗时 | 秒/(MP·帧) |
|---|---|---|
| 0.4MP / 5s / 4 步 | 1.3 分钟（含冷启动加载） | — |
| 0.6MP / 8s / 8 步 | 4.2–4.4 分钟 | 2.19–2.29 |
| **0.6MP / 8s / 8 步 + EasyCache** | **3.43 分钟** | 1.71 |
| 0.8MP / 8s / 8 步 | 6.0 分钟 | 2.35 |
| **0.8MP / 8s / 8 步 + EasyCache** | **4.69 分钟** | 1.84 |
| 1.0MP / 8s / 8 步 | 8.6 分钟 | 2.68 |

三条结论：

1. **`EasyCache` 是唯一现成可用的加速**（ComfyUI 自带 `comfy_extras.nodes_easycache`，
   接在 LoRA 之后、guider 之前）：默认 `reuse_threshold=0.2` 时 8 步**跳过 2 步**，
   自报 `1.33x`，实测整体 **1.28×**。接法见下。
2. **第三方 `TE-Speed-MiniMaxH3` 在 ComfyUI 0.37 上跑不起来**：
   `TypeError: FinalLayer.forward() missing 3 required positional arguments:
   'sigma', 'sample_sigmas', and 'shifts'`。上游最新（2026-09-06，"适配官方新版 H3 接口"）
   同样报错；换 3 种接线都一样 —— 是插件与 ComfyUI 版本的签名不匹配。
   （所以自己的工作流里那个节点是 bypass 状态，加速改走 turbo LoRA + 8 步。）
3. **`BlockSparseAttention` 要序列够长才有用**：`min_tokens` 默认 12288，
   而 0.6MP/8s 的 H3 latent 只有约 2565 token → 保持 dense，等于没开。
   长镜头（15s）或 1.2MP 以上再考虑；三种方法里只有 `sol-attn` 免训练。

想采 EasyCache，把它插到 model 链上即可（API 图里加一个节点）：

```json
"362": { "class_type": "EasyCache",
         "inputs": { "model": ["140", 0], "reuse_threshold": 0.2,
                     "start_percent": 0.15, "end_percent": 0.95, "verbose": true } }
```
然后把 guider 的 `model` 从 `["140",0]` 改到 `["362",0]`。
每上一档都要**抽帧复验**：跳步会在动作和细节上体现出来。

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
| DSH 认不出 skill | 目录层级错了。必须 `~/.dsh/skills/HORSEmovie/SKILL.md`**，且 frontmatter 的 `name` 必须是 kebab-case**（`horsemovie`，不是 `HORSEmovie`）——写成大写名 DSH 会静默忽略 |
| 打印工作流名就崩（`UnicodeEncodeError: 'gbk'`） | Windows 控制台是 GBK。脚本里的 stdout 已强制 UTF-8；自己写脚本时记得 `sys.stdout.reconfigure(encoding="utf-8")` |
| `Value not in list: unet_name` 但模型明明在 | 模型在子目录里（`Qwen\...`）或改了名。用 ComfyUI 里显示的名字；doctor.py 的 `model_aliases` 可以声明对应关系 |
| `~/.dsh` 不存在 | DSH 还没跑过。第一次启动会创建 |
| 工作流加载报红 | 自定义节点没装全（第 4 步） |
| 报 `Value not in list: unet_name` | 模型文件名对不上（`models.txt` 标 ⚠️ 的那几个） |
| 跑起来没声音 | 提示词里没写 `<d>`；或 `non_diegetic_music` 没写 `N/A` |
| 慢得离谱 | 用错工作流——**必须**是 `▶▷MiniMaxH3-加速视频流整合`（带 SageAttention 补丁那条） |
| 输出是正方形 | 工作流里存的是 `1:1`，要显式覆盖画幅 |
| 换脸 | 走了 Ref2VA。必须用 I2VA（关键帧当首帧） |
| 出现两个同一个人 | 提示词里让镜头"推"了。改成固定机位 + 硬切换景别 |

**每一条的完整"症状→原因→处理"在 `skills/HORSEmovie/references/pitfalls.md`。**
