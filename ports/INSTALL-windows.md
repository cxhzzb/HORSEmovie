# Windows 部署说明

> 本文记录 **2026-10-08 在 Windows 11 + RTX 5060 Ti 16G** 上完成的一次真实部署。
> 原 skill 全部按 Linux 写死（`~/ComfyUI`、`.venv/bin/python`、`pgrep`、`:` 分隔的路径列表），
> 这里记录平台适配的做法，以及三个必须处理的坑。

---

## 0. 一次跑完

```powershell
python C:\DEEPHARNESS\HORSEmovie-port\port_and_install.py
```

这个脚本做四件事（可重复执行、幂等）：

| 步骤 | 内容 |
|---|---|
| 1 | 把 `skills/HORSEmovie` 与 `skills/comfyui` 装到 skill 根目录，**目录名与 frontmatter `name` 统一为 kebab-case**（`horsemovie`） |
| 2 | 逐文件做平台移植（见第 2 节） |
| 3 | 装三个工作流到 `ComfyUI\user\default\workflows\`（同名先备份 `.bak-时间戳`） |
| 4 | 打本机补丁（见 `patch_local.py`），并把 skill 同步到自定义根 |

> 仓库根目录的 `install.sh` 是 bash 脚本，Windows 上不适用。`doctor.py` 是自检脚本，
> 移植后可从 skill 根目录调用。

---

## 1. skill 的命名与注册（最容易踩）

### 1.1 目录名与 `name` 必须是 kebab-case

DSH 用这条正则校验 skill 名：

```js
const SKILL_NAME = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
ctx.logger.warn(`skill file ${path} ignored: invalid skill name "${name}"`);
```

上游的 `name: HORSEmovie` 含大写，**不匹配**，会被静默忽略（只写一条 warn 日志，模型目录里看不到）。
所以移植时必须同时改：

- 目录名：`skills/HORSEmovie/` → `skills/horsemovie/`
- frontmatter：`name: HORSEmovie` → `name: horsemovie`

### 1.2 用 Cordis 插件注册，不要依赖文件扫描

**本次部署遇到的关键问题：文件系统 skill 发现机制失效。** 现象是把 skill 放进任何目录都扫不到。
排查后的三条证据：

1. **宿主平面的 provider 被预设禁用** —— `cordis` 预设的补丁里写着：
   ```yaml
   - id: skill-filesystem
     disabled: true    # 本地发现归预设所有
   - id: tool-skill
     disabled: true
   ```
   改这个已禁用的条目不会有任何效果。

2. **预设自己声明的 `customSkillDirs` 也失效** —— 预设把 `customSkillDirs` 指向
   `@deepseek-ai/dsh-agent-preset/skills`，但该目录里的 skill 同样加载不了：
   ```
   skill agent-experience          → unknown
   skill cordis-plugin-development → unknown
   ```
   连它自己配置的目录都发现不了，说明不是路径问题，而是整条链路失效。

3. **`office-docx` 能加载 ≠ 文件扫描可用** —— 它由 `@deepseek-ai/dsh-skill-office` 插件
   通过 `assetRoot` 自己读取，与 `skill-filesystem` 无关。

**验证过无效的做法**（不要重试）：
`~/.dsh/skills`、`<项目根>/.dsh/skills`、`<项目根>/.agents/skills`、
profile 补丁加 `customSkillDirs`、加 `bundledSkillDir`、
塞进 bundled 目录 `office-skills`、平铺 `<name>.md`、junction 与真实目录两种形式。

**有效做法：写 Cordis 插件用代码注册。** 见 `ports/dsh-plugin-horsemovie/`：

```js
export const inject = ["skills"];

export function apply(ctx) {
  const raw = readFileSync("<SKILLS_ROOT>/horsemovie/SKILL.md", "utf8");
  ctx.skills.register({
    name: frontmatterField(raw, "name"),
    description: frontmatterField(raw, "description"),
    whenToUse: frontmatterField(raw, "whenToUse"),
    content: bodyOf(raw),
    source: "user-dsh",
    provider: "horsemovie-local",
    invocation: { modelInvocable: true, userInvocable: true },
    resourceBase: { kind: "directory", path: "<SKILLS_ROOT>/horsemovie" },
    path: "<SKILLS_ROOT>/horsemovie/SKILL.md",
  });
}
```

安装方式（官方规范：bundle 写在 workspace，再用 `plugin_manager install_bundle` 装）：

```
plugin_manager(action: install_bundle, target: "<插件包的绝对路径>")
```

返回 `application: applied` 即成功。**正文是运行时从磁盘读的**，所以改 `SKILL.md`
后无需重装插件。注意 **skill 目录在会话开始时拉取一次**，装完要**新开会话**才能在 `/` 菜单看到。

---

## 2. 平台移植清单

所有平台相关假设集中到一个配置层 `_horse/config.py`，脚本不再自己猜平台。
每个值都可用环境变量覆盖：

| 环境变量 | 作用 |
|---|---|
| `COMFY_DIR` | ComfyUI 本体（含 `main.py`） |
| `COMFY_PROJECTS` | 项目目录 |
| `COMFY_HOST` | ComfyUI 服务地址 |
| `COMFY_WORKFLOW_DIRS` | 工作流目录，多个用 `os.pathsep` 分隔 |
| `COMFY_MODEL_DIRS` | 模型根目录，多个用 `os.pathsep` 分隔 |
| `FFMPEG` / `FFPROBE` | 外部可执行文件路径 |
| `HORSE_HOME` | skill 根目录 |

改动逐项：

| 位置 | Linux 写法 | Windows 处理 |
|---|---|---|
| ComfyUI 路径 | `~/ComfyUI` | `_horse_config.COMFY_DIR` |
| venv 解释器 | `.venv/bin/python` | `.venv\Scripts\python.exe`（`venv_python()` 按平台分流） |
| 工作流列表分隔 | `:` 硬编码 | `os.pathsep`（Windows 是 `;`） |
| 进程查找 | `pgrep -f "main.py --listen"` | `tasklist` |
| 冷启动附加项 | 设 `LD_LIBRARY_PATH` | 仅非 Windows 设置；`start_new_session` 换平台参数 |
| 启动脚本名 | `启动ComfyUI.sh` | `启动ComfyUI.bat` |
| 模型查找 | 只看 `ComfyUI/models` | `find_model()` 跨多个根目录按文件名递归找 |
| ffmpeg / ffprobe | 直接调 `"ffmpeg"` | 由 `_horse_config` 解析绝对路径 |
| **模型名分隔符** | 服务器要 `/` | **Windows 版 ComfyUI 暴露的模型名用 `\`** —— 原实现方向是反的 |
| **模型名对齐** | —— | `resolve_combo_values()` 按服务器实际选项做文件名匹配 |

### 2.1 两个必须自己修的坑

**① 路径分隔符方向反了。** 上游 `comfy.py` 的 `norm_value()` 注释写着
"服务器要 `/`"，于是把 `\` 一律转成 `/`。那是 Linux 的假设 —— Windows 版 ComfyUI
暴露的模型名用**反斜杠**，于是连存在的模型都被判 `value_not_in_list`。

**② `--set` 的值没走归一化。** `apply_set()` 把值原样塞进 API，绕过了 `norm_value`。
两处都要处理。

**③ 跨机器模型名对不上。** 同一个模型在不同来源下量化版命名不同
（如 `qwen3vl_32b_minimax_h3_int8_convrot` vs `qwen3vl_32b_h3_ultra_uncensored_heretic_int8_convrot`）。
`resolve_combo_values()` 在提交前按服务器的 `object_info` 选项做"带目录相对路径 →
纯文件名"逐级匹配，能唯一对上就替换，并打印 `resolve #N.field: 旧 -> 新`。

---

## 3. 幽灵节点：H3 加速工作流的提示词通路

`▶▷MiniMaxH3-加速视频流整合` 的提示词通路是：

```
#368 H3PromptPolish → #369 H3PromptEdit → #133.prompt
```

**但 `H3PromptPolish` / `H3PromptEdit` 这两个节点包在目标机器上可能没有提供方。**
判断方法（整机搜索后，这两个名字只出现在工作流 JSON 里就是缺）：

```powershell
Get-ChildItem <ComfyUI根> -Recurse -File -Include *.py,*.js,*.json |
  Where-Object { $_.FullName -notmatch "\.venv|node_modules" } |
  ForEach-Object { if ((Get-Content $_.FullName -Raw -EA SilentlyContinue) -match "H3PromptEdit|H3PromptPolish") { $_.FullName } }
```

缺失时 `comfy.py` 的转换会丢弃这些"未知节点"，于是 `#133.prompt` 无人喂值，
工作流**跑不出视频**（校验能过，但没有提示词）。此时用
`ports/make_local_h3_workflow.py` 生成一个本机副本：

- 摘掉全部幽灵节点及其连线（本次实测摘掉 8 个节点、13 条连线）
- 清空视频节点的 prompt widget，改由 bridge 直接写
- **不动上游原始工作流文件**

然后把 `pipelines.json` 的 `video-main` / `i2v-minimax` 指向副本，
`patch` 里的提示词字段从 `#369.text` 改成 `#133.prompt`。

---

## 4. 模型：替代件的处理

目标机器上可能没有工作流里写死的量化版模型。本次实测缺两个：

| 工作流里写的 | 实际可用 | 处理 |
|---|---|---|
| `qwen3vl_32b_minimax_h3_int8_convrot.safetensors` | `qwen3vl_32b_h3_ultra_uncensored_heretic_int8_convrot.safetensors` | `pipelines.json` 的 `auto_set` 自动替换 |
| `qwen-image-2.1-UC-int8_convrot.safetensors` | `Qwen\qwen_image_2.1_int8_convrot.safetensors` | 运行时 `--set '#476.unet_name=…'` |

其余 6 个必需模型正常就位（合计约 54 GB）。
`doctor.py` 会把这类"看起来缺、其实只是命名不同"的项一并列出，附带可能的位置。

**注意**：替换文本编码器会改变画面表现，换完应做一次身份验证（关键帧 → I2VA → 抽帧对比）。

---

## 5. 开工前的自检顺序

```powershell
$PY = "<bundled python>"          # 见 _horse/config.py 的 bundled_python()
$C  = "<SKILLS_ROOT>\comfyui\comfy.py"

& $PY "<SKILLS_ROOT>\_horse\doctor.py"        # 环境自检（只读）
& $PY "<SKILLS_ROOT>\_horse\config.py"        # 打印实际解析出的所有路径
& $PY $C status                                # 服务器 / 显卡 / 队列
& $PY $C plan "<视频工作流名>"                  # 开工前必做：看实际接线与节点 id
```

`plan` 的输出**优先于** `pipelines.json` —— 工作流一旦在 UI 里被改过，节点 id 就会变。

---

## 6. 实测结果（2026-10-08）

| 项目 | 结果 |
|---|---|
| 生图 | ✅ 832×640，民国写实风格正确（`docs/evidence/windows-smoke-image-832x640.png`） |
| 出视频 | ✅ 864×480 / 24fps / 124 帧 = 5+17×7 / 音频 32kHz 立体声 |
| 逐镜验收 | ✅ `qa_shot.py` 四项规格判定全过 |
| GPU | RTX 5060 Ti 16G，峰值利用率 89%、显存 8.1 GB、115 W |

### 一个实测得出的教训：必须用质量档

**4 步 / LoRA 0.75（省时档）会产生固定位置的橙色光斑伪影**，8 步 / 1.0 则干净。
对照证据：`docs/evidence/windows-smoke-video-4step-qa-BAD.png`（有伪影）
vs `windows-smoke-video-8step-qa.png`（干净）。

质量档参数声明在 `pipelines.json` 的 `i2v-minimax.fixed` 里：

```json
"fixed": { "#126.steps": 8, "#140.strength_model": 1.0 }
```

手工用 `comfy.py run` 测试时若走 `video-main` 条目，**不会**吃到这两个参数 ——
正常出片走 `render_shots.py --via i2v` 才会。

同样，`--check` 校验（提交后立即取消）会在 ComfyUI 历史里留下 `execution_interrupted`
记录、被 UI 显示为"失败"。那是校验的正常痕迹，不是真失败。
