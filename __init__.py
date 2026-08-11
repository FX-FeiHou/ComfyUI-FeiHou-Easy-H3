from .nodes import (
    FeiHouEasyH3,
    FeiHouEasyH3Loader,
    FeiHouEasyH3ModelAdapter,
    FeiHouEasyH3LoraStack,
    FeiHouEasyH3Output,
    FeiHouEasyH3PromptPreview,
)

NODE_CLASS_MAPPINGS = {
    "FeiHouEasyH3LoraStack": FeiHouEasyH3LoraStack,
    "FeiHouEasyH3Loader": FeiHouEasyH3Loader,
    "FeiHouEasyH3ModelAdapter": FeiHouEasyH3ModelAdapter,
    "FeiHouEasyH3": FeiHouEasyH3,
    "FeiHouEasyH3Output": FeiHouEasyH3Output,
    "FeiHouEasyH3PromptPreview": FeiHouEasyH3PromptPreview,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "FeiHouEasyH3LoraStack": "加载LoRA（旁路，仅模型）（用于调试）",
    "FeiHouEasyH3Loader": "FeiHou Easy H3 Loader",
    "FeiHouEasyH3ModelAdapter": "FeiHou Easy H3 Model Adapter",
    "FeiHouEasyH3": "ComfyUI-FeiHou-Easy-H3 · modified · H3: nkxx188/ComfyUI-MiniMaxH3-Easy · API: yawiii/ComfyUI-Prompt-Assistant",
    "FeiHouEasyH3Output": "FeiHou Easy H3 Output",
    "FeiHouEasyH3PromptPreview": "FeiHou Easy H3 提示词预览",
}

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
