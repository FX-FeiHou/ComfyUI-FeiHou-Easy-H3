"""Local LLM dependency checks/installers. Never installs during node import."""
from __future__ import annotations

import argparse
import importlib.metadata as metadata
import importlib.util
import json
import logging
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

PROFILES = ("all", "gguf", "transformers")
DEVICES = ("auto", "cuda", "cpu")
BASE = {
    "transformers": "transformers>=4.51.0", "accelerate": "accelerate",
    "safetensors": "safetensors", "sentencepiece": "sentencepiece",
    "PIL": "Pillow", "imageio_ffmpeg": "imageio-ffmpeg>=0.6.0",
}
GGUF_HELPERS = {"numpy": "numpy>=1.21.6", "diskcache": "diskcache>=5.6.2",
                "jinja2": "jinja2>=3.1.0", "typing_extensions": "typing-extensions>=4.8.0", "PIL": "Pillow"}
WHEEL_VERSION = "0.4.2"
WHEEL_DATE = "20261003"
CUDA_BUILDS = {"12.4": "cu124", "12.6": "cu126", "12.8": "cu128", "13.0": "cu130", "13.1": "cu131"}
MARKER = "FEIHOU_DEPENDENCIES_JSON="
_CACHE = {}
_CACHE_LOCK = threading.Lock()
_JOB_LOCK = threading.Lock()
_JOB = {"state": "idle", "message": "", "log": [], "restart_required": False}


def validate(profile, device):
    if profile not in PROFILES or device not in DEVICES:
        raise ValueError("Invalid local dependency profile/device")


def _missing(requirements):
    from packaging.requirements import Requirement
    missing = []
    for module, expression in requirements.items():
        requirement = Requirement(expression)
        try:
            installed = metadata.version(requirement.name)
            found = importlib.util.find_spec(module) is not None
        except (metadata.PackageNotFoundError, ImportError, ValueError):
            installed, found = "", False
        if not found or installed not in requirement.specifier:
            missing.append(expression)
    return missing


def light_status():
    """Metadata only; no native library import or network request."""
    return {"missing_transformers": _missing(BASE),
            "gguf_installed": importlib.util.find_spec("llama_cpp") is not None,
            "python": sys.executable}


def _environment():
    result = {"python": sys.executable, "python_version": platform.python_version(),
              "system": platform.system(), "machine": platform.machine(),
              "cuda_version": "", "gpu_available": False, "cuda_offload": False,
              "torch_available": False, "gguf_version": "", "handlers": [], "errors": [], "warnings": []}
    try:
        import torch
        result["cuda_version"] = torch.version.cuda or ""
        result["gpu_available"] = bool(torch.cuda.is_available())
        result["torch_available"] = True
    except Exception as exc:
        result["errors"].append(f"无法导入 ComfyUI 的 torch，请确认使用启动 ComfyUI 的 Python：{exc}")
    return result


def _worker(profile, device):
    validate(profile, device)
    result = _environment()
    result.update(profile=profile, device=device)
    if profile in ("all", "transformers"):
        result["missing_transformers"] = _missing(BASE)
        if result["missing_transformers"]:
            result["errors"].append("缺少本地 Transformers 依赖：" + ", ".join(result["missing_transformers"]))
        elif not result["errors"]:
            try:
                from transformers import AutoModelForCausalLM, AutoTokenizer
            except Exception as exc:
                result["errors"].append(f"Transformers 无法导入：{exc}")
    if profile in ("all", "gguf"):
        missing = _missing(GGUF_HELPERS)
        if missing:
            result["errors"].append("缺少 GGUF 辅助依赖：" + ", ".join(missing))
        try:
            import ctypes
            import llama_cpp
            from llama_cpp import Llama, llama_cpp as native
            import llama_cpp.llama_chat_format as formats
            native.llama_backend_init()
            loader = getattr(sys.modules.get("llama_cpp.llama"), "ggml_backend_load_all_from_path", None)
            if callable(loader):
                loader(ctypes.c_char_p(str(Path(llama_cpp.__file__).parent / "lib").encode("utf-8")))
            result["gguf_version"] = getattr(llama_cpp, "__version__", "unknown")
            result["cuda_offload"] = bool(native.llama_supports_gpu_offload())
            result["handlers"] = [name for name in ("Qwen35ChatHandler", "Qwen3VLChatHandler", "MTMDChatHandler", "GenericMTMDChatHandler") if hasattr(formats, name)]
            if not result["handlers"]:
                result["errors"].append("GGUF 后端缺少本插件所需的 Qwen/MTMD 接口，请安装兼容版本。")
            if (device == "cuda" or (device == "auto" and result["gpu_available"])) and not result["cuda_offload"]:
                result["errors"].append("当前 GGUF 后端未成功加载 GPU 加速；请安装匹配 CUDA 的运行库。")
            # Preserve ComfyUI's NumPy rather than silently downgrading shared packages.
            from packaging.requirements import Requirement
            for raw in metadata.requires("llama-cpp-python") or []:
                requirement = Requirement(raw)
                if requirement.name.lower() == "numpy" and metadata.version("numpy") not in requirement.specifier:
                    result["warnings"].append(f"当前 NumPy {metadata.version('numpy')} 超出后端声明 {requirement.specifier}；安装器保留现有 NumPy。")
        except Exception as exc:
            result["errors"].append(f"GGUF 运行库无法导入：{exc}")
    if device == "cuda" and not result["gpu_available"]:
        result["errors"].append("当前 PyTorch 无法使用 CUDA；请先修复 ComfyUI 的 CUDA 环境。")
    result["ready"] = not result["errors"]
    return result


def check(profile="all", device="auto", *, refresh=False):
    """Probe in a child Python: failed DLLs cannot crash the ComfyUI server."""
    validate(profile, device)
    key = (profile, device)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
    if cached and not refresh and time.monotonic() - cached[0] < 60:
        return cached[1]
    command = [sys.executable, str(Path(__file__).resolve()), "--probe", "--profile", profile, "--device", device]
    try:
        process = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        lines = [line[len(MARKER):] for line in process.stdout.splitlines() if line.startswith(MARKER)]
        if process.returncode != 0 or not lines:
            detail = (process.stderr or process.stdout)[-1600:]
            raise RuntimeError(f"依赖检查进程退出 ({process.returncode})：{detail}")
        result = json.loads(lines[-1])
    except (OSError, subprocess.TimeoutExpired, ValueError, RuntimeError) as exc:
        # A broken native GGUF DLL can terminate its probe. Recover environment
        # information separately so the installer can still replace that DLL.
        result = {"python": sys.executable, "errors": [], "warnings": [], "handlers": []}
        try:
            environment = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--environment"],
                                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            lines = [line[len(MARKER):] for line in environment.stdout.splitlines() if line.startswith(MARKER)]
            if environment.returncode == 0 and lines:
                result.update(json.loads(lines[-1]))
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass
        result["errors"].append(str(exc))
        result["ready"] = False
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic(), result)
    return result


def wheel_url(environment, device="auto"):
    """Fixed release sources, binary wheels only. Never guesses another CUDA ABI."""
    system, machine = environment["system"], environment["machine"].lower()
    python = environment["python_version"].split(".")[:2]
    if python[0] != "3" or python[1] not in ("10", "11", "12", "13", "14"):
        raise RuntimeError("GGUF 自动安装支持 Python 3.10–3.14，请使用兼容的 ComfyUI Python。")
    cp = "cp" + "".join(python)
    cuda = environment.get("cuda_version", "")
    wants_cuda = device == "cuda" or (device == "auto" and environment.get("gpu_available"))
    if wants_cuda or (device == "cpu" and cuda in CUDA_BUILDS):
        build = CUDA_BUILDS.get(cuda)
        if not build:
            raise RuntimeError(f"暂未提供 CUDA {cuda or '未知'} 的匹配预编译包，请参考本地模型依赖说明手动安装；不会回退到慢速 CPU 包。")
        if machine not in ("amd64", "x86_64") or system not in ("Windows", "Linux"):
            raise RuntimeError("此 CUDA 预编译包支持 Windows/Linux x64，请按文档手动安装其他平台后端。")
        os_tag = "win" if system == "Windows" else "linux"
        platform_tag = "win_amd64" if system == "Windows" else "linux_x86_64"
        tag = f"v{WHEEL_VERSION}-{build}-{os_tag}-{WHEEL_DATE}"
        name = f"llama_cpp_python-{WHEEL_VERSION}+{build}-{cp}-{cp}-{platform_tag}.whl"
        return f"https://github.com/JamePeng/llama-cpp-python/releases/download/{tag}/{name}"
    if system == "Darwin" and machine in ("arm64", "aarch64"):
        tag = f"v{WHEEL_VERSION}-Metal-macos-{WHEEL_DATE}"
        name = f"llama_cpp_python-{WHEEL_VERSION}-{cp}-{cp}-macosx_11_0_arm64.whl"
        return f"https://github.com/JamePeng/llama-cpp-python/releases/download/{tag}/{name}"
    from packaging.tags import sys_tags
    from packaging.utils import parse_wheel_filename
    valid = set(sys_tags())
    index = "https://abetlen.github.io/llama-cpp-python/whl/cpu/llama-cpp-python/"
    with urllib.request.urlopen(index, timeout=30) as response:
        html = response.read(4 * 1024 * 1024).decode("utf-8")
    for link in re.findall(r'href=[\"\']([^\"\']+)', html):
        url = urllib.parse.urljoin(index, link)
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != "github.com" or not parsed.path.startswith("/abetlen/llama-cpp-python/releases/download/"):
            continue
        name = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1])
        try:
            _, version, _, tags = parse_wheel_filename(name)
        except ValueError:
            continue
        if str(version) == "0.3.36" and valid.intersection(tags):
            return url
    raise RuntimeError("没有适配当前 Python/平台的 GGUF CPU 包，请按文档手动安装。")


def install(profile="all", device="auto", *, emit=print, dry_run=False):
    validate(profile, device)
    environment = check(profile, device, refresh=True)
    if not environment.get("system"):
        raise RuntimeError("无法检查 Python 环境：" + "; ".join(environment["errors"]))
    if not environment.get("torch_available"):
        raise RuntimeError("当前 Python 无法加载 ComfyUI 的 torch；请使用正确的 ComfyUI Python 并先修复其运行环境。")
    requirements = {}
    if profile in ("all", "transformers"):
        requirements.update(BASE)
    if profile in ("all", "gguf"):
        requirements.update(GGUF_HELPERS)
    missing = _missing(requirements)
    commands = []
    # Keep existing Torch/NumPy versions and use wheel packages only.
    if missing:
        commands.append([sys.executable, "-m", "pip", "install", "--only-binary=:all:", *missing])
    if profile in ("all", "gguf") and (not environment.get("gguf_version") or not environment.get("handlers") or
            ((device == "cuda" or (device == "auto" and environment.get("gpu_available"))) and not environment.get("cuda_offload"))):
        commands.append([sys.executable, "-m", "pip", "install", "--no-deps", "--force-reinstall", wheel_url(environment, device)])
    if dry_run:
        return {"python": sys.executable, "commands": commands, "status": environment}
    with tempfile.TemporaryDirectory(prefix="feihou-h3-dependencies-") as temp:
        constraints = Path(temp) / "preserve-comfy.txt"
        pins = []
        for package in ("torch", "torchvision", "torchaudio", "numpy"):
            try:
                pins.append(f"{package}=={metadata.version(package)}")
            except metadata.PackageNotFoundError:
                pass
        constraints.write_text("\n".join(pins), encoding="utf-8")
        for command in commands:
            emit("安装依赖：" + subprocess.list2cmdline(command))
            env = {**os.environ, "PIP_CONSTRAINT": str(constraints), "PYTHONIOENCODING": "utf-8", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                       encoding="utf-8", errors="replace", env=env,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            # Bound network and package-install stalls; stdout is drained on a reader thread.
            def read_output():
                for line in process.stdout:
                    emit(line.rstrip())
            reader = threading.Thread(target=read_output, daemon=True)
            reader.start()
            try:
                code = process.wait(timeout=1800)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise RuntimeError("依赖安装超时，请检查网络后重试，或使用随包脚本安装。")
            finally:
                reader.join(timeout=5)
            if code:
                raise RuntimeError(f"依赖安装失败 (pip 返回 {code})，请检查上面的日志后重试。")
    result = check(profile, device, refresh=True)
    if not result["ready"]:
        raise RuntimeError("安装后的检查未通过：" + "; ".join(result["errors"]))
    result["restart_required"] = bool(commands)
    emit("依赖检查通过。" + ("请重启 ComfyUI 并刷新页面。" if commands else "无需重复安装。"))
    return result


def job_status():
    with _JOB_LOCK:
        return json.loads(json.dumps(_JOB))


def start_install(profile, device, *, release=None):
    validate(profile, device)
    with _JOB_LOCK:
        if _JOB["state"] == "running":
            raise RuntimeError("依赖正在安装，请等待完成。")
        _JOB.update(state="running", message="正在检查当前 ComfyUI Python…", log=[], restart_required=False)
    def emit(line):
        logging.info("[Easy H3 dependencies] %s", line)
        with _JOB_LOCK:
            _JOB["message"] = line
            _JOB["log"] = [*_JOB["log"], line][-40:]
    def run():
        try:
            status = install(profile, device, emit=emit)
            with _JOB_LOCK:
                _JOB.update(state="success", status=status, restart_required=status["restart_required"])
        except Exception as exc:
            emit(str(exc))
            with _JOB_LOCK:
                _JOB.update(state="error", restart_required=True)
        finally:
            if release is not None:
                release()
    try:
        threading.Thread(target=run, name="easy-h3-dependency-install", daemon=True).start()
    except Exception as exc:
        with _JOB_LOCK:
            _JOB.update(state="error", message=str(exc))
        raise RuntimeError("无法启动依赖安装任务，请稍后重试。") from exc
    return job_status()


def main():
    parser = argparse.ArgumentParser(description="Install Easy H3 local-model dependencies using this Python.")
    parser.add_argument("--profile", choices=PROFILES, default="all")
    parser.add_argument("--device", choices=DEVICES, default="auto")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--environment", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.environment:
        print(MARKER + json.dumps(_environment(), ensure_ascii=True), flush=True)
        return 0
    if args.probe:
        print(MARKER + json.dumps(_worker(args.profile, args.device), ensure_ascii=True), flush=True)
        return 0
    if args.check:
        result = check(args.profile, args.device, refresh=True)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ready"] else 1
    try:
        result = install(args.profile, args.device, dry_run=args.dry_run)
        if args.dry_run:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"[Easy H3 dependencies] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
