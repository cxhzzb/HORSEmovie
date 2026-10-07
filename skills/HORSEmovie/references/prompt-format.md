# MiniMax H3 官方提示词规范（本项目验证版）

> 依据：MiniMax H3 官方 Base / Ref2VA Guide。**字段名、标签、固定句、时间格式必须照抄，不要自创。**
> 本项目在 2026-10-05 用这套格式实测通过（中文台词、环境声、无 BGM、人物锁脸）。

## 0. 三条最容易错的

1. **重写段落一律英文。** 只有 `<d>` 里的台词、歌词、画面里真实可见的文字保留原语言。
2. **I2VA 第一行是官方固定句，一字不能改**，且后面要空一行。
3. **台词只写在 `<d>[语言] …</d>` 内**；音色、语气、年龄感、语速、表情、动作全写在 `<d>` 外面。

---

## 1. I2VA（首帧生音视频）— 本项目主力模式

### 结构：固定首行 + 三段式

```text
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] ...
[Shot 2] At 00:05.000, the shot cuts to ...

overall_soundscape: ...

non_diegetic_music: N/A
```

**`<Picture 1>` 就是目标视频 0.00 秒的真实首帧**（本项目的关键帧）。先承认它已经给出人物、构图、场景，
然后描述**从这张图之后发生的变化**——不要用新形容词重新设计这张图。

**首帧即构图。** 想要特写就出特写关键帧，不要在提示词里让镜头"推"过去。

### 三段各自写什么

| 字段 | 内容 | 长度 |
|---|---|---|
| `integrated_multimodal_description` | 按时间写：风格 → 初始构图 → 主体外观位置 → 动作与反应 → 镜头 → 台词 → 状态变化 | 生成类通常 350–500 英文词；对白密集时优先保证完整时间轴 |
| `overall_soundscape` | 环境底噪、风雨、机械、脚步、衣料、碰撞、呼吸、喘息等**非语言**声音 | 1–4 句英文，单段连续 |
| `non_diegetic_music` | 只有观众听得到的配乐（乐器、速度、节奏、进出时机） | 1–3 句；**没有就写 `N/A`** |

### 其他固定首行（备查）

```text
# FL2VA（首尾帧）
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot N) aligns with the S.SS-second mark of the target video.

# L2VA（尾帧）
How the reference pictures align with the target video — <Picture 1> (from [Shot N]) aligns with the S.SS-second mark of the target video.
```

`S.SS` 精确到两位小数，且是**有效视频时长**。

---

## 2. Ref2VA（全参考）— 六段式

```text
subject_definitions:
<Subject 1> is ... from <Picture 1>, ...

summary:
[reference generation] ...

retention_analysis:
<Subject 1> (appears in [Shot 1]): fully_preserved - ...

detailed_description:
The target video is ...
[Shot 1] ...
[Shot 2] At 00:03.000, ...

overall_soundscape:
...

non_diegetic_music:
N/A
```

### 四类标签

| 标签 | 用途 | 本项目用法 |
|---|---|---|
| `<Subject N>` | 可复用的可见内容（人/物/场景/服装/特效） | 角色与场景 |
| `<Picture N>` | 只当图片承担**具体帧锚点/构图锚点**时才单列 | 一般不用，身份图写进 Subject 即可 |
| `<Video N>` | 整段视频级关系（编辑源、续写起点、运镜参考） | 未用 |
| `<Audio N>` | 被复制或被参考的音频信号（可做**音色克隆**） | 未用；管线支持 `ref_audios` |

**`<Picture N>` 编号按上传顺序，图片/视频/音频分别独立计数。**

### 保留关系标记

- 视觉：`fully_preserved` / `partially_preserved` / `attribute_transfer` / `weak_reference`
- 音频：`fully_copy` / `partially_copy` / `reference` / `weak_reference`

### ⚠️ 但本项目不要用 Ref2VA 出片

**它会换脸。** 实测：同一个提示词、同一批参考图，共工从"42 岁、灰白胡须、左脸星图"
变成"年轻、无胡须、星图几乎消失"。Ref2VA 只锁得住气质与服装，锁不住同一张脸。

Ref2VA 的提示词写法仍然有用——**WAN3 等其他模型用得上**，只是本地 H3 出片要走 I2VA。

---

## 3. Shot 与时间轴

- **`[Shot 1]` 不写时间戳。**
- 后续用严格递增的切点：`[Shot 2] At 00:05.000, the shot cuts to ...`
- 可用动词：`the camera cuts to` / `the shot cuts to` / `the shot transitions to` / `the shot changes to`
- 只有用户明确要求才用叠化/淡入淡出/划像。
- **切镜必须带来新信息**（新主体/新空间/新状态/新视角/新时间阶段/关键反应）。
  只是景别稍变，优先运镜；但本项目里**景别变化一律用 cut**（见坑位表：推镜会重影）。
- 信息预算（工程经验，非硬限制）：2 个镜头最稳；3 个镜头适合"建立—行动—反应"；4 个以上容易身份漂移。

---

## 4. 运镜词表（类型 + 幅度 + 速度）

| 维度 | 官方表达 |
|---|---|
| 类型 | `Zoom In/Out`、`Push In/Pull Out`、`Pan Left/Right`、`Truck Left/Right`、`Tilt Up/Down`、`Pedestal Up/Down`、`Arc Shot`、`Tracking Shot`、**`Static Shot`**、`Shake Slightly/Strongly`、`POV`、`Roll Clockwise/Counterclockwise` |
| 幅度 | `with small amplitude` / `with large amplitude` |
| 速度 | `at slow speed` / `at fast speed` |

写成自然句：`The camera pushes in with small amplitude at slow speed toward the letter in her hands.`

**本项目纪律：**
- **台词期间 = `Static Shot`**（或至少不叠任何位移）。
- 想固定机位时，把话写死：`The camera does not move at all in this shot: no push, no zoom, no pan, no handheld drift.`
- 一个时间段只指定**一个主运镜**。

---

## 5. 说话人、台词与口型

### 稳定说话人 ID

- 真实发声的主体用 `(S1)` `(S2)` `(S3)`，按**目标视频中第一次实际发声的顺序**编号，全程复用。
- 同时说话：`(S1,S2)`。**从不发声的角色不要分配 ID。**

### `<d>` 写法

```text
The young man with a low, hoarse, slightly rasping voice (S1) says quietly: <d>[Chinese] ……这个不是废铁。</d> He closes his mouth and keeps looking down into the stone.
```

- `<d>` 内**只有** `[Language]` 和**原台词**，标点逐字保留。
- `<d>` 外写：谁说、音色、年龄感、语速、情绪、动作、表情。
- 说完接一句状态收束：`He closes his mouth and holds the camera's gaze without blinking.`

### 旁白

```text
A calm adult female narrator with a warm, measured Mandarin delivery (S1) says in an off-screen voiceover: <d>[Chinese] 今天，我们从这里出发。</d> while the on-screen character's lips remain completely closed.
```

必须用 `says in an off-screen voiceover` 并锁住画面人物嘴唇。

### 其他

- 跨切镜的同一句台词用 `<scenetrans>` 并写明声音连续，**不要在两个镜头各复制一遍完整台词**（会读两遍）。
- 台词被片尾故意截断用 `<cutoff>`。
- 口型稳定：一段里少切说话人；一个镜头只让一个主体承担主要台词；台词期间不要同时高速跑动/遮脸/转身。

### 台词数量要与时长匹配

按中文正常语速 **≈4–5 字/秒** 估算。8 秒的镜头，台词控制在 **25 字以内**，
并且要留出动作和收束的时间。

---

## 6. 声音分层（四层，不要串）

| 声音 | 写在哪 |
|---|---|
| 对白、歌唱 | 当前 Shot 的 `<d>` |
| 与画面同步的画内声（按钮、门铃、枪声、收音机） | 当前 Shot 的正文 |
| 全片环境与物理声（雨、风、脚步、衣料、呼吸） | `overall_soundscape` |
| 只有观众听得到的配乐 | `non_diegetic_music` |

**完整台词/歌词只在 `<d>` 里出现一次**，不要在 soundscape 或 music 段重复，否则会重复朗读。

**要环境音、不要 BGM** → `overall_soundscape` 写具体材质，`non_diegetic_music: N/A`。

环境音要写到**材质**层面，例：

```text
overall_soundscape: A dry wind moves continuously through the broken tower, producing a low resonant hum around the hanging cable. Fine grit hisses along the armour. A single distant metal groan comes from the wreckage behind him. His breathing is slow and even.
```

---

## 7. ⛔ 禁用词（会真的生成字幕卡）

**正向提示词里绝不能出现 `title` / `credit` / `text` / `caption` / `logo` / `subtitle` / `lettering` / `sign` 这类词。**

⚠️ **连否定式也别写**（`no text`、`不要字幕` 同样会触发）。直接不提。

实测：045 镜的提示词里写了 `freeze frame as the pupil dilates, title card`，
模型就**真的生成了一张写着 "A FILM BY CIANN BEUSS" 的英文演职员字幕卡**。

真正需要画面文字时，按官方写法用英文双引号包住原文：`A red neon sign reading "营业中" glows above the doorway.`

---

## 8. 能力边界（官方）

| 项目 | 规格 |
|---|---|
| 单次输出时长 | **4–15 秒** |
| 帧率 | 24 fps |
| 输出音频 | 32 kHz 立体声 |
| Ref2VA 图片 | 最多 9 张 |
| Ref2VA 视频 | 最多 3 段，每段 2–15 秒，总长 ≤15 秒 |
| Ref2VA 音频 | 最多 3 段，每段 2–15 秒，总长 ≤15 秒 |
| 混合素材合计 | ≤12 个 |
| 稳定对白语言 | 中文、英语、日语、韩语、法语、德语、意大利语、西班牙语、葡萄牙语、俄语、阿拉伯语等 |

**本地管线时长换算**：`#135.value`（I2V）/ `#292.value`（Ref2V）单位是**秒**，
内部映射为帧数，**合法帧数 = 5 + 17k**（24fps）。

| 秒 | 帧 | 实际时长 |
|---|---|---|
| 5 | 124 | 5.17s |
| 6 | 158 | 6.58s |
| 7 | 175 | 7.29s |
| 8 | 192 | 8.00s |

---

## 9. 可直接复用的模板

### 9.1 有台词的单人镜头（I2VA）

```text
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] Live-action cinematic <genre>. Photographed on a large-format anamorphic lens wide open; the image carries real optical character: fine film grain, gentle highlight roll-off, true blacks with detail in them, and a natural falloff into bokeh. Surfaces are rendered at macro fidelity — every pore, every wet strand of hair, every micro-ripple, every scratch and every mote of suspended matter reads as a physical object, not as a smooth CG surface. Nothing is plastic, nothing is denoised, nothing is airbrushed. The camera does not move at all in this shot: no push, no zoom, no pan, no handheld drift; the framing stays exactly as established in <Picture 1>. Only one person exists in the entire frame — <身份描述，与首帧一致>.
<微动作：只写呼吸、眼神、光纹、发丝、粒子等小幅度变化。>
<角色描述 with voice quality> (S1) says <delivery>: <d>[Chinese] <台词></d> He closes his mouth <收束动作>.
[Shot 2] At 00:0X.000, the shot cuts to <更紧/更松的景别>. The camera remains completely static in this shot as well. <这一镜的新信息 + 微动作。>

overall_soundscape: <环境底噪按材质写>。

non_diegetic_music: N/A
```

### 9.2 无台词的气氛镜头

同上去掉台词，改用 `overall_soundscape` 承担全部声音叙事；`non_diegetic_music` 仍写 `N/A`
（除非用户明确要配乐）。

### 9.3 质感强化语（本项目验证有效，直接抄）

```text
Photographed on a large-format anamorphic lens wide open; the image carries real optical character: fine film grain, gentle highlight roll-off, true blacks with detail in them, and a natural falloff into bokeh. Surfaces are rendered at macro fidelity — every pore, every wet strand of hair, every micro-ripple, every scratch and every mote of suspended matter reads as a physical object, not as a smooth CG surface. Nothing is plastic, nothing is denoised, nothing is airbrushed.
```

---

## 10. 两个实测通过的完整范例

见 `~/comfy-projects/tianlie/shots.json` 里 `008` 与 `030` 两镜的 `motion` 字段
（008 = 水下倒影特写 + 切镜到眼部大特写；030 = 共工中景 + 5 秒硬切到面部特写、星图由暖金转冷蓝）。
它们分别验证了：人物锁脸、无重影、中文台词、环境声、无 BGM、零画幅错误。

### 写 I2VA 提示词的自检

- [ ] 第一行是官方固定句，且后面空了一行
- [ ] `[Shot 1]` 没有时间戳，后续切点严格递增且小于总时长
- [ ] 首帧景别 = 目标景别；没有让镜头"推"过去
- [ ] 台词全在 `<d>` 里、只出现一次、字数与时长相称
- [ ] 说话人有稳定 `(S1)`，从不发声的角色没分配 ID
- [ ] `overall_soundscape` 写了具体材质
- [ ] `non_diegetic_music` 写了 `N/A`（或明确的配乐描述）
- [ ] 全文没有 `title` / `credit` / `text` / `caption` / `logo` 及其否定式
