# 制作包多音频规范（HTML / skills 调整说明）

## 范围及兼容性

标准版制作包加载器支持每镜任意数量的有序音频引用，不设单音频数量上限。
仍保留 ZIP 文件数量、总大小、安全路径检查，以及图片 1–9 张、视频最多 3 段等现有约束。
音频数量没有人为上限不表示 H3 模型或显存无限。请只引用该镜实际需要的素材。
RH 无制作包加载器，本次不涉及 RH。

主节点手动上传区仍是 3 个音频格，超过 3 个的制作包音频不在上传格显示，
但会在制作包检测/预览信息中逐条列出，并通过 `production_shot` 连线完整加载到后端。
必须保留这条连线；不要断开制作包后误以为手动上传区包含全部音频。

## 推荐 HTML 数据格式

把最终数据放进 `script type="application/json"`，ID 使用 `dh-project-data` 或 `mv-project-data`。
保留原有分镜数据字段；新增每镜 `audio_references` 列表。列表顺序就是该镜提示词的 Audio 编号。
项目级 `audio_references` 是可选资产目录，不是“每镜全部加载”的指令。

```html
<script id="dh-project-data" type="application/json">
{
  "package_mode": "digital_human",
  "aspect_ratio": "16:9",
  "fps": 24,
  "audio_references": [
    {"id": "A1", "file": "A1.MP3"},
    {"id": "A2", "file": "声音/A2.wav"},
    {"id": "A3", "file": "背景音乐.mp3"}
  ],
  "shots": [{
    "id": "H3-001",
    "title": "双人讲解",
    "seconds": 8.0,
    "range": "00:00:000–00:00:000",
    "references": ["P1", "P2"],
    "audio_references": [
      {"id": "A2", "range": "00:00:000–00:04:000"},
      {"id": "A1", "range": "00:02:000–00:06:500"}
    ],
    "prompt_zh": "<Picture 1> 中的人物参考 <Audio 1> 的音色；<Picture 2> 中的人物参考 <Audio 2> 的音色。",
    "prompt_en": "The person in <Picture 1> uses the voice identity of <Audio 1>; the person in <Picture 2> uses the voice identity of <Audio 2>."
  }]
}
</script>
```

上例只加载 A2、A1，不加载 A3。Audio 1 对应 A2，Audio 2 对应 A1：
**资产 ID 与分镜局部引用编号不是同一回事。** 图片仍按 references 数组的局部顺序编号。
混用视频参考时，视频自带音轨的内部编号由现有后端处理；HTML 的 Audio N 指独立音频列表的位置。

### 每镜音频条目

- 推荐 `{ "file": "相对路径.wav", "range": "00:00:000–00:05:300" }`，无需项目级目录。
- 或 `{ "id": "A2", "range": "..." }`：从项目级目录找到同 ID 文件，分镜字段覆盖资产默认字段。
- 也接受字符串 `"A2"`（从项目目录解析）或 `"声音/A2.wav"`（直接文件路径）。推荐对象格式，避免 ID/路径歧义。
- 新列表条目未写 range 时，默认 `00:00:000–00:00:000`，即完整音频。
- 结束为 0 表示到音频末尾；非零结束必须大于起始。超过实际文件末尾由现有音频裁剪逻辑截到末尾。
- 时间精度为 100ms，四舍五入，例如 `00:20:940` → `00:20:900`。
- range 表示从该音频素材取哪一段，**不是把该音频放在成片的第几秒**，也不会自动混音或安排多人说话轮次。
- 文件路径相对于制作包根目录，不是 HTML 所在子目录。优先精确相对路径，找不到时尝试唯一同名文件；同名歧义报错，不随机选择。
- 新列表显式写 `[]` 表示该镜无参考音频，即使包内有歌曲也不会自动加载。
- 新列表存在时覆盖旧 voice_reference / audio_file / master_audio，避免隐式多加一个音频。

## 分镜时长

新格式 `audio_references` 的分镜使用 HTML 声明的分镜时长，不使用第一条参考音频自动覆盖时长。
音色参考通常比视频短，不能用它推算视频长度。
数字人继续要求 `seconds` 和分镜 `range: "00:00:000–00:00:000"`；每条音频自己的 range 可以不同。
MV 使用原有分镜 range 计算时长；若同时给 seconds，应与范围差值一致。支持的分镜时长仍为 0.2–30 秒。
如果 MV 需要截歌曲，请显式把歌曲截取范围写在对应音频条目里；不会把镜头 range 自动套给新列表全部音频。

## 其他 HTML 形式

`script#feihou-shotlist` 格式也支持每镜 audio_references，但条目必须直接给 file；它不解析项目级资产 ID。

旧 DOM 卡片可在 `article.unit` 内添加：

```html
<span class="ref-chip" data-media-type="audio"
      data-file="A1.MP3" data-range="00:00:000–00:05:000">A1 音色</span>
<span class="ref-chip" data-media-type="audio"
      data-file="A2.wav" data-range="00:01:000–00:04:000">A2 音色</span>
```

DOM 顺序就是 Audio 1、Audio 2；data-file 必须是真实文件名，不要用“待上传”等说明文字代替。
使用 project-data JSON 的页面以 JSON 为准，修改可见卡片而不修改 JSON 不会改变读取结果。

## skills 必须调整的生成规则

1. 维护项目音频目录：每条唯一 id、真实 file，可选默认 range。不要生成虚构文件名或“待上传”占位后声称制作包完整。
2. 为每个分镜明确生成 audio_references，包含该镜真正需要的音频，未用音频不加入。无音频写 []。
3. 为该镜创建“资产 ID → Audio N”的局部映射，中文和英文提示词统一使用这个映射。
4. 确保提示词所有 Audio N 都在 1 到列表长度之间。不把 A8 直接写成 Audio 8，除非它确实在该镜列表第 8 位。
5. 多人物时说明每个 Subject 使用哪个 Audio 的音色；音乐/音效与语音分别说明用途。参考音频不保证逐字复用、混音或口型同步。
6. 每个参考片段独立填写 range，不把歌曲的绝对时间范围误套给音色样本。
7. 独立维护分镜 seconds/range，不从任意音色样本长度推导镜头时长。分辨率继续由用户在主节点手动选择。
8. 校验引用文件真实存在、路径唯一、JSON 有效、分镜 ID 唯一、时长一致、所有引用编号有对应条目。
9. JSON 与页面可见的分镜卡片应由同一份数据渲染，防止二者内容不一致。嵌入 JSON 时正确转义 `</script>` 等 HTML 敏感内容。
10. 交付后重新上传/刷新制作包并检测，不能继续用旧的 prepared package ID；它是不可变的旧数据快照。

## 旧格式仍支持

未声明每镜 audio_references 时，保留旧 voice_reference、audio_file、master_audio 的单音频处理。
旧数字人项目只有项目级音频目录而无每镜列表时，仍采用第一个条目的旧默认行为，避免既有制作包改变。
旧 MV 包仅有一个音频文件时仍可自动选择；有多个且未指定时继续提示歧义。
要真正启用多音频，必须生成每镜 audio_references，而不是仅往目录多放文件。
