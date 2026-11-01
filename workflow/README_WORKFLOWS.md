# FeiHou Easy H3 工作流说明

## 中文说明

使用本文件夹中的工作流前，请先安装所需插件，并下载对应模型。

### 可能需要安装的插件

- `ComfyUI-FeiHou-Easy-H3`（本目录对应的节点包）
- [ComfyUI-KJNodes](https://github.com/kijai/ComfyUI-KJNodes)（请更新到最新版，旧版可能报 `unexpected keyword argument 'attention'`）
- [ComfyUI-VideoHelperSuite](https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite)
- [ComfyUI-FeiHou-Toolbox](https://github.com/FX-FeiHou/ComfyUI-FeiHou-Toolbox)（二段式采样 v8、一键长视频自动替换 v1）
- [rgthree-comfy](https://github.com/rgthree/rgthree-comfy)（LoRA 加载器需要，二段式采样 v8 的分组开关和标题也用它）
- [ComfyUI-MiniMaxH3Mod](https://github.com/Luisacaotica/ComfyUI-MiniMaxH3Mod)（二段式采样 v8 的 RefMod 节点，默认静音，用不到可不装）

也可以在 ComfyUI Manager 中搜索插件名称安装。安装或更新后请重启 ComfyUI。

### 模型与资源

工作流需要的插件、模型和相关资源：

小肥猴的网盘：<https://pan.quark.cn/s/27fe4727197b>

B 站：<https://space.bilibili.com/389291683>

YouTube：<https://www.youtube.com/@ivever5>

不同工作流需要的模型可能不同，请按照工作流中的加载器选择对应文件。如果列表中找不到模型，请检查模型是否放入了正确的 `ComfyUI/models` 子目录，然后刷新或重启 ComfyUI。

部分工作流还需要额外的 LoRA 或其他自定义节点，具体以工作流中的节点为准。

---

# FeiHou Easy H3 Workflow Guide

## English

Before using any workflow in this folder, install the required custom nodes and download the models used by that workflow.

### Required custom nodes

- `ComfyUI-FeiHou-Easy-H3` (this custom-node package)
- [ComfyUI-KJNodes](https://github.com/kijai/ComfyUI-KJNodes) (keep it up to date; old versions can fail with `unexpected keyword argument 'attention'`)
- [ComfyUI-VideoHelperSuite](https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite)
- [ComfyUI-FeiHou-Toolbox](https://github.com/FX-FeiHou/ComfyUI-FeiHou-Toolbox) (Two-Stage Sampling v8, one-click long-video replacement v1)
- [rgthree-comfy](https://github.com/rgthree/rgthree-comfy) (required by the LoRA loader; Two-Stage Sampling v8 also uses its group toggles and labels)
- [ComfyUI-MiniMaxH3Mod](https://github.com/Luisacaotica/ComfyUI-MiniMaxH3Mod) (the RefMod node in Two-Stage Sampling v8; muted by default and optional)

You can also install them by searching for their names in ComfyUI Manager. Restart ComfyUI after installing or updating custom nodes.

### Models and assets

The plugins, models, and related assets used by the workflows are available here:

Xiao Fei Hou's cloud drive: <https://pan.quark.cn/s/27fe4727197b>

Bilibili: <https://space.bilibili.com/389291683>

YouTube: <https://www.youtube.com/@ivever5>

Model requirements may differ between workflows. Select the matching files in each workflow's loader node. If a model is not listed, place it in the correct `ComfyUI/models` subdirectory, then refresh or restart ComfyUI.

Some workflows may also require additional LoRAs or custom nodes. Please check the nodes included in the workflow.
