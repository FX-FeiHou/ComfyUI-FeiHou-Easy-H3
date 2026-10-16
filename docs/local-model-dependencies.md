# 本地提示词模型依赖安装

标准版 v1.4.21 增加依赖检查、设置页安装入口和随包安装脚本。仅复制或解压文件不会执行 Python/pip；解压完成后仍可直接在 ComfyUI 内安装。API 提示词服务无需 GGUF 后端。

## Manager 安装

支持执行自定义安装脚本的 ComfyUI-Manager 会处理 `requirements.txt` 并执行 `install.py`。脚本使用 Manager 所运行的 Python 检查 Transformers 依赖，并按 Python、操作系统、PyTorch CUDA 版本选择 GGUF 预编译包。用户禁用安装脚本或安装失败时，可按下面的手动方式继续。安装失败不会阻止 API 模式加载节点。

## 手动下载 ZIP / 解压安装

1. 将节点包解压到 `ComfyUI/custom_nodes/ComfyUI-FeiHou-Easy-H3`，重启 ComfyUI。
2. 打开设置 → 🐵Easy H3 → 本地模型，选择模型和运行设备。页面自动检查依赖，也可以点“检查依赖”。
3. 点“安装/修复依赖”。等待安装和检查结束，按页面提示重启 ComfyUI，再刷新浏览器。

安装进度及失败原因显示在设置页，同时写入 ComfyUI 控制台。不在节点启动时静默联网安装。安装前需清空 ComfyUI 队列；安装期间不能运行本地提示词优化。

Windows 用户也可在节点目录双击 `install-local-llm.bat`。脚本查找常见整合包/portable/venv 的 Python；若找到多个或找不到，会要求输入**启动 ComfyUI 的 Python 路径**，不会默认安装到系统 Python。

Linux、macOS 或特殊目录结构，可直接用启动 ComfyUI 的 Python 执行：

```text
<ComfyUI 的 Python> install.py --profile all
<ComfyUI 的 Python> install.py --profile gguf
<ComfyUI 的 Python> install.py --profile transformers
```

`--device cpu` 选择 CPU 模式，`--device cuda` 明确要求 CUDA，默认 `auto`。只检查用 `--check`；查看安装计划而不改环境用 `--dry-run`。

## 安装范围与兼容性

- 安装器只下载 Python 二进制 wheel，不自动编译 llama.cpp，不下载模型。
- CUDA 使用固定版本的 [JamePeng llama-cpp-python](https://github.com/JamePeng/llama-cpp-python/releases)，支持 Python 3.10–3.14、Windows/Linux x64，以及 CUDA 12.4、12.6、12.8、13.0、13.1 的对应构建。Apple Silicon 使用该项目 Metal 构建；纯 CPU 环境查找 [abetlen 官方 CPU 包](https://abetlen.github.io/llama-cpp-python/whl/cpu)。其他组合会明确提示手动安装，不把需要 GPU 的配置静默换成 CPU 包。
- 保留已有 Torch、TorchVision、TorchAudio 和 NumPy。第三方后端的 NumPy 声明不匹配会显示警告；不会为了安装 GGUF 自动改动 ComfyUI 的 NumPy。
- 一般 Transformers 模型使用 4.51+；DeepSeek-V4 还需要真正包含其实现的 Transformers 5.x。扫描、依赖就绪与该模型能成功推理是不同检查，模型架构、tokenizer、mmproj 和显存仍需匹配。
- Windows/Linux CUDA、CPU 和 macOS 的包选择逻辑有测试覆盖；只有本机 Windows CUDA 环境进行了实际原生后端检查，不能把其他平台的静态检查当成已实机验证。

## English quick start

Manager runs requirements/install.py when custom install scripts are enabled. A manual ZIP copy does not run an installer: restart ComfyUI, open Easy H3 settings → Local models, then use **Check dependencies** / **Install / repair dependencies**. Alternatively run install-local-llm.bat on Windows, or execute install.py with the Python that starts ComfyUI. Restart after installation. The installer chooses binary wheels for the active environment, preserves Torch/NumPy and reports errors instead of silently using a CPU backend for a GPU configuration.
