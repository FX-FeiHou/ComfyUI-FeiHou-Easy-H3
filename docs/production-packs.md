# 制作包分镜加载器（标准版，实验功能）

新增节点：**FeiHou Easy H3 制作包分镜加载器** / **FeiHou Easy H3 Production Pack Loader**。
将它的 `production_shot` 输出连接到主节点 `ComfyUI-FeiHou-Easy-H3` 的同名输入。
不连接时，原来的手动工作流不变；既有主节点的控件保存顺序和输出接口顺序不变。

## 使用

1. 重启 ComfyUI、刷新浏览器。添加加载器并连接主节点的“制作包分镜”输入。旧工作流会在原有接口后补上新输入，不改变旧接线编号。
2. 在制作包路径填写 **运行 ComfyUI 的电脑**上的目录，例如 `F:\Codex\机甲女团2`，点击“载入 / 刷新制作包”；也可以点击“上传 ZIP 压缩包”，ZIP 应包含 HTML、资产图片和歌曲。
3. 默认递归寻找唯一的 `*Shotlist*.html`，自动选择包内唯一的歌曲。多个候选时，在相应栏填写**相对于包根目录**的路径再刷新，不会猜选。
4. 检查状态中的总镜数、分镜编号、歌曲时间范围和引用图编号。选择中文 `zh` 或英文 `en`，更换语言或镜号后可点击“预览当前分镜”。缺少所选语言会报错，不会悄悄替换。
5. 从头生成时将“分镜序号”设为 **1**，“生成后操作”设为 **increment**，ComfyUI 队列次数设为 **36**（或实际镜头数）。加载器每个任务输出一镜，最终停止在第 36 镜；再提交第 37 镜会明确报错，不循环。
6. 原有模型加载、LoRA、采样器和保存视频节点继续使用。为了把 H3 向上补齐的帧数裁回歌曲片段时长，请保留/连接既有的“数字人/MV 时长裁剪”节点：Easy H3 输出节点的时长控制输出连接裁剪节点，解码结果经裁剪后再保存。

**编号在提交任务后递增，不是在成功后递增。** 如果第 12 镜失败，修复原因后把序号设回 12，单镜重跑时可用 `fixed`；继续批量时改回 `increment`，队列次数填写剩余镜数。加载器不额外向服务器提交任务，不更改采样器的种子递增规则。

## 应用规则

- 以 HTML **最终渲染后的分镜卡片**为准，不读取历史脚本中已失效的数组。这份示例的旧数据为 35 镜，最终页为 36 镜。
- 支持示例页的 `article.unit`、`.time-pill`、`.duration-pill`、`.ref-chip`、双语 `.prompt-pane pre` 结构。其他任意 HTML 排版不保证支持；无法识别会报错。
- 按卡片引用编号的顺序，把 P 编号映射到本地同名图片，成为 `<Picture 1>`、`<Picture 2>` 等局部编号。大小写不敏感，`P1B` 可匹配 `P1b.png`；同编号多个文件会报错，不会混用 P1/P1b。
- 清空当前任务的手动/上一镜媒体选择，**仅复制和加载当前镜引用的图片**以及歌曲。资产清单扫描不等于把所有图片解码到内存。每镜最多 9 图，超限报错，不能静默截掉多余图片。
- 歌曲为 `Audio 1`，使用分镜时间范围裁剪；时间取 100 ms 精度四舍五入。镜头声明时长与歌曲范围不一致会报错；范围超过歌曲实际末尾仍按原有音频裁剪规则截到实际末尾。
- 自动启用参考生视频和“数字人/MV 自动时长”，关闭提示词优化，保留制作包的成稿提示词。参考图和音频会回显到主节点对应槽位，提示词和时长也会更新；后台始终使用当前排队任务的输入，不依赖界面回显时序。
- 读取卡片标明的宽高比、分辨率和 FPS；没有写的保留主节点原值，不自行猜测。模型、LoRA、采样步数、种子和二采开关不由制作包改动。
- 当前导入器针对**参考图片 + 一首主歌曲**的制作包，不支持引用视频或多音轨分镜；会拒绝出现 `<Video N>` / `<Audio 2>` 等引用的提示词。

## 保存、缓存与安全

- 保存工作流时会保存路径、镜号、生成后操作和已准备制作包 ID。重启可继续读取本机缓存；换电脑、清缓存或修改 HTML 后需要重新“载入 / 刷新”，不能只复制一个缓存 ID。
- 导入信息/解压包保存在 `ComfyUI/user/feihou_h3_production_packs`；当前镜媒体副本保存在 `ComfyUI/input/feihou_h3_pack_media`。不改写原始制作包。成功导入的缓存不会自动删除，以免已排队任务引用失效；不用相关工作流且队列停止后可自行清理这两个专用目录，随后重新导入。
- 文件夹导入路由仅允许本机浏览器访问，并要求同源请求。ZIP 只提取图片、音频和 HTML，拒绝路径越界、符号链接和加密包；限 5000 个文件、4 GiB 解压体积、16 MiB HTML。
- HTML 不在 Python 服务端执行；动态页面在无同源权限、禁止外部资源的隔离 iframe 中渲染，读取完即移除。只导入可信来源的制作包，恶意/极复杂脚本仍可能卡住浏览器，超过 10 秒不返回则报错。
- 目前只在标准版增加，RH 版未改动；本功能尚未进行完整 GPU 视频生成验收。

## English quick start

Connect **FeiHou Easy H3 Production Pack Loader → production_shot** on the main node. Enter a local folder/ZIP path or upload a ZIP, then click **Load / refresh package**. Choose a prompt language and preview the current shot. Set **shot_index = 1**, **control_after_generate = increment**, and queue the reported shot count (36 for the sample). The index advances on submission, including failed tasks; select an explicit index and `fixed` when retrying.

The importer supports the sample's rendered Shotlist cards (not arbitrary HTML), resolves ordered asset IDs case-insensitively, loads only referenced images plus Audio 1, and applies the shot's audio range. It enables reference-video/automatic audio duration and disables prompt rewriting. Missing FPS/resolution preserve the main node's settings. Keep the existing duration-crop node before saving to remove H3 frame padding. Original package files are not modified. Local preparation is required again after moving machines, changing the HTML or deleting the cache. RH is not modified.
