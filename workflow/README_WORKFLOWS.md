# FeiHou Easy H3 工作流说明

## 中文说明

使用本文件夹中的工作流前，请先安装所需插件，并下载对应模型。

### 可能需要安装的插件

- `ComfyUI-FeiHou-Easy-H3`（本目录对应的节点包）
- [ComfyUI-KJNodes](https://github.com/kijai/ComfyUI-KJNodes)（请更新到最新版，旧版可能报 `unexpected keyword argument 'attention'`）
- [ComfyUI-VideoHelperSuite](https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite)
- [ComfyUI-FeiHou-Toolbox](https://github.com/FX-FeiHou/ComfyUI-FeiHou-Toolbox)（二段式采样 v8、一键长视频工作流 v1–v3）
- [rgthree-comfy](https://github.com/rgthree/rgthree-comfy)（二段式采样 v8 的分组开关和标题）
- [ComfyUI-MiniMaxH3Mod](https://github.com/Luisacaotica/ComfyUI-MiniMaxH3Mod)（二段式采样 v8 的 RefMod 节点，默认静音，用不到可不装）

也可以在 ComfyUI Manager 中搜索插件名称安装。安装或更新后请重启 ComfyUI。

### 模型与资源

工作流需要的插件、模型和相关资源：

小肥猴的网盘：<https://pan.quark.cn/s/27fe4727197b>

B 站：<https://space.bilibili.com/389291683>

YouTube：<https://www.youtube.com/@ivever5>

不同工作流需要的模型可能不同，请按照工作流中的加载器选择对应文件。如果列表中找不到模型，请检查模型是否放入了正确的 `ComfyUI/models` 子目录，然后刷新或重启 ComfyUI。

部分工作流还需要额外的 LoRA 或其他自定义节点，具体以工作流中的节点为准。

### 英文版工作流

- `【MiniMax-H3 Remix】FeiHou Easy H3 Two-Stage Sampling v8 (English).json`：二段式采样 v8 英文版，标题、分组、备注和使用说明都是英文。
- `【MiniMax-H3 Remix】FeiHou Easy H3 One-Click Long-Video Auto Replacement v2 (English).json`：一键长视频自动替换 v2 英文版。
- `【MiniMax-H3 Remix】FeiHou Easy H3 Character Replacement Long-Video One-Click Automation v3 (English).json`：人物替换长视频一键自动化 v3 英文版（Remake 上下文窗口）。

节点下拉选项（如「顺序生成」「自动」「上下文窗口」）是节点本身的取值，保持中文；英文说明里都附了英文解释。长视频工作流的分镜 md 可以用仓库 `skills` 目录里的 `feihou-h3-motion-ultra` 技能让 AI 来写。

### 联系方式

- 微信：fx-feihou
- LINE：fx-feihou
- Q 群：1091593367
- Civitai：<https://civitai.com/models/2879272/minimax-h3-remix>

加好友请备注来意（加群、商务）。

---

# FeiHou Easy H3 Workflow Guide

## English

Before using any workflow in this folder, install the required custom nodes and download the models used by that workflow.

### Required custom nodes

- `ComfyUI-FeiHou-Easy-H3` (this custom-node package)
- [ComfyUI-KJNodes](https://github.com/kijai/ComfyUI-KJNodes) (keep it up to date; old versions can fail with `unexpected keyword argument 'attention'`)
- [ComfyUI-VideoHelperSuite](https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite)
- [ComfyUI-FeiHou-Toolbox](https://github.com/FX-FeiHou/ComfyUI-FeiHou-Toolbox) (Two-Stage Sampling v8, the one-click long-video workflows v1–v3)
- [rgthree-comfy](https://github.com/rgthree/rgthree-comfy) (group toggles and labels in Two-Stage Sampling v8)
- [ComfyUI-MiniMaxH3Mod](https://github.com/Luisacaotica/ComfyUI-MiniMaxH3Mod) (the RefMod node in Two-Stage Sampling v8; muted by default and optional)

You can also install them by searching for their names in ComfyUI Manager. Restart ComfyUI after installing or updating custom nodes.

### Models and assets

The plugins, models, and related assets used by the workflows are available here:

Xiao Fei Hou's cloud drive: <https://pan.quark.cn/s/27fe4727197b>

Bilibili: <https://space.bilibili.com/389291683>

YouTube: <https://www.youtube.com/@ivever5>

Model requirements may differ between workflows. Select the matching files in each workflow's loader node. If a model is not listed, place it in the correct `ComfyUI/models` subdirectory, then refresh or restart ComfyUI.

Some workflows may also require additional LoRAs or custom nodes. Please check the nodes included in the workflow.

### English workflows

- `【MiniMax-H3 Remix】FeiHou Easy H3 Two-Stage Sampling v8 (English).json`: two-stage sampling (1st pass for motion and composition, 2nd pass latent upscale and refine) for image-to-video, text-to-video, first/last frame, digital humans, character replacement and video editing. Optional groups: shot md / production pack, RefMods, Face Refine, segment-by-segment continuation.
- `【MiniMax-H3 Remix】FeiHou Easy H3 One-Click Long-Video Auto Replacement v2 (English).json`: replaces the person in a long video block by block from a shot md and joins the result into one video, keeping the original motion, camera, background and sound.
- `【MiniMax-H3 Remix】FeiHou Easy H3 Character Replacement Long-Video One-Click Automation v3 (English).json`: the v3 long-video workflow with Remake swap and context windows, so one-take or few-cut videos keep their quality however long they are. It also does motion transfer.

Each workflow has its own **How to Use** note on the canvas. Drop-down values such as 顺序生成, 自动 or 上下文窗口 are the nodes' own option values and stay in Chinese; the notes explain each one in English.

The shot md for the long-video workflows is easiest to get from an AI agent (Claude Code, Codex…) with the `feihou-h3-motion-ultra` skill from the `skills` folder of this repo.

### Contact

- WeChat: fx-feihou
- LINE: fx-feihou
- QQ group: 1091593367
- Bilibili: <https://space.bilibili.com/389291683>
- YouTube: <https://www.youtube.com/@ivever5>
- Civitai: <https://civitai.com/models/2879272/minimax-h3-remix>
- Cloud drive: <https://pan.quark.cn/s/27fe4727197b>

When you add me on WeChat or LINE, please say why (to join the group, or business).
