from .nodes import (
    FeiHouEasyH3Resolution,
    FeiHouEasyH3,
    FeiHouEasyH3Loader,
    FeiHouEasyH3RemixLoader,
    FeiHouEasyH3ModelAdapter,
    FeiHouEasyH3LoraStack,
    FeiHouEasyH3Output,
    FeiHouEasyH3DurationCrop,
    FeiHouEasyH3PromptPreview,
)

from .production_pack import FeiHouEasyH3ProductionPackLoader
from .production_continuation import FeiHouEasyH3ContinueOut
from .face_refine import FeiHouEasyH3FaceRefine
from .split_nodes import FeiHouEasyH3Media, FeiHouEasyH3Setup
from .dual_sample import FeiHouEasyH3SampleEnhancer1st, FeiHouEasyH3SampleEnhancer2nd

NODE_CLASS_MAPPINGS = {
    "FeiHouEasyH3FaceRefine": FeiHouEasyH3FaceRefine,
    "FeiHouEasyH3Resolution": FeiHouEasyH3Resolution,
    "FeiHouEasyH3ProductionPackLoader": FeiHouEasyH3ProductionPackLoader,
    "FeiHouEasyH3LoraStack": FeiHouEasyH3LoraStack,
    "FeiHouEasyH3Loader": FeiHouEasyH3Loader,
    "FeiHouEasyH3RemixLoader": FeiHouEasyH3RemixLoader,
    "FeiHouEasyH3ModelAdapter": FeiHouEasyH3ModelAdapter,
    "FeiHouEasyH3": FeiHouEasyH3,
    "FeiHouEasyH3Media": FeiHouEasyH3Media,
    "FeiHouEasyH3Setup": FeiHouEasyH3Setup,
    "FeiHouEasyH3ContinueOut": FeiHouEasyH3ContinueOut,
    "FeiHouEasyH3Output": FeiHouEasyH3Output,
    "FeiHouEasyH3DurationCrop": FeiHouEasyH3DurationCrop,
    "FeiHouEasyH3PromptPreview": FeiHouEasyH3PromptPreview,
    "FeiHouEasyH3SampleEnhancer1st": FeiHouEasyH3SampleEnhancer1st,
    "FeiHouEasyH3SampleEnhancer2nd": FeiHouEasyH3SampleEnhancer2nd,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "FeiHouEasyH3FaceRefine": "FeiHou Easy H3 Face Refine (Experimental)",
    "FeiHouEasyH3Resolution": "FeiHou Easy H3 Resolution",
    "FeiHouEasyH3ProductionPackLoader": "FeiHou Easy H3 Production Pack Loader",
    "FeiHouEasyH3LoraStack": "Load LoRA (Model Only)",
    "FeiHouEasyH3Loader": "FeiHou Easy H3 Loader",
    "FeiHouEasyH3RemixLoader": "FeiHou Easy H3 Remix Loader",
    "FeiHouEasyH3ModelAdapter": "FeiHou Easy H3 Model Adapter",
    "FeiHouEasyH3": "ComfyUI-FeiHou-Easy-H3",
    "FeiHouEasyH3Media": "FeiHou Easy H3 Media",
    "FeiHouEasyH3Setup": "FeiHou Easy H3 Setup",
    "FeiHouEasyH3ContinueOut": "FeiHou Easy H3 接续合并",
    "FeiHouEasyH3Output": "FeiHou Easy H3 Output",
    "FeiHouEasyH3DurationCrop": "FeiHou Easy H3 Digital Human/MV Duration Crop",
    "FeiHouEasyH3PromptPreview": "FeiHou Easy H3 Prompt Preview",
    "FeiHouEasyH3SampleEnhancer1st": "FeiHou Easy H3 1st Sample Enhancer（一采）",
    "FeiHouEasyH3SampleEnhancer2nd": "FeiHou Easy H3 2nd Sample Enhancer（二采）",
}

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

# Manual ZIP installs do not execute install.py. Surface optional dependency
# gaps without downloading packages or slowing ComfyUI startup with DLL probes.
from . import local_dependencies
import logging

_dependency_summary = local_dependencies.light_status()
if not _dependency_summary["gguf_installed"] or _dependency_summary["missing_transformers"]:
    logging.info("[Easy H3] 本地模型依赖未完全安装。请到设置→Easy H3→本地模型→安装/修复依赖，"
                 "或运行节点目录内 install-local-llm.bat。API 提示词模式不受影响。")
