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
from .face_refine import FeiHouEasyH3FaceRefine
from .split_nodes import FeiHouEasyH3Media, FeiHouEasyH3Setup
from .dual_sample import FeiHouEasyH3DualSample1st, FeiHouEasyH3DualSample2nd, FeiHouEasyH3SampleEnhancer1st, FeiHouEasyH3SampleEnhancer2nd

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
    "FeiHouEasyH3Output": FeiHouEasyH3Output,
    "FeiHouEasyH3DurationCrop": FeiHouEasyH3DurationCrop,
    "FeiHouEasyH3PromptPreview": FeiHouEasyH3PromptPreview,
    "FeiHouEasyH3DualSample1st": FeiHouEasyH3DualSample1st,
    "FeiHouEasyH3DualSample2nd": FeiHouEasyH3DualSample2nd,
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
    "FeiHouEasyH3Output": "FeiHou Easy H3 Output",
    "FeiHouEasyH3DurationCrop": "FeiHou Easy H3 Digital Human/MV Duration Crop",
    "FeiHouEasyH3PromptPreview": "FeiHou Easy H3 Prompt Preview",
    "FeiHouEasyH3DualSample1st": "FeiHou Easy H3 Dual Sample 1st（一采）",
    "FeiHouEasyH3DualSample2nd": "FeiHou Easy H3 Dual Sample 2nd（二采）",
    "FeiHouEasyH3SampleEnhancer1st": "FeiHou Easy H3 1st Sample Enhancer（一采）",
    "FeiHouEasyH3SampleEnhancer2nd": "FeiHou Easy H3 2nd Sample Enhancer（二采）",
}

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
