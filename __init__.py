from .nodes import (
    FeiHouEasyH3,
    FeiHouEasyH3Loader,
    FeiHouEasyH3RemixLoader,
    FeiHouEasyH3ModelAdapter,
    FeiHouEasyH3LoraStack,
    FeiHouEasyH3Output,
    FeiHouEasyH3DurationCrop,
    FeiHouEasyH3PromptPreview,
)

NODE_CLASS_MAPPINGS = {
    "FeiHouEasyH3LoraStack": FeiHouEasyH3LoraStack,
    "FeiHouEasyH3Loader": FeiHouEasyH3Loader,
    "FeiHouEasyH3RemixLoader": FeiHouEasyH3RemixLoader,
    "FeiHouEasyH3ModelAdapter": FeiHouEasyH3ModelAdapter,
    "FeiHouEasyH3": FeiHouEasyH3,
    "FeiHouEasyH3Output": FeiHouEasyH3Output,
    "FeiHouEasyH3DurationCrop": FeiHouEasyH3DurationCrop,
    "FeiHouEasyH3PromptPreview": FeiHouEasyH3PromptPreview,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "FeiHouEasyH3LoraStack": "Load LoRA (Bypass, Model Only) (Debug)",
    "FeiHouEasyH3Loader": "FeiHou Easy H3 Loader",
    "FeiHouEasyH3RemixLoader": "FeiHou Easy H3 Remix Loader",
    "FeiHouEasyH3ModelAdapter": "FeiHou Easy H3 Model Adapter",
    "FeiHouEasyH3": "ComfyUI-FeiHou-Easy-H3",
    "FeiHouEasyH3Output": "FeiHou Easy H3 Output",
    "FeiHouEasyH3DurationCrop": "FeiHou Easy H3 Digital Human/MV Duration Crop",
    "FeiHouEasyH3PromptPreview": "FeiHou Easy H3 Prompt Preview",
}

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
