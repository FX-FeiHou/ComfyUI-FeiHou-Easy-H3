"""A compact MiniMax H3 entry point for ComfyUI.

The node intentionally keeps the graph contract small: one loader bundle, one
mode-aware conditioning node, and standard ComfyUI outputs for the sampler
chain. Reference files are selected, uploaded, and previewed inside the main
node; the browser extension sends their input-folder paths to this backend.
"""

from __future__ import annotations

import io
import logging
import math
import os
import re
import sys
import threading
import uuid
import base64
import asyncio
import json
import mimetypes
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import torch
import torchaudio

import comfy.model_management
import comfy.sd
import comfy.utils
import folder_paths
import node_helpers
import nodes
from comfy_extras import nodes_minimax_h3 as h3
from comfy_extras import nodes_audio as comfy_audio_nodes
from comfy_api.latest import InputImpl


MODE_IMAGE = "image"
MODE_REFERENCE = "reference"
KEYFRAME_FIRST = "first"
KEYFRAME_LAST = "last"
REFERENCE_SHORT_EDGES = ("480", "544", "640", "736", "768", "832", "928", "1024", "1088")
REF_IMAGE_DEFAULT = "480"
REFERENCE_MENTION_FILENAME = "filename"
REFERENCE_MENTION_INDEX = "index"
NONE_MODEL = "none"
NONE_MODEL_DISPLAY_VALUES = (NONE_MODEL, "None", "无")
NONE_MODEL_ALIASES = {value.lower() for value in NONE_MODEL_DISPLAY_VALUES}
RESOLUTION_360 = "360P"
RESOLUTION_416 = "416P"
RESOLUTION_480 = "480P"
RESOLUTION_540 = "540P"
RESOLUTION_640 = "640P"
RESOLUTION_720 = "720P"
RESOLUTION_768 = "768P"
RESOLUTION_832 = "832P"
RESOLUTION_928 = "928P"
RESOLUTION_1024 = "1024P"
RESOLUTION_1080 = "1080P"
RESOLUTION_CUSTOM = "custom"
ASPECT_SQUARE = "1:1"
ASPECT_PHOTO_PORTRAIT = "2:3"
ASPECT_PHOTO = "3:2"
ASPECT_STANDARD_PORTRAIT = "3:4"
ASPECT_STANDARD = "4:3"
ASPECT_WIDESCREEN_PORTRAIT = "9:16"
ASPECT_WIDESCREEN = "16:9"
ASPECT_ULTRAWIDE = "21:9"
RESOLUTION_MEGAPIXELS = {
    RESOLUTION_360: 0.2,
    RESOLUTION_416: 0.3,
    RESOLUTION_480: 0.4,
    RESOLUTION_540: 0.5,
    RESOLUTION_640: 0.7,
    RESOLUTION_720: 0.9,
    RESOLUTION_768: 1.0,
    RESOLUTION_832: 1.2,
    RESOLUTION_928: 1.5,
    RESOLUTION_1024: 1.8,
    RESOLUTION_1080: 2.0,
}
RESOLUTIONS = (*RESOLUTION_MEGAPIXELS, RESOLUTION_CUSTOM)
REFERENCE_SIZE_SEARCH_RADIUS = 16
ASPECT_RATIOS = {
    ASPECT_SQUARE: (1, 1),
    ASPECT_PHOTO_PORTRAIT: (2, 3),
    ASPECT_PHOTO: (3, 2),
    ASPECT_STANDARD_PORTRAIT: (3, 4),
    ASPECT_STANDARD: (4, 3),
    ASPECT_WIDESCREEN_PORTRAIT: (9, 16),
    ASPECT_WIDESCREEN: (16, 9),
    ASPECT_ULTRAWIDE: (21, 9),
}
MAX_MEDIA = 15
MAX_IMAGES = 9
MAX_VIDEOS = 3
MAX_AUDIOS = 3
LORA_STACK_TYPE = "FEIHOU_MERGE_LORA_STACK"
MIN_SECONDS = 0.2
MAX_SECONDS = 30.0
PROMPT_GUIDES_DIR = os.path.join(os.path.dirname(__file__), "prompt_guides")
PROMPT_GUIDE_MANIFEST = os.path.join(PROMPT_GUIDES_DIR, "manifest.json")
PROMPT_OPTIMIZER_TIMEOUT_SECONDS = 600
PROMPT_OPTIMIZER_MAX_OUTPUT_TOKENS = 50000
PROMPT_OPTIMIZER_MODEL_LIST_TIMEOUT_SECONDS = 20
PROMPT_OPTIMIZER_JPEG_QUALITY = 85
PROMPT_OPTIMIZER_VIDEO_SAMPLE_COUNT = 3
PROMPT_OPTIMIZER_CONFIG_VERSION = 4
PROMPT_OPTIMIZER_ZHIPU_MODELS = (
    "glm-5.1", "glm-5", "glm-5-turbo", "glm-5v-turbo", "glm-4.7", "glm-4.7-flash",
    "glm-4.7-flashx", "glm-4.6", "glm-4.6v", "glm-4.6v-flash", "glm-4.5",
    "glm-4.5-flash", "glm-4.5-air", "glm-4.5-airx", "glm-4.5v", "glm-4-plus",
    "glm-4-flash", "glm-4-flash-250414", "glm-4-air", "glm-4-air-250414",
    "glm-z1-flash", "glm-4v-plus", "glm-4v-flash", "glm-4v", "glm-ocr",
    "glm-4-long", "glm-4-longwriter", "glm-zero-preview", "glm-4.1v-thinking-flash",
)
PROMPT_OPTIMIZER_CONFIG_DEFAULTS = {
    "version": PROMPT_OPTIMIZER_CONFIG_VERSION,
    "active_provider": "zhipu",
    "providers": [
        {
            "id": "zhipu",
            "name": "智谱",
            "api_format": "openai",
            "api_url": "https://open.bigmodel.cn/api/paas/v4",
            "api_key": "",
            "llm_models": [],
            "vlm_models": [],
            "llm_model": "",
            "vlm_model": "",
            "description": "免费 GLM 系列模型",
            "temperature": 0.7,
            "max_tokens": 4096,
            "top_p": 0.9,
            "builtin": True,
        },
        {
            "id": "xflow",
            "name": "xFlow-API聚合",
            "api_format": "openai",
            "api_url": "https://api.xflow.cc/v1",
            "api_key": "",
            "llm_models": [],
            "vlm_models": [],
            "llm_model": "",
            "vlm_model": "",
            "description": "Gemini、Grok、ChatGPT 等 API 聚合",
            "temperature": 0.7,
            "max_tokens": 4096,
            "top_p": 0.9,
            "builtin": True,
        },
        {
            "id": "ollama",
            "name": "Ollama",
            "api_format": "ollama",
            "api_url": "http://localhost:11434/",
            "api_key": "",
            "llm_models": [],
            "vlm_models": [],
            "llm_model": "",
            "vlm_model": "",
            "description": "调用本地模型",
            "temperature": 0.7,
            "max_tokens": 4096,
            "top_p": 0.9,
            "builtin": True,
        },
        {
            "id": "aliyun",
            "name": "阿里云",
            "api_format": "openai",
            "api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "",
            "llm_models": [],
            "vlm_models": [],
            "llm_model": "",
            "vlm_model": "",
            "description": "阿里云百炼 OpenAI 兼容接口",
            "temperature": 0.7,
            "max_tokens": 4096,
            "top_p": 0.9,
            "builtin": True,
        },
        {
            "id": "deepseek",
            "name": "DeepSeek",
            "api_format": "openai",
            "api_url": "https://api.deepseek.com",
            "api_key": "",
            "llm_models": [],
            "vlm_models": [],
            "llm_model": "",
            "vlm_model": "",
            "description": "DeepSeek OpenAI 兼容接口",
            "temperature": 0.7,
            "max_tokens": 4096,
            "top_p": 0.9,
            "builtin": True,
        },
    ],
    "active_scheme": "none",
    "custom_schemes": [],
    "read_media": False,
}


def _reference_aligned_size(image_w: int, image_h: int, scale: float) -> tuple[int, int]:
    """Choose H3-aligned dimensions near the scaled area without stretching refs."""
    multiple = h3.CANVAS_MULTIPLE
    scaled_w = max(float(multiple), image_w * scale)
    scaled_h = max(float(multiple), image_h * scale)
    target_area = scaled_w * scaled_h
    aspect = image_w / max(1, image_h)
    center_h_units = max(1, round(scaled_h / multiple))
    best = None

    for h_units in range(
        max(1, center_h_units - REFERENCE_SIZE_SEARCH_RADIUS),
        center_h_units + REFERENCE_SIZE_SEARCH_RADIUS + 1,
    ):
        ideal_w_units = h_units * aspect
        min_w_units = max(1, math.floor(ideal_w_units) - 2)
        max_w_units = max(min_w_units, math.ceil(ideal_w_units) + 2)
        for w_units in range(min_w_units, max_w_units + 1):
            target_w = w_units * multiple
            target_h = h_units * multiple
            ratio_error = abs((target_w / target_h) / aspect - 1.0)
            area_error = abs((target_w * target_h) / target_area - 1.0)
            score = ratio_error * 20.0 + area_error
            candidate = (score, ratio_error, area_error, target_w, target_h)
            if best is None or candidate < best:
                best = candidate

    return best[3], best[4]
_PROMPT_OPTIMIZER_CONFIG_LOCK = threading.RLock()
REFERENCE_PLACEHOLDER_RE = re.compile(r"__MINIMAX_H3_REF_(\d+)__")
UNRESOLVED_REFERENCE_RE = re.compile(r"__MINIMAX_H3_UNRESOLVED_REF_[^_]+__")
MODEL_FILE_EXTENSIONS = {".safetensors", ".gguf"}


def _normalise_model_name(name: str) -> str:
    """Turn community naming variants into comparable tokens.

    MiniMax H3 files appear with underscores, dashes, camel case and sometimes
    only a role folder (for example ``FL2VA/model.safetensors``). Matching the
    normalised path rather than one exact filename keeps the loader useful for
    community quantisations without admitting every unrelated model.
    """
    value = str(name or "").replace("\\", "/").lower()
    value = re.sub(r"([a-z])([0-9])", r"\1 \2", value)
    value = re.sub(r"([0-9])([a-z])", r"\1 \2", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _model_tokens(name: str) -> set[str]:
    return set(_normalise_model_name(name).split())


def _is_minimax_h3_name(normalised: str, compact: str, tokens: set[str]) -> bool:
    """Require an explicit MiniMax H3 identity before matching shared roles."""
    return "minimaxh3" in compact or ("minimax" in tokens and "h3" in compact)


def _is_weight_file(name: str) -> bool:
    return os.path.splitext(str(name or ""))[1].lower() in MODEL_FILE_EXTENSIONS


def _is_gguf_file(name: str) -> bool:
    return str(name or "").lower().endswith(".gguf")


def _category_names(category: str) -> list[str]:
    """Read a ComfyUI filename category without assuming it exists."""
    try:
        return [str(name) for name in folder_paths.get_filename_list(category)]
    except Exception:
        return []


def _category_paths(category: str) -> list[str]:
    try:
        entry = folder_paths.folder_names_and_paths.get(category)
        if not entry:
            return []
        paths = entry[0]
        if isinstance(paths, (str, os.PathLike)):
            paths = [paths]
        return [os.fspath(path) for path in paths]
    except Exception:
        return []


def _filesystem_weight_names(categories: tuple[str, ...]) -> list[str]:
    """Find GGUF files even when ComfyUI has no GGUF extension category yet."""
    names: list[str] = []
    for category in categories:
        for base in _category_paths(category):
            if not os.path.isdir(base):
                continue
            try:
                for root, _dirs, files in os.walk(base):
                    for filename in files:
                        if os.path.splitext(filename)[1].lower() not in MODEL_FILE_EXTENSIONS:
                            continue
                        full_path = os.path.join(root, filename)
                        relative = os.path.relpath(full_path, base).replace(os.sep, "/")
                        names.append(relative)
            except OSError:
                continue
    return names


@lru_cache(maxsize=16)
def _collect_weight_names(categories: tuple[str, ...]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for category in categories:
        for name in _category_names(category):
            if not _is_weight_file(name):
                continue
            key = name.replace("\\", "/")
            if key not in seen:
                seen.add(key)
                names.append(key)
    # The normal ComfyUI categories may not advertise .gguf until the optional
    # GGUF node is loaded, so supplement them from the actual model folders.
    for name in _filesystem_weight_names(categories):
        key = name.replace("\\", "/")
        if key not in seen:
            seen.add(key)
            names.append(key)
    return names


def _has_role(name: str, role: str) -> bool:
    normalised = _normalise_model_name(name)
    compact = normalised.replace(" ", "")
    tokens = set(normalised.split())
    if role == "fl2va":
        if "minimax" not in tokens and "h3" not in compact:
            return False
        if "ref2va" in compact or "ref2v" in compact:
            return False
        return "fl2va" in compact or "fl2v" in compact
    if role == "ref2va":
        if "minimax" not in tokens and "h3" not in compact:
            return False
        return "ref2va" in compact or "ref2v" in compact
    if role == "text_encoder":
        if ("qwen3vl" in compact or ("qwen3" in tokens and "vl" in tokens)) and (
            "32b" in tokens or "32" in tokens
        ):
            return True
        # Some community H3 exports omit "minimax_h3" from the encoder
        # filename but retain the characteristic INT8/ConvRot or NVFP4/AWQ
        # variant naming.
        if (
            "qwen3" in tokens
            and "vl" in tokens
            and ("32b" in tokens or "32" in tokens)
            and (("int8" in tokens and "convrot" in tokens) or ("nvfp4" in tokens and "awq" in tokens))
        ):
            return True
        # A few community exports use only text_encoder.safetensors, but keep
        # the match scoped to an H3-named path to avoid generic CLIP files.
        return "text encoder" in normalised and ("minimax" in tokens or "h3" in compact)
    if role == "video_vae":
        is_minimax_h3 = _is_minimax_h3_name(normalised, compact, tokens)
        is_video_vae = (
            ("video" in tokens and "vae" in tokens)
            or "videovae" in compact
            # Diffusers-style exports may use MiniMax-H3/vae/... without the
            # word "video". In H3, an unqualified VAE is the visual VAE.
            or ("vae" in tokens and "audio" not in tokens and "audiovae" not in compact)
        )
        return is_minimax_h3 and is_video_vae and "tae" not in tokens and "approx" not in tokens
    if role == "audio_vae":
        is_minimax_h3 = _is_minimax_h3_name(normalised, compact, tokens)
        is_audio_vae = (
            ("audio" in tokens and "vae" in tokens)
            or "audiovae" in compact
        )
        return is_minimax_h3 and is_audio_vae and "tae" not in tokens and "approx" not in tokens
    return False


def _sort_model_names(names: list[str]) -> list[str]:
    def sort_key(name: str) -> tuple[int, int, str]:
        normalised = _normalise_model_name(name)
        # Keep safetensors first for the native path, followed by GGUF. Within
        # each group use a deterministic name order for stable workflows.
        extension_rank = 1 if _is_gguf_file(name) else 0
        official_rank = 0 if "minimax" in normalised and "h3" in normalised else 1
        return extension_rank, official_rank, normalised

    return sorted(names, key=sort_key)


def _is_none_model(value: Any) -> bool:
    return str(value or "").strip().lower() in NONE_MODEL_ALIASES


def _as_bool(value: Any) -> bool:
    """Parse workflow booleans without treating the string 'false' as true."""
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _read_prompt_guide_text(relative_path: str) -> str:
    path = os.path.realpath(os.path.join(PROMPT_GUIDES_DIR, str(relative_path or "")))
    root = os.path.realpath(PROMPT_GUIDES_DIR)
    if not path.startswith(root + os.sep) or not os.path.isfile(path):
        raise ValueError(f"Prompt guide file not found: {relative_path}")
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


@lru_cache(maxsize=1)
def _prompt_guide_manifest() -> dict[str, Any]:
    try:
        with open(PROMPT_GUIDE_MANIFEST, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _prompt_guide_bundle(
    scene_guide: str,
    mode: str,
    seconds: float,
    media_counts: Mapping[str, int],
    custom_prompt: str = "",
) -> str:
    manifest = _prompt_guide_manifest()
    general = manifest.get("general") if isinstance(manifest.get("general"), dict) else {}
    blocks = [
        "You are the MiniMax H3 Prompt Optimizer inside a ComfyUI node.",
        "Return only the final prompt text. Do not add explanations, markdown fences, titles, or commentary.",
        "Use the complete prompt guide text below. Preserve all official field names, section order, labels, timing notation, dialogue language, and reference tags.",
        f"Node context: mode={mode}; duration_seconds={float(seconds):.2f}; media_counts={dict(media_counts)}.",
    ]
    if general.get("path"):
        blocks.append("=== H3 GENERAL PROMPT GUIDE ===\n" + _read_prompt_guide_text(str(general["path"])))
    if general.get("base_reference") and mode != MODE_REFERENCE:
        blocks.append("=== H3 BASE REFERENCE GUIDE ===\n" + _read_prompt_guide_text(str(general["base_reference"])))
    if general.get("ref_reference") and mode == MODE_REFERENCE:
        blocks.append("=== H3 FULL-REFERENCE GUIDE ===\n" + _read_prompt_guide_text(str(general["ref_reference"])))
    if scene_guide and scene_guide != "none":
        for item in manifest.get("scene_guides") or []:
            if isinstance(item, dict) and str(item.get("id")) == scene_guide and item.get("path"):
                scene_path = str(item["path"])
                blocks.append("=== SELECTED SCENE PROMPT GUIDE ===\n" + _read_prompt_guide_text(scene_path))
                reference_dir = os.path.join(PROMPT_GUIDES_DIR, os.path.dirname(scene_path), "references")
                if os.path.isdir(reference_dir):
                    for root, _dirs, filenames in os.walk(reference_dir):
                        for filename in sorted(filenames):
                            if os.path.splitext(filename)[1].lower() not in {".md", ".txt"}:
                                continue
                            relative = os.path.relpath(os.path.join(root, filename), PROMPT_GUIDES_DIR).replace(os.sep, "/")
                            blocks.append(f"=== SELECTED SCENE REFERENCE: {relative} ===\n" + _read_prompt_guide_text(relative))
                break
    if str(custom_prompt or "").strip():
        blocks.append("=== CUSTOM PROMPT GUIDE ===\n" + str(custom_prompt).strip())
    return "\n\n".join(blocks)


def _prompt_optimizer_config_path() -> str:
    return os.path.join(
        folder_paths.get_user_directory(),
        "default",
        "ComfyUI-FeiHou-Easy-H3",
        "prompt_optimizer.json",
    )


def _legacy_prompt_optimizer_config_path() -> str:
    return os.path.join(os.path.dirname(os.path.realpath(__file__)), "prompt_optimizer.json")


def _safe_config_id(value: Any, prefix: str) -> str:
    token = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(value or "").strip()).strip("_").lower()
    return token[:64] or f"{prefix}_{abs(hash(str(value))) & 0xFFFFFFFF:x}"


def _default_optimizer_providers() -> list[dict[str, Any]]:
    return [dict(item) for item in PROMPT_OPTIMIZER_CONFIG_DEFAULTS["providers"]]


def _normalize_model_names(value: Any, fallback: Any = None) -> list[str]:
    source = value if isinstance(value, list) else (fallback if isinstance(fallback, list) else [])
    result: list[str] = []
    for item in source[:50]:
        name = str(item.get("name") if isinstance(item, Mapping) else item or "").strip()
        if name and name not in result:
            result.append(name[:160])
    return result


_PROMPT_OPTIMIZER_V3_SEEDED_MODELS = {
    "zhipu": {
        "llm_models": ["glm-4-flash-250414", "glm-4.5-flash"],
        "vlm_models": ["glm-4.6V-Flash", "glm-4v-flash"],
    },
    "xflow": {
        "llm_models": ["gemini-3-flash-preview-nothinking"],
        "vlm_models": ["grok-4-1-fast-non-reasoning"],
    },
    "aliyun": {
        "llm_models": ["qwen-plus"],
        "vlm_models": ["qwen-vl-max"],
    },
    "deepseek": {
        "llm_models": ["deepseek-chat"],
        "vlm_models": [],
    },
}


def _normalize_prompt_optimizer_config(
    value: Mapping[str, Any] | None,
    existing: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    previous = existing if isinstance(existing, Mapping) else {}
    try:
        source_version = int(source.get("version") or previous.get("version") or PROMPT_OPTIMIZER_CONFIG_VERSION)
    except (TypeError, ValueError):
        source_version = PROMPT_OPTIMIZER_CONFIG_VERSION
    previous_providers = {
        str(item.get("id")): item
        for item in previous.get("providers", [])
        if isinstance(item, Mapping) and item.get("id")
    }
    defaults = {item["id"]: item for item in _default_optimizer_providers()}

    raw_providers = source.get("providers") if isinstance(source.get("providers"), list) else []
    # Migrate the original single-endpoint configuration into a custom service.
    if not raw_providers and any(key in source for key in ("api_url", "api_key", "model")):
        raw_providers = [
            {
                "id": "custom",
                "name": "自定义 API",
                "api_format": source.get("api_format", "openai"),
                "api_url": source.get("api_url", ""),
                "api_key": source.get("api_key", ""),
                "model": source.get("model", ""),
                "builtin": False,
            }
        ]

    supplied: dict[str, Mapping[str, Any]] = {}
    for item in raw_providers[:20]:
        if not isinstance(item, Mapping):
            continue
        provider_id = _safe_config_id(item.get("id") or item.get("name"), "provider")
        if provider_id and provider_id not in supplied:
            supplied[provider_id] = item

    providers: list[dict[str, Any]] = []
    ordered_ids = [*defaults]
    ordered_ids.extend(provider_id for provider_id in supplied if provider_id not in defaults)
    for provider_id in ordered_ids:
        template = defaults.get(provider_id, {})
        item = supplied.get(provider_id, template)
        previous_item = previous_providers.get(provider_id, {})
        if source_version < 4 and provider_id in _PROMPT_OPTIMIZER_V3_SEEDED_MODELS:
            seeded = _PROMPT_OPTIMIZER_V3_SEEDED_MODELS[provider_id]
            migrated_item = dict(item)
            for model_kind in ("llm", "vlm"):
                list_key = f"{model_kind}_models"
                active_key = f"{model_kind}_model"
                seeded_models = seeded[list_key]
                current_models = _normalize_model_names(migrated_item.get(list_key))
                if current_models == seeded_models:
                    migrated_item[list_key] = []
                    migrated_item[active_key] = ""
                    if model_kind == "llm":
                        migrated_item["model"] = ""
            item = migrated_item
        elif source_version < 4 and provider_id == "ollama" and str(item.get("api_key") or "") == "ollama":
            item = {**item, "api_key": ""}
        api_format = str(item.get("api_format") or template.get("api_format") or "openai").strip().lower()
        if api_format not in {"openai", "gemini", "ollama"}:
            api_format = "openai"
        if item.get("clear_api_key"):
            api_key = ""
        elif "api_key" in item:
            api_key = str(item.get("api_key") or "")
        else:
            api_key = str(previous_item.get("api_key") or template.get("api_key") or "")
        legacy_model = str(item.get("model") or previous_item.get("model") or template.get("model") or "").strip()
        llm_models = _normalize_model_names(
            item.get("llm_models"),
            previous_item.get("llm_models") or template.get("llm_models"),
        )
        vlm_models = _normalize_model_names(
            item.get("vlm_models"),
            previous_item.get("vlm_models") or template.get("vlm_models"),
        )
        if legacy_model and not llm_models:
            llm_models = [legacy_model]
        llm_model = str(
            item.get("llm_model")
            or previous_item.get("llm_model")
            or template.get("llm_model")
            or legacy_model
            or (llm_models[0] if llm_models else "")
        ).strip()
        vlm_model = str(
            item.get("vlm_model")
            or previous_item.get("vlm_model")
            or template.get("vlm_model")
            or (vlm_models[0] if vlm_models else "")
        ).strip()
        if llm_model and llm_model not in llm_models:
            llm_models.insert(0, llm_model)
        if vlm_model and vlm_model not in vlm_models:
            vlm_models.insert(0, vlm_model)
        providers.append({
            "id": provider_id,
            "name": str(item.get("name") or template.get("name") or provider_id).strip()[:80],
            "description": str(item.get("description") or template.get("description") or "").strip()[:160],
            "api_format": api_format,
            "api_url": str(item.get("api_url") or template.get("api_url") or "").strip(),
            "api_key": api_key,
            "llm_models": llm_models,
            "vlm_models": vlm_models,
            "llm_model": llm_model,
            "vlm_model": vlm_model,
            "temperature": min(2.0, max(0.0, float(item.get("temperature", template.get("temperature", 0.7)) or 0.7))),
            "max_tokens": min(PROMPT_OPTIMIZER_MAX_OUTPUT_TOKENS, max(1, int(item.get("max_tokens", template.get("max_tokens", 4096)) or 4096))),
            "top_p": min(1.0, max(0.0, float(item.get("top_p", template.get("top_p", 0.9)) or 0.9))),
            "builtin": provider_id in defaults,
        })

    custom_schemes = []
    seen_schemes = set()
    for item in source.get("custom_schemes", []) if isinstance(source.get("custom_schemes"), list) else []:
        if not isinstance(item, Mapping):
            continue
        scheme_id = _safe_config_id(item.get("id") or item.get("name"), "scheme")
        if not scheme_id.startswith("custom_"):
            scheme_id = f"custom_{scheme_id}"
        if scheme_id in seen_schemes:
            continue
        seen_schemes.add(scheme_id)
        name = str(item.get("name") or "自定义提示词方案").strip()[:100]
        prompt = str(item.get("prompt") or "").strip()
        if name and prompt:
            custom_schemes.append({"id": scheme_id, "name": name, "prompt": prompt})
        if len(custom_schemes) >= 50:
            break

    provider_ids = {item["id"] for item in providers}
    active_provider = _safe_config_id(source.get("active_provider") or previous.get("active_provider") or "zhipu", "provider")
    if active_provider not in provider_ids:
        active_provider = providers[0]["id"] if providers else "zhipu"
    scheme_ids = {"none", *{
        str(item.get("id"))
        for item in (_prompt_guide_manifest().get("scene_guides") or [])
        if isinstance(item, Mapping) and item.get("id")
    }, *{item["id"] for item in custom_schemes}}
    active_scheme = str(source.get("active_scheme") or previous.get("active_scheme") or "none")
    if active_scheme not in scheme_ids:
        active_scheme = "none"
    read_media = source.get("read_media", False)
    if isinstance(read_media, str):
        read_media = read_media.strip().lower() in {"1", "true", "yes", "on"}
    return {
        "version": PROMPT_OPTIMIZER_CONFIG_VERSION,
        "active_provider": active_provider,
        "providers": providers,
        "active_scheme": active_scheme,
        "custom_schemes": custom_schemes,
        "read_media": bool(read_media),
    }


def _public_prompt_optimizer_config(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    public_providers = []
    for item in value.get("providers", []) if isinstance(value.get("providers"), list) else []:
        provider = dict(item)
        api_key = str(provider.pop("api_key", "") or "")
        provider["api_key_exists"] = bool(api_key)
        provider["api_key_masked"] = (
            f"{api_key[:6]}***{api_key[-4:]}" if len(api_key) >= 12 else ("***" if api_key else "")
        )
        public_providers.append(provider)
    result["providers"] = public_providers
    result["schemes"] = _public_prompt_optimizer_schemes(value)
    return result


def _public_prompt_optimizer_schemes(value: Mapping[str, Any]) -> list[dict[str, Any]]:
    manifest = _prompt_guide_manifest()
    general = manifest.get("general") if isinstance(manifest.get("general"), Mapping) else {}
    schemes: list[dict[str, Any]] = []
    for item in manifest.get("scene_guides", []) if isinstance(manifest.get("scene_guides"), list) else []:
        if not isinstance(item, Mapping) or not item.get("id"):
            continue
        scheme_id = str(item["id"])
        path = str(item.get("path") or (general.get("path") if scheme_id == "none" else ""))
        try:
            prompt = _read_prompt_guide_text(path) if path else ""
        except (OSError, ValueError):
            prompt = ""
        schemes.append({
            "id": scheme_id,
            "name": str(item.get("name") or scheme_id),
            "name_zh": str(item.get("name_zh") or item.get("name") or scheme_id),
            "prompt": prompt,
            "editable": False,
        })
    for item in value.get("custom_schemes", []) if isinstance(value.get("custom_schemes"), list) else []:
        if not isinstance(item, Mapping) or not item.get("id"):
            continue
        schemes.append({
            "id": str(item["id"]),
            "name": str(item.get("name") or item["id"]),
            "name_zh": str(item.get("name") or item["id"]),
            "prompt": str(item.get("prompt") or ""),
            "editable": True,
        })
    return schemes


def _prompt_optimizer_scheme_choices(settings: Mapping[str, Any] | None = None) -> tuple[list[str], str]:
    config = settings if isinstance(settings, Mapping) else _read_prompt_optimizer_config()
    choices = [item["id"] for item in _public_prompt_optimizer_schemes(config)] or ["none"]
    default = str(config.get("active_scheme") or "none")
    return choices, default if default in choices else choices[0]


def _prompt_optimizer_provider_choices(settings: Mapping[str, Any] | None = None) -> list[str]:
    config = settings if isinstance(settings, Mapping) else _read_prompt_optimizer_config()
    choices = []
    for item in config.get("providers", []) if isinstance(config.get("providers"), list) else []:
        if not isinstance(item, Mapping) or not item.get("id"):
            continue
        provider_id = str(item.get("id"))
        models = _normalize_model_names([
            *(item.get("llm_models") if isinstance(item.get("llm_models"), list) else []),
            *(item.get("vlm_models") if isinstance(item.get("vlm_models"), list) else []),
        ])
        choices.extend(f"{provider_id}/{model}" for model in models)
    # ComfyUI combo inputs require at least one value before a user has added
    # an API model in Settings.  An empty value is a configuration placeholder,
    # not an exposed “Disabled” option; the optimizer switch controls execution.
    return choices or [""]


def _read_prompt_optimizer_config() -> dict[str, Any]:
    path = _prompt_optimizer_config_path()
    with _PROMPT_OPTIMIZER_CONFIG_LOCK:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            try:
                with open(_legacy_prompt_optimizer_config_path(), "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError, ValueError):
                payload = PROMPT_OPTIMIZER_CONFIG_DEFAULTS
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            payload = PROMPT_OPTIMIZER_CONFIG_DEFAULTS
    return _normalize_prompt_optimizer_config(payload)


def _write_prompt_optimizer_config(value: Mapping[str, Any] | None) -> dict[str, Any]:
    current = _read_prompt_optimizer_config()
    normalized = _normalize_prompt_optimizer_config(value, current)
    path = _prompt_optimizer_config_path()
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    temporary_path = ""
    with _PROMPT_OPTIMIZER_CONFIG_LOCK:
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=directory,
                prefix=".prompt_optimizer.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = handle.name
                json.dump(normalized, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temporary_path, path)
        finally:
            if temporary_path and os.path.exists(temporary_path):
                try:
                    os.remove(temporary_path)
                except OSError:
                    pass
    return normalized


_OPTIMIZER_KNOWN_ENDPOINT_SUFFIXES = (
    "/v1/chat/completions",
    "/chat/completions",
)
_OPTIMIZER_GEMINI_ENDPOINT_RE = re.compile(
    r"/(v1beta|v1)/models/[^/?:#]+?:(generateContent|streamGenerateContent)$",
    flags=re.I,
)


def _normalize_optimizer_base_url(api_url: str) -> str:
    base = str(api_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("Prompt optimization API URL is required")
    if not re.match(r"^https?://", base, flags=re.I):
        base = "https://" + base
    return base.rstrip("/")


def _optimizer_endpoint_kind(value: str) -> str:
    lower = str(value or "").lower()
    if lower.endswith("/chat/completions"):
        return "chat"
    if _OPTIMIZER_GEMINI_ENDPOINT_RE.search(lower):
        return "gemini"
    return ""


def _normalize_gemini_model_id(model: str) -> str:
    """Accept a bare model ID, ``models/<id>``, or a full Gemini model URL."""
    raw = urllib.parse.unquote(str(model or "").strip())
    if not raw:
        raise ValueError("Prompt optimization model is required")
    if "://" in raw:
        raw = urllib.parse.urlsplit(raw).path
    raw = raw.split("?", 1)[0].split("#", 1)[0].strip().strip("/")
    match = re.search(r"(?:^|/)models/([^/:]+)(?::[A-Za-z]+)?$", raw, flags=re.I)
    if match:
        raw = match.group(1)
    else:
        if raw.lower().startswith("models/"):
            raw = raw[7:]
        raw = raw.rsplit("/", 1)[-1]
        raw = re.sub(r":(?:generateContent|streamGenerateContent)$", "", raw, flags=re.I)
    raw = raw.strip()
    if not raw:
        raise ValueError("Prompt optimization model is required")
    return raw


def _gemini_url_with_query(url: str, query: str) -> str:
    # ``alt=sse`` belongs to streamGenerateContent and would corrupt the JSON
    # response expected from generateContent. Preserve other proxy parameters.
    pairs = [(key, value) for key, value in urllib.parse.parse_qsl(query, keep_blank_values=True) if key.lower() != "alt"]
    encoded = urllib.parse.urlencode(pairs)
    return url + (f"?{encoded}" if encoded else "")


def _normalize_gemini_optimizer_url(api_url: str, model: str) -> str:
    base = _normalize_optimizer_base_url(api_url)
    parsed = urllib.parse.urlsplit(base)
    clean = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
    lower = clean.lower()
    model_id = urllib.parse.quote(_normalize_gemini_model_id(model), safe=".-_")

    endpoint_match = _OPTIMIZER_GEMINI_ENDPOINT_RE.search(lower)
    if endpoint_match and lower.endswith(endpoint_match.group(0)):
        version = endpoint_match.group(1)
        clean = clean[: endpoint_match.start()].rstrip("/")
        url = f"{clean}/{version}/models/{model_id}:generateContent"
        return _gemini_url_with_query(url, parsed.query)

    if lower.endswith("/v1beta/models") or lower.endswith("/v1/models"):
        url = f"{clean}/{model_id}:generateContent"
    elif lower.endswith("/v1beta") or lower.endswith("/v1"):
        url = f"{clean}/models/{model_id}:generateContent"
    elif lower.endswith("/models"):
        url = f"{clean}/{model_id}:generateContent"
    else:
        url = f"{clean}/v1beta/models/{model_id}:generateContent"
    return _gemini_url_with_query(url, parsed.query)


def _strip_optimizer_endpoint(base: str) -> str:
    lower = base.lower()
    for suffix in _OPTIMIZER_KNOWN_ENDPOINT_SUFFIXES:
        if lower.endswith(suffix):
            return base[: len(base) - len(suffix)].rstrip("/")
    match = _OPTIMIZER_GEMINI_ENDPOINT_RE.search(lower)
    if match and lower.endswith(match.group(0)):
        return base[: match.start()].rstrip("/")
    return base


def _normalize_optimizer_url(api_url: str, api_format: str, model: str) -> str:
    if api_format == "gemini":
        return _normalize_gemini_optimizer_url(api_url, model)
    if api_format == "ollama":
        base = _normalize_optimizer_base_url(api_url)
        base = _strip_optimizer_endpoint(base)
        if base.lower().endswith("/v1"):
            base = base[:-3].rstrip("/")
        if base.lower().endswith("/api/chat"):
            return base
        if base.lower().endswith("/api"):
            return base + "/chat"
        return base + "/api/chat"
    base = _normalize_optimizer_base_url(api_url)
    endpoint = "/v1/chat/completions"
    base_kind = _optimizer_endpoint_kind(base)
    endpoint_kind = _optimizer_endpoint_kind(endpoint)
    if base_kind == endpoint_kind == "chat":
        return base
    if base_kind == endpoint_kind == "gemini":
        base_match = _OPTIMIZER_GEMINI_ENDPOINT_RE.search(base.lower())
        if base_match and base.lower().endswith(base_match.group(0)) and base_match.group(0) == endpoint.lower():
            return base
    base = _strip_optimizer_endpoint(base)
    if base.lower().endswith("/v1") and endpoint.lower().startswith("/v1/"):
        endpoint = endpoint[3:]
    if base.lower().endswith("/v1beta") and endpoint.lower().startswith("/v1beta/"):
        endpoint = endpoint[7:]
    return base + endpoint


def _optimizer_model_list_url(api_url: str, api_format: str) -> str:
    base = _normalize_optimizer_base_url(api_url)
    base = _strip_optimizer_endpoint(base)
    if api_format == "ollama":
        if base.lower().endswith("/v1"):
            base = base[:-3].rstrip("/")
        if base.lower().endswith("/api/tags"):
            return base
        if base.lower().endswith("/api"):
            return base + "/tags"
        return base + "/api/tags"
    if base.lower().endswith("/models"):
        return base
    return base + "/models"


def _optimizer_available_models(provider: Mapping[str, Any]) -> list[str]:
    provider_id = str(provider.get("id") or "").strip().lower()
    if provider_id == "zhipu":
        # 智谱没有公开、稳定的模型枚举接口；与 prompt-assistant 一致使用其维护的可选清单。
        return list(PROMPT_OPTIMIZER_ZHIPU_MODELS)

    api_format = str(provider.get("api_format") or "openai").strip().lower()
    api_url = str(provider.get("api_url") or "").strip()
    api_key = str(provider.get("api_key") or "").strip()
    if api_format != "ollama" and not api_key:
        raise ValueError("请先填写并保存 API Key")

    url = _optimizer_model_list_url(api_url, api_format)
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if api_format != "ollama" and api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=PROMPT_OPTIMIZER_MODEL_LIST_TIMEOUT_SECONDS) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if exc.code == 401:
            raise RuntimeError("API Key 错误，认证失败") from exc
        if exc.code == 404:
            raise RuntimeError("Base URL 未找到模型列表接口") from exc
        raise RuntimeError(f"模型列表接口返回 HTTP {exc.code}: {detail[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接模型列表接口: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("获取模型列表超时") from exc

    raw_models: Any
    if api_format == "ollama":
        raw_models = data.get("models", []) if isinstance(data, Mapping) else []
    else:
        raw_models = data.get("data", []) if isinstance(data, Mapping) else []
        if not raw_models and isinstance(data, Mapping):
            raw_models = data.get("models", [])
    names: list[str] = []
    for item in raw_models if isinstance(raw_models, list) else []:
        if isinstance(item, Mapping):
            name = item.get("id") or item.get("name") or item.get("model")
        else:
            name = item
        text = str(name or "").strip()
        if text and text not in names:
            names.append(text[:200])
    if not names:
        raise RuntimeError("接口未返回任何可用模型")
    return names


def _optimizer_log_url(url: str) -> str:
    """Return an endpoint identifier without query parameters or credentials."""
    try:
        parsed = urllib.parse.urlsplit(str(url or ""))
        if parsed.scheme and parsed.netloc:
            return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    except (TypeError, ValueError):
        pass
    return "<invalid endpoint>"


def _optimizer_log_media_summary(media_parts: list[Mapping[str, Any]]) -> str:
    """Summarize attached media without exposing filenames or Base64 payloads."""
    if not media_parts:
        return "none"
    summary: dict[str, list[int]] = {}
    for part in media_parts:
        kind = "unknown"
        encoded = ""
        if isinstance(part.get("inlineData"), Mapping):
            inline = part["inlineData"]
            kind = str(inline.get("mimeType") or "inline")
            encoded = str(inline.get("data") or "")
        elif str(part.get("type") or "") == "image_url":
            kind = "image_url"
            image_url = part.get("image_url") if isinstance(part.get("image_url"), Mapping) else {}
            encoded = str(image_url.get("url") or "")
        elif str(part.get("type") or "") == "ollama_image":
            kind = "ollama_image"
            encoded = str(part.get("data") or "")
        summary.setdefault(kind, []).append(len(encoded))
    return ", ".join(
        f"{kind}×{len(sizes)} (~{sum(sizes) * 3 // 4 // 1024} KiB encoded)"
        for kind, sizes in sorted(summary.items())
    )


def _redact_optimizer_log_text(value: Any, limit: int = 2000) -> str:
    """Keep provider diagnostics useful while never logging secrets or media data."""
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    text = re.sub(r"data:[^\s\"']+;base64,[A-Za-z0-9+/=_-]+", "data:<redacted>", text, flags=re.I)
    text = re.sub(r"\b(?:sk|rk|key)-[A-Za-z0-9._-]{8,}\b", "<redacted-key>", text, flags=re.I)
    text = re.sub(r"(?i)(authorization|api[_ -]?key|x-goog-api-key)(\s*[:=]\s*)([^,}\]\s]+)", r"\1\2<redacted>", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def _optimizer_http_json(
    api_url: str,
    api_key: str,
    model: str,
    api_format: str,
    system_prompt: str,
    user_prompt: str,
    media_parts: list[dict[str, Any]] | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    top_p: float = 0.9,
    _allow_media_fallback: bool = True,
    _allow_parameter_fallback: bool = True,
    _minimal_openai_payload: bool = False,
) -> str:
    url = _normalize_optimizer_url(api_url, api_format, model)
    request_id = uuid.uuid4().hex[:8]
    media_parts = list(media_parts or [])
    temperature = min(2.0, max(0.0, float(temperature)))
    max_tokens = min(PROMPT_OPTIMIZER_MAX_OUTPUT_TOKENS, max(1, int(max_tokens)))
    top_p = min(1.0, max(0.0, float(top_p)))
    if api_format == "gemini":
        headers = {"Content-Type": "application/json", "Accept": "application/json", "x-goog-api-key": api_key}
        # Some Gemini-compatible channels accept the native payload and return
        # candidates but silently ignore systemInstruction. Keep the complete
        # Prompt Guide and the user's source prompt in the same user text part,
        # matching the node's previously verified working Gemini request.
        instruction_and_prompt = (
            system_prompt
            + "\n\n=== USER PROMPT TO OPTIMIZE ===\n"
            + user_prompt
            + "\n\nFollow the Prompt Guide above and return only the final rewritten MiniMax H3 prompt."
        )
        parts = [{"text": instruction_and_prompt}] + media_parts
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens, "topP": top_p},
        }
    elif api_format == "ollama":
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if api_key and api_key.lower() != "ollama":
            headers["Authorization"] = f"Bearer {api_key}"
        images = [str(item.get("data")) for item in media_parts if item.get("type") == "ollama_image" and item.get("data")]
        user_message: dict[str, Any] = {"role": "user", "content": user_prompt}
        if images:
            user_message["images"] = images
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system_prompt}, user_message],
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens, "top_p": top_p},
        }
    else:
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        content: str | list[dict[str, Any]]
        if media_parts:
            content = [{"type": "text", "text": user_prompt}, *media_parts]
        else:
            content = user_prompt
        # xFlow/Grok and several other OpenAI-compatible aggregation gateways
        # always return SSE, even when stream=false is requested.  Explicitly
        # request a stream and consume it below, matching prompt-assistant.
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": content}],
            "stream": True,
        }
        if not _minimal_openai_payload:
            payload.update({"temperature": temperature, "max_tokens": max_tokens, "top_p": top_p})
    request = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
    logging.info(
        "[Easy H3 Prompt API %s] request format=%s model=%s url=%s stream=%s media=%s",
        request_id,
        api_format,
        model,
        _optimizer_log_url(url),
        bool(payload.get("stream")),
        _optimizer_log_media_summary(media_parts),
    )
    try:
        with urllib.request.urlopen(request, timeout=PROMPT_OPTIMIZER_TIMEOUT_SECONDS) as response:
            raw_response = response.read().decode("utf-8", errors="replace")
            status = getattr(response, "status", 200)
            content_type = str(response.headers.get("Content-Type") or "unknown")
        try:
            data = json.loads(raw_response)
        except json.JSONDecodeError as exc:
            # A few OpenAI-compatible aggregation services return Server-Sent
            # Events. Rebuild a normal chat-completions-shaped response from
            # their delta chunks.
            streamed_parts: list[str] = []
            stream_parse_failed = False
            saw_stream_event = False
            for line in raw_response.splitlines():
                if not line.startswith("data:"):
                    continue
                saw_stream_event = True
                event_data = line[5:].strip()
                if not event_data or event_data == "[DONE]":
                    continue
                try:
                    event = json.loads(event_data)
                except json.JSONDecodeError:
                    stream_parse_failed = True
                    break
                choices = event.get("choices") if isinstance(event, Mapping) else []
                for choice in choices if isinstance(choices, list) else []:
                    if not isinstance(choice, Mapping):
                        continue
                    delta = choice.get("delta") if isinstance(choice.get("delta"), Mapping) else {}
                    message = choice.get("message") if isinstance(choice.get("message"), Mapping) else {}
                    part = delta.get("content", message.get("content", ""))
                    if isinstance(part, str):
                        streamed_parts.append(part)
                    elif isinstance(part, list):
                        streamed_parts.extend(
                            str(item.get("text", ""))
                            for item in part
                            if isinstance(item, Mapping) and item.get("text") is not None
                        )
            if saw_stream_event and not stream_parse_failed:
                data = {"choices": [{"message": {"content": "".join(streamed_parts)}}]}
            else:
                if media_parts and _allow_media_fallback:
                    return _optimizer_http_json(
                        api_url, api_key, model, api_format, system_prompt, user_prompt,
                        [], temperature, max_tokens, top_p, _allow_media_fallback=False,
                    )
                preview = raw_response.strip().replace("\n", " ")[:500]
                if not preview:
                    preview = "<empty response>"
                logging.error(
                    "[Easy H3 Prompt API %s] non-JSON response http=%s content_type=%s model=%s body=%s",
                    request_id,
                    status,
                    content_type,
                    model,
                    _redact_optimizer_log_text(raw_response),
                )
                raise RuntimeError(
                    f"提示词优化接口返回非 JSON 内容（HTTP {status}，{content_type}）：{preview}"
                ) from exc
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        safe_detail = _redact_optimizer_log_text(detail)
        logging.error(
            "[Easy H3 Prompt API %s] HTTP %s content_type=%s format=%s model=%s url=%s response=%s",
            request_id,
            exc.code,
            str(exc.headers.get("Content-Type") or "unknown") if exc.headers else "unknown",
            api_format,
            model,
            _optimizer_log_url(url),
            safe_detail or "<empty response>",
        )
        raise RuntimeError(f"Prompt optimization API error ({exc.code}): {(safe_detail or '<empty response>')[:1000]}") from exc
    except urllib.error.URLError as exc:
        logging.error(
            "[Easy H3 Prompt API %s] network failure format=%s model=%s url=%s reason=%s",
            request_id,
            api_format,
            model,
            _optimizer_log_url(url),
            _redact_optimizer_log_text(exc.reason, 500),
        )
        raise RuntimeError(f"Prompt optimization request failed: {exc.reason}") from exc
    if api_format == "gemini":
        candidates = data.get("candidates") if isinstance(data, dict) else None
        if not isinstance(candidates, list) or not candidates:
            feedback = data.get("promptFeedback") if isinstance(data, dict) else None
            reason = feedback.get("blockReason") if isinstance(feedback, dict) else None
            detail = f": {reason}" if reason else ""
            raise RuntimeError(f"Gemini API returned no candidates{detail}")
        candidate = candidates[0] if isinstance(candidates[0], dict) else {}
        parts = candidate.get("content", {}).get("parts", []) if isinstance(candidate.get("content"), dict) else []
        text = "".join(str(part.get("text", "")) for part in parts if isinstance(part, dict) and part.get("text") is not None)
        if not text.strip():
            finish_reason = candidate.get("finishReason") or candidate.get("finish_reason") or "unknown"
            raise RuntimeError(f"Gemini API returned no text (finish reason: {finish_reason})")
    elif api_format == "ollama":
        message = data.get("message") if isinstance(data, dict) else None
        content = message.get("content", "") if isinstance(message, dict) else data.get("response", "") if isinstance(data, dict) else ""
        text = str(content or "")
    else:
        content = ((data.get("choices") or [{}])[0].get("message", {}) or {}).get("content", "")
        text = content if isinstance(content, str) else "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
    text = str(text or "").strip()
    if not text:
        # Some xFlow/Grok routes acknowledge an extended payload but emit only
        # a usage SSE chunk. Retry once with the minimal compatible request.
        if api_format == "openai" and _allow_parameter_fallback:
            return _optimizer_http_json(
                api_url, api_key, model, api_format, system_prompt, user_prompt,
                [], temperature, max_tokens, top_p,
                _allow_media_fallback=False,
                _allow_parameter_fallback=False,
                _minimal_openai_payload=True,
            )
        raise RuntimeError("Prompt optimization API returned an empty response")
    return text


def _optimizer_asset_path(asset: Mapping[str, Any]) -> str | None:
    filename = str(asset.get("filename") or "").strip()
    if not filename or os.path.isabs(filename):
        return None
    storage = str(asset.get("storage") or "input").lower()
    roots = {
        "input": folder_paths.get_input_directory(),
        "output": folder_paths.get_output_directory(),
        "temp": folder_paths.get_temp_directory(),
    }
    root = os.path.realpath(roots.get(storage, roots["input"]))
    subfolder = str(asset.get("subfolder") or "").replace("\\", "/").strip("/")
    candidate = os.path.realpath(os.path.join(root, subfolder, filename))
    if candidate != root and not candidate.startswith(root + os.sep):
        return None
    return candidate if os.path.isfile(candidate) else None


def _optimizer_reference_short_edge(value: Any) -> int:
    """Resolve the node's reference-image-size selector to a supported edge."""
    legacy_short_edges = {"match": 480, "1k": 1024, "1.5k": 1088, "2k": 1088, "original": 1088}
    try:
        short_edge = int(str(value or REF_IMAGE_DEFAULT))
    except (TypeError, ValueError):
        short_edge = legacy_short_edges.get(str(value or "").strip().lower(), int(REF_IMAGE_DEFAULT))
    allowed = tuple(int(item) for item in REFERENCE_SHORT_EDGES)
    return short_edge if short_edge in allowed else min(allowed, key=lambda item: abs(item - short_edge))


def _optimizer_jpeg_bytes(image: Any, short_edge: int) -> bytes:
    """Make an API-only RGB JPEG; originals used by H3 are never altered."""
    from PIL import Image, ImageOps

    normalized = ImageOps.exif_transpose(image)
    if normalized.mode in {"RGBA", "LA"} or (normalized.mode == "P" and "transparency" in normalized.info):
        rgba = normalized.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, "white")
        canvas.paste(rgba, mask=rgba.getchannel("A"))
        normalized = canvas
    elif normalized.mode != "RGB":
        normalized = normalized.convert("RGB")
    width, height = normalized.size
    scale = min(1.0, float(short_edge) / max(1, min(width, height)))
    if scale < 1.0:
        target = (max(1, round(width * scale)), max(1, round(height * scale)))
        normalized = normalized.resize(target, Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    normalized.save(buffer, format="JPEG", quality=PROMPT_OPTIMIZER_JPEG_QUALITY, optimize=True)
    return buffer.getvalue()


def _optimizer_image_jpeg(path: str, short_edge: int) -> bytes:
    from PIL import Image

    with Image.open(path) as image:
        return _optimizer_jpeg_bytes(image, short_edge)


def _optimizer_video_keyframes(path: str) -> list[Any]:
    """Read approximate first/middle/last video frames without sending video bytes."""
    import av

    frames: list[Any] = []
    with av.open(path, mode="r") as container:
        stream = next((item for item in container.streams if item.type == "video"), None)
        if stream is None:
            return frames
        time_base = float(stream.time_base) if stream.time_base else 0.0
        duration = float(stream.duration * stream.time_base) if stream.duration and stream.time_base else 0.0
        if duration <= 0.0 and getattr(container, "duration", None):
            duration = float(container.duration) / float(av.time_base)
        targets = [0.0] if duration <= 0.0 else [0.0, duration * 0.5, max(0.0, duration - 0.05)]
        for timestamp in targets[:PROMPT_OPTIMIZER_VIDEO_SAMPLE_COUNT]:
            try:
                if time_base:
                    container.seek(max(0, int(timestamp / time_base)), stream=stream, backward=True, any_frame=False)
                frame = next(container.decode(stream), None)
                if frame is not None:
                    frames.append(frame.to_image())
            except Exception:
                continue
    return frames


def _optimizer_encoded_image_part(encoded: str, api_format: str) -> dict[str, Any]:
    if api_format == "gemini":
        return {"inlineData": {"mimeType": "image/jpeg", "data": encoded}}
    if api_format == "ollama":
        return {"type": "ollama_image", "data": encoded}
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}}


def _optimizer_media_parts(
    resources: list[Mapping[str, Any]],
    api_format: str,
    reference_short_edge: Any = REF_IMAGE_DEFAULT,
) -> list[dict[str, Any]]:
    """Attach compact visual evidence only; audio stays local to the H3 run."""
    parts: list[dict[str, Any]] = []
    short_edge = _optimizer_reference_short_edge(reference_short_edge)
    for resource in resources[:MAX_MEDIA]:
        asset = resource.get("asset") if isinstance(resource.get("asset"), Mapping) else {}
        path = _optimizer_asset_path(asset)
        media_type = str(resource.get("type") or "").lower()
        if not path or media_type not in {"image", "video", "audio"}:
            continue
        try:
            if media_type == "image":
                encoded = base64.b64encode(_optimizer_image_jpeg(path, short_edge)).decode("ascii")
                parts.append(_optimizer_encoded_image_part(encoded, api_format))
            elif media_type == "video":
                for frame in _optimizer_video_keyframes(path):
                    encoded = base64.b64encode(_optimizer_jpeg_bytes(frame, short_edge)).decode("ascii")
                    parts.append(_optimizer_encoded_image_part(encoded, api_format))
            # Audio has no portable OpenAI-compatible message schema.  Keep it
            # for H3 generation, but do not transmit it to the prompt API.
        except (OSError, ValueError, ImportError):
            continue
    return parts


def _media_counts_from_kwargs(kwargs: Mapping[str, Any]) -> dict[str, int]:
    counts = {"image": 0, "video": 0, "audio": 0}
    for index in range(1, MAX_MEDIA + 1):
        kind = str(kwargs.get(f"media_type_{index}") or "").lower()
        if kind in counts and kwargs.get(f"media_{index}") is not None:
            counts[kind] += 1
    direct = kwargs.get("media")
    if direct is not None:
        counts[_infer_media_type(direct)] += 1
    return counts


def _optimizer_system_prompt(
    scene_guide: str,
    mode: str,
    seconds: float,
    media_counts: Mapping[str, int],
    attached_media_count: int = 0,
    custom_prompt: str = "",
) -> str:
    prompt = _prompt_guide_bundle(scene_guide, mode, seconds, media_counts, custom_prompt)
    actual_count = max(0, int(attached_media_count or 0))
    if actual_count:
        prompt += (
            "\n\n=== MEDIA EVIDENCE RULE ===\n"
            f"Actual media parts attached to this request: {actual_count}.\n"
            "The presence of a media part in the request does not prove that you can perceive it. "
            "Only compact still images are attached to this request. Reference videos are represented by sampled first, middle, and last frames when available; standalone audio and video audio tracks are not attached. "
            "Use visual details only when they are directly observable in those attached images. "
            "Do not invent or confidently describe details for any referenced media that is not actually attached. "
            "For a media tag without corresponding attached evidence, preserve the tag and infer only from the original user prompt and explicit instructions, never from an imagined asset."
        )
    else:
        prompt += (
            "\n\n=== MEDIA EVIDENCE RULE ===\n"
            "No actual media file was attached to this request. Do not invent, hallucinate, or confidently describe the content of any image, video, or audio reference. "
            "Preserve media reference tags when needed, but infer only from the original user prompt and explicit instructions. Never fabricate a subject, appearance, action, setting, sound, or other media detail."
        )
    return prompt


def _optimizer_service_selection(settings: Mapping[str, Any], requested: Any = None) -> tuple[str, str]:
    raw = str(requested or settings.get("active_provider") or "").strip()
    if raw in {"", "disabled", "none"}:
        return "", ""
    provider_id, separator, model = raw.partition("/")
    for item in settings.get("providers", []) if isinstance(settings.get("providers"), list) else []:
        if not isinstance(item, Mapping):
            continue
        if provider_id in {str(item.get("id") or ""), str(item.get("name") or "")}:
            return str(item.get("id") or ""), model.strip() if separator else ""
    if separator:
        return provider_id, model.strip()
    return raw, ""


def _active_optimizer_provider(settings: Mapping[str, Any], requested: Any = None) -> dict[str, Any]:
    provider_id, requested_model = _optimizer_service_selection(settings, requested)
    if not provider_id:
        return {}
    for item in settings.get("providers", []) if isinstance(settings.get("providers"), list) else []:
        if isinstance(item, Mapping) and str(item.get("id")) == provider_id:
            provider = dict(item)
            provider["_requested_model"] = requested_model
            return provider
    return {}


def _optimizer_scheme(settings: Mapping[str, Any], requested: Any = None) -> tuple[str, str]:
    scheme_id = str(requested or settings.get("active_scheme") or "none")
    for item in settings.get("custom_schemes", []) if isinstance(settings.get("custom_schemes"), list) else []:
        if isinstance(item, Mapping) and str(item.get("id")) == scheme_id:
            return "none", str(item.get("prompt") or "")
    builtin_ids = {
        str(item.get("id"))
        for item in (_prompt_guide_manifest().get("scene_guides") or [])
        if isinstance(item, Mapping) and item.get("id")
    }
    return (scheme_id if scheme_id in builtin_ids else "none"), ""


def _optimizer_resources_from_kwargs(kwargs: Mapping[str, Any]) -> list[dict[str, Any]]:
    resources: list[dict[str, Any]] = []
    counts = {"image": 0, "video": 0, "audio": 0}
    for index in range(1, MAX_MEDIA + 1):
        filename = str(kwargs.get(f"media_{index}") or "").strip().replace("\\", "/")
        media_type = str(kwargs.get(f"media_type_{index}") or "").strip().lower()
        if not filename or media_type not in counts:
            continue
        counts[media_type] += 1
        resources.append({
            "type": media_type,
            "tag": {"image": "Picture", "video": "Video", "audio": "Audio"}[media_type]
            + f" {counts[media_type]}",
            "name": filename.rsplit("/", 1)[-1],
            "asset": {"filename": filename, "subfolder": "", "storage": "input"},
        })
    return resources


def _run_configured_prompt_optimizer(
    prompt: str,
    mode: str,
    seconds: float,
    service_model: str,
    scene_guide: str,
    media_counts: Mapping[str, Any] | None = None,
    resources: list[Mapping[str, Any]] | None = None,
    settings: Mapping[str, Any] | None = None,
    reference_short_edge: Any = REF_IMAGE_DEFAULT,
) -> tuple[str, str, str]:
    config = settings if isinstance(settings, Mapping) else _read_prompt_optimizer_config()
    provider = _active_optimizer_provider(config, service_model)
    if not provider:
        raise ValueError("提示词优化服务未启用或已不存在")

    api_key = str(provider.get("api_key") or "")
    api_url = str(provider.get("api_url") or "")
    api_format = str(provider.get("api_format") or "openai").lower()
    if api_format not in {"openai", "gemini", "ollama"}:
        raise ValueError("不支持当前 API 格式")

    raw_counts = media_counts if isinstance(media_counts, Mapping) else {}
    counts = {
        kind: max(0, min(MAX_MEDIA, int(raw_counts.get(kind, 0) or 0)))
        for kind in ("image", "video", "audio")
    }
    resource_items = list(resources or [])
    llm_models = _normalize_model_names(provider.get("llm_models"))
    vlm_models = _normalize_model_names(provider.get("vlm_models"))
    requested_model = str(provider.get("_requested_model") or "").strip()
    configured_models = [*llm_models, *[name for name in vlm_models if name not in llm_models]]
    if requested_model and requested_model not in configured_models:
        raise ValueError("所选模型已不在后台 API 设置中，请重新选择")

    configured_vlm = str(provider.get("vlm_model") or "").strip()
    selected_is_vlm = bool(requested_model and requested_model in vlm_models)
    media_model = requested_model if selected_is_vlm else (configured_vlm if not requested_model else "")
    media_parts = (
        _optimizer_media_parts(resource_items, api_format, reference_short_edge)
        if bool(config.get("read_media")) and media_model
        else []
    )
    model = media_model if media_parts else requested_model or str(provider.get("llm_model") or "").strip()
    requires_key = api_format != "ollama"
    if not str(prompt or "").strip():
        raise ValueError("提示词不能为空")
    if not api_url.strip() or not model or (requires_key and not api_key.strip()):
        raise ValueError("提示词优化 API 设置不完整")

    resolved_scene_guide, custom_prompt = _optimizer_scheme(config, scene_guide)
    result = _optimizer_http_json(
        api_url,
        api_key,
        model,
        api_format,
        _optimizer_system_prompt(
            resolved_scene_guide,
            str(mode or MODE_IMAGE),
            min(MAX_SECONDS, max(MIN_SECONDS, float(seconds))),
            counts,
            len(media_parts),
            custom_prompt,
        ),
        str(prompt or ""),
        media_parts,
        float(provider.get("temperature", 0.7)),
        int(provider.get("max_tokens", 4096)),
        float(provider.get("top_p", 0.9)),
    )
    return result, str(provider.get("id") or ""), model


class MiniMaxH3PromptOptimizer:
    CATEGORY = "FeiHou Easy H3"
    FUNCTION = "optimize"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("optimized_prompt",)
    OUTPUT_NODE = True
    DESCRIPTION = "Optimize a MiniMax H3 prompt with the complete node-adapted Prompt Guide."

    @classmethod
    def INPUT_TYPES(cls):
        manifest = _prompt_guide_manifest()
        scene_items = manifest.get("scene_guides") if isinstance(manifest.get("scene_guides"), list) else []
        choices = [str(item.get("id")) for item in scene_items if isinstance(item, dict) and item.get("id")] or ["none"]
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True, "default": ""}),
                "mode": ([MODE_IMAGE, MODE_REFERENCE], {"default": MODE_IMAGE}),
                "seconds": ("FLOAT", {"default": 10.0, "min": MIN_SECONDS, "max": MAX_SECONDS, "step": 0.1}),
                "scene_guide": (choices, {"default": "none"}),
                "api_format": (["openai", "gemini"], {"default": "openai"}),
                "api_url": ("STRING", {"default": ""}),
                "api_key": ("STRING", {"default": "", "multiline": False, "password": True}),
                "model": ("STRING", {"default": ""}),
            },
        }

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def optimize(self, prompt, mode, seconds, scene_guide, api_format, api_url, api_key, model):
        if not str(api_key or "").strip():
            raise ValueError("Prompt optimization API key is required")
        if not str(model or "").strip():
            raise ValueError("Prompt optimization model is required")
        counts = {"image": 0, "video": 0, "audio": 0}
        system = _optimizer_system_prompt(str(scene_guide or "none"), str(mode or MODE_IMAGE), float(seconds), counts)
        return (_optimizer_http_json(str(api_url), str(api_key), str(model), str(api_format or "openai"), system, str(prompt or "")),)


def _register_prompt_optimizer_route() -> bool:
    try:
        from aiohttp import web
        from server import PromptServer
    except Exception:
        return False
    routes = getattr(getattr(PromptServer, "instance", None), "routes", None)
    if routes is None or getattr(_register_prompt_optimizer_route, "_registered", False):
        return bool(getattr(_register_prompt_optimizer_route, "_registered", False))

    @routes.get("/feihou_easy_h3/prompt_optimizer_settings")
    async def _prompt_optimizer_settings_get(request):
        return web.json_response({"ok": True, "settings": _public_prompt_optimizer_config(_read_prompt_optimizer_config())})

    @routes.post("/feihou_easy_h3/prompt_optimizer_settings")
    async def _prompt_optimizer_settings_post(request):
        try:
            payload = await request.json()
            settings = _write_prompt_optimizer_config(payload if isinstance(payload, dict) else {})
            return web.json_response({"ok": True, "settings": _public_prompt_optimizer_config(settings)})
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    @routes.get("/feihou_easy_h3/providers/{provider_id}/models")
    async def _prompt_optimizer_provider_models(request):
        try:
            provider_id = _safe_config_id(request.match_info.get("provider_id"), "provider")
            settings = _read_prompt_optimizer_config()
            provider = next(
                (item for item in settings.get("providers", []) if isinstance(item, Mapping) and str(item.get("id")) == provider_id),
                None,
            )
            if provider is None:
                return web.json_response({"ok": False, "error": "API 接口不存在"}, status=404)
            models = await asyncio.to_thread(_optimizer_available_models, provider)
            return web.json_response({"ok": True, "models": models})
        except (ValueError, RuntimeError) as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    @routes.get("/feihou_easy_h3/loras")
    async def _feihou_easy_h3_loras(request):
        try:
            return web.json_response({"ok": True, "loras": folder_paths.get_filename_list("loras")})
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    @routes.post("/feihou_easy_h3/prompt_optimize")
    async def _prompt_optimize(request):
        try:
            payload = await request.json()
            prompt = str(payload.get("prompt") or "")
            settings = _read_prompt_optimizer_config()
            requested_service = str(payload.get("service_model") or payload.get("provider_id") or "disabled")
            raw_counts = payload.get("media_counts") if isinstance(payload.get("media_counts"), dict) else {}
            resources = payload.get("resources") if isinstance(payload.get("resources"), list) else []
            result, provider_id, model = await asyncio.to_thread(
                _run_configured_prompt_optimizer,
                prompt,
                str(payload.get("mode") or MODE_IMAGE),
                float(payload.get("seconds") or 10.0),
                requested_service,
                str(payload.get("scene_guide") or "none"),
                raw_counts,
                resources,
                settings,
                payload.get("ref_image_size") or REF_IMAGE_DEFAULT,
            )
            return web.json_response({
                "ok": True,
                "prompt": result,
                "provider_id": provider_id,
                "model": model,
            })
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    _register_prompt_optimizer_route._registered = True
    return True


def _register_prompt_optimizer_route_when_ready() -> None:
    if _register_prompt_optimizer_route():
        return

    def wait_for_server() -> None:
        # ComfyUI creates PromptServer shortly after custom-node imports. Retry
        # for a bounded period without delaying node import.
        for _ in range(2400):
            if _register_prompt_optimizer_route():
                return
            threading.Event().wait(0.05)

    threading.Thread(target=wait_for_server, daemon=True, name="MiniMaxH3PromptOptimizerRoute").start()


def _role_choices(role: str, categories: tuple[str, ...], fallback: str) -> list[str]:
    names = _collect_weight_names(categories)
    selected = [name for name in names if _has_role(name, role)]
    return _sort_model_names(selected) or [fallback]


def _optional_role_choices(role: str, categories: tuple[str, ...]) -> list[str]:
    names = _collect_weight_names(categories)
    selected = _sort_model_names([name for name in names if _has_role(name, role)])
    # ComfyUI validates combo values before invoking the node. The frontend
    # localizes the sentinel to either "None" or "无", so all display values
    # must also be accepted by the server-side combo definition.
    return [*selected, *NONE_MODEL_DISPLAY_VALUES]


def _all_weight_choices(categories: tuple[str, ...], fallback: str, optional: bool = False) -> list[str]:
    """List model files by their ComfyUI folder, never by a filename pattern.

    Community H3 packages are frequently renamed.  The loader exposes the
    relevant ComfyUI model folders verbatim, so users can assign renamed files
    to their actual H3 role themselves.  File compatibility is still checked by
    ComfyUI when that selected file is loaded.
    """
    selected = _sort_model_names(_collect_weight_names(categories))
    if optional:
        return [*selected, *NONE_MODEL_DISPLAY_VALUES]
    return selected or [fallback]


def _filtered_choices(category: str, needles: tuple[str, ...], fallback: str) -> list[str]:
    names = _collect_weight_names((category,))
    selected = [name for name in names if any(needle.lower() in _normalise_model_name(name).replace(" ", "") for needle in needles)]
    return _sort_model_names(selected) or [fallback]


def _model_choices() -> list[str]:
    return _all_weight_choices(("diffusion_models", "unet", "unet_gguf"), "", optional=True)


def _ref_model_choices() -> list[str]:
    return _all_weight_choices(("diffusion_models", "unet", "unet_gguf"), "", optional=True)


def _clip_choices() -> list[str]:
    return _all_weight_choices(("text_encoders", "clip", "clip_gguf"), "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors")


def _vae_choices(needles: tuple[str, ...], fallback: str) -> list[str]:
    return _all_weight_choices(("vae",), fallback)


@lru_cache(maxsize=16)
def _registered_node_class(*names: str):
    """Find an optional custom-node class without importing it unconditionally."""
    mappings = getattr(nodes, "NODE_CLASS_MAPPINGS", {})
    for name in names:
        node_class = mappings.get(name) if hasattr(mappings, "get") else None
        if node_class is not None:
            return node_class
        node_class = getattr(nodes, name, None)
        if node_class is not None:
            return node_class
    for module in tuple(sys.modules.values()):
        if module is None:
            continue
        for name in names:
            node_class = getattr(module, name, None)
            if node_class is not None:
                return node_class
    return None


def _load_gguf_unet(model_name: str):
    loader_class = _registered_node_class("UnetLoaderGGUF", "UNETLoaderGGUF", "UnetLoaderGGUFAdvanced")
    if loader_class is None:
        raise RuntimeError(
            "检测到 GGUF MiniMax H3 主模型，但当前 ComfyUI 未安装 GGUF 加载节点。"
            "请安装 ComfyUI-GGUF 后重启 ComfyUI。"
        )
    loader = loader_class()
    return loader.load_unet(model_name)[0]


def _load_text_encoder(text_encoder: str):
    if not _is_gguf_file(text_encoder):
        return nodes.CLIPLoader().load_clip(text_encoder, "minimax", "default")[0]

    loader_class = _registered_node_class("CLIPLoaderGGUF", "CLIPLoaderGGUFAdvanced")
    if loader_class is None:
        raise RuntimeError(
            "检测到 GGUF MiniMax H3 文本编码器，但当前 ComfyUI 未安装 GGUF 加载节点。"
            "请安装 ComfyUI-GGUF 后重启 ComfyUI。"
        )
    loader = loader_class()
    try:
        return loader.load_clip(text_encoder, "minimax")[0]
    except TypeError:
        return loader.load_clip(text_encoder, type="minimax")[0]


@dataclass
class MiniMaxH3Bundle:
    fl2va_model_name: str
    ref2va_model_name: str
    clip_name: str
    video_vae_name: str
    audio_vae_name: str
    clip: Any
    video_vae: Any
    audio_vae: Any
    lora_stack: tuple[tuple[str, float], ...] = ()
    fl2va_model_obj: Any = None
    ref2va_model_obj: Any = None

    def __post_init__(self) -> None:
        self._model = None
        self._model_kind = ""
        self._model_name = ""
        self._model_cache_key: tuple[str, Any] | None = None
        self._loaded_loras: dict[str, tuple[int, int, Any]] = {}
        self._lock = threading.RLock()

    def _model_name_for(self, kind: str) -> str:
        """Return the preferred model, falling back to the other H3 model.

        FL2VA and REF2VA are exposed as separate choices when both are
        installed, but a user may intentionally install only one of them for
        testing. In that case, let the remaining transformer serve either
        generation path instead of rejecting the mode before execution.
        """
        requested_kind = "ref2va" if kind == "ref2va" else "fl2va"
        preferred = self.ref2va_model_name if requested_kind == "ref2va" else self.fl2va_model_name
        if not _is_none_model(preferred):
            return preferred

        fallback = self.fl2va_model_name if requested_kind == "ref2va" else self.ref2va_model_name
        if not _is_none_model(fallback):
            return fallback

        if requested_kind == "ref2va":
            raise ValueError("Reference Video mode requires at least one MiniMax H3 transformer model.")
        raise ValueError("Text-to-video and I2V or First/Last Frame mode require at least one MiniMax H3 transformer model.")

    def _model_object_for(self, kind: str):
        """Return an already-loaded transformer, falling back to the other role."""
        requested_kind = "ref2va" if kind == "ref2va" else "fl2va"
        preferred = self.ref2va_model_obj if requested_kind == "ref2va" else self.fl2va_model_obj
        if preferred is not None:
            return preferred
        fallback = self.fl2va_model_obj if requested_kind == "ref2va" else self.ref2va_model_obj
        return fallback

    def _load_lora(self, name: str):
        path = folder_paths.get_full_path_or_raise("loras", name)
        stat = os.stat(path)
        signature = (int(stat.st_mtime_ns), int(stat.st_size))
        cached = self._loaded_loras.get(path)
        if cached and cached[:2] == signature:
            return cached[2]
        lora = comfy.utils.load_torch_file(path, safe_load=True)
        self._loaded_loras[path] = (*signature, lora)
        return lora

    def _apply_loras(self, model):
        if not self.lora_stack:
            return model
        loader = getattr(comfy.sd, "load_bypass_lora_for_models", None)
        if not callable(loader):
            raise RuntimeError("当前 ComfyUI 不支持旁路 LoRA 加载，请更新 ComfyUI。")
        result = model
        for name, strength in self.lora_stack:
            lora = self._load_lora(name)
            result, _clip = loader(result, None, lora, float(strength), 0.0)
        return result

    def model_for(self, kind: str):
        kind = "ref2va" if kind == "ref2va" else "fl2va"
        with self._lock:
            supplied_model = self._model_object_for(kind)
            if supplied_model is not None:
                model_name = ""
                cache_key = ("object", id(supplied_model))
            else:
                model_name = self._model_name_for(kind)
                cache_key = ("file", model_name)
            if self._model is not None and self._model_cache_key == cache_key:
                return self._model

            if self._model is not None:
                self._model = None
                self._model_kind = ""
                self._model_name = ""
                self._model_cache_key = None
                comfy.model_management.soft_empty_cache()

            if supplied_model is not None:
                base_model = supplied_model
            elif _is_gguf_file(model_name):
                base_model = _load_gguf_unet(model_name)
            else:
                base_model, = nodes.UNETLoader().load_unet(model_name, "default")
            self._model = self._apply_loras(base_model)
            self._model_kind = kind
            self._model_name = model_name
            self._model_cache_key = cache_key
            return self._model


@dataclass(frozen=True)
class MiniMaxH3Context:
    conditioning: Any
    latent: Any
    video_vae: Any
    audio_vae: Any
    fps: float
    prompt_preview: str


@dataclass(frozen=True)
class _MediaInput:
    input_index: int
    media_type: str
    value: Any


class _AnyType(str):
    def __ne__(self, value: object) -> bool:
        return False


class _FlexibleOptionalInputType(dict):
    def __init__(self, flexible_type: str, data: dict | None = None):
        super().__init__(data or {})
        self.flexible_type = flexible_type
        self.data = data or {}

    def __getitem__(self, key):
        if key in self.data:
            return self.data[key]
        return (self.flexible_type,)

    def __contains__(self, key):
        return True


_ANY_TYPE = _AnyType("*")


def _normalize_lora_stack(value: Any) -> tuple[tuple[str, float], ...]:
    result: list[tuple[str, float]] = []
    for item in list(value or [])[:50]:
        if isinstance(item, Mapping):
            name = str(item.get("lora") or item.get("name") or "").strip()
            enabled = item.get("on", item.get("enabled", True))
            strength = float(item.get("strength", 1.0))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            name = str(item[0] or "").strip()
            enabled = True
            strength = float(item[1])
        else:
            continue
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() in {"1", "true", "yes", "on"}
        if enabled and name and name.lower() != "none" and strength != 0.0:
            result.append((name, strength))
    return tuple(result)


class FeiHouEasyH3LoraStack:
    """Dynamic LoRA stack matching FeiHou LoRA Stack (Merge/Extract)."""

    CATEGORY = "FeiHou Easy H3"
    FUNCTION = "stack"
    RETURN_TYPES = (LORA_STACK_TYPE,)
    RETURN_NAMES = ("lora_stack",)
    DESCRIPTION = "Chainable dynamic LoRA stack consumed by FeiHou Easy H3 Loader."

    @classmethod
    def INPUT_TYPES(cls):
        optional = _FlexibleOptionalInputType(
            _ANY_TYPE,
            {"optional_lora_stack": (LORA_STACK_TYPE,)},
        )
        return {"required": {}, "optional": optional}

    def stack(self, optional_lora_stack=None, **kwargs):
        result = list(_normalize_lora_stack(optional_lora_stack))
        for key, value in kwargs.items():
            if not key.lower().startswith("lora_") or not isinstance(value, Mapping):
                continue
            result.extend(_normalize_lora_stack([value]))
        return (result,)


class FeiHouEasyH3Loader:
    CATEGORY = "FeiHou Easy H3"
    FUNCTION = "load"
    RETURN_TYPES = ("MINIMAX_H3_BUNDLE",)
    RETURN_NAMES = ("h3_bundle",)
    DESCRIPTION = "Load either or both MiniMax H3 transformers, plus the text encoder and both AV VAEs."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "fl2va_model": (_model_choices(),),
                "ref2va_model": (_ref_model_choices(),),
                "text_encoder": (_clip_choices(),),
                "video_vae": (_vae_choices(("minimax_h3_video_vae",), "minimax_h3_video_vae_fp16.safetensors"),),
                "audio_vae": (_vae_choices(("minimax_h3_audio_vae",), "minimax_h3_audio_vae_fp32.safetensors"),),
            },
            "optional": {
                "lora_stack": (LORA_STACK_TYPE,),
            },
        }

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        base = "|".join(str(kwargs.get(key, "")) for key in ("fl2va_model", "ref2va_model", "text_encoder", "video_vae", "audio_vae"))
        return base + "|" + json.dumps(_normalize_lora_stack(kwargs.get("lora_stack")), ensure_ascii=False)

    def load(self, fl2va_model, ref2va_model, text_encoder, video_vae, audio_vae, lora_stack=None):
        if _is_none_model(fl2va_model) and _is_none_model(ref2va_model):
            raise ValueError("Select at least one MiniMax H3 transformer: FL2VA or REF2VA.")
        clip = _load_text_encoder(text_encoder)
        video_vae_obj, = nodes.VAELoader().load_vae(video_vae)
        audio_vae_obj, = nodes.VAELoader().load_vae(audio_vae)
        return (MiniMaxH3Bundle(
            fl2va_model_name=fl2va_model,
            ref2va_model_name=ref2va_model,
            clip_name=text_encoder,
            video_vae_name=video_vae,
            audio_vae_name=audio_vae,
            clip=clip,
            video_vae=video_vae_obj,
            audio_vae=audio_vae_obj,
            lora_stack=_normalize_lora_stack(lora_stack),
        ),)


class FeiHouEasyH3ModelAdapter:
    CATEGORY = "FeiHou Easy H3"
    FUNCTION = "assemble"
    RETURN_TYPES = ("MINIMAX_H3_BUNDLE",)
    RETURN_NAMES = ("h3_bundle",)
    DESCRIPTION = "Assemble standard ComfyUI MODEL, CLIP and VAE outputs into a MiniMax H3 bundle."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text_encoder": ("CLIP",),
                "video_vae": ("VAE",),
                "audio_vae": ("VAE",),
            },
            "optional": {
                "fl2va_model": ("MODEL",),
                "ref2va_model": ("MODEL",),
            },
        }

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    @staticmethod
    def assemble(text_encoder, video_vae, audio_vae, fl2va_model=None, ref2va_model=None):
        if fl2va_model is None and ref2va_model is None:
            raise ValueError("Connect at least one transformer MODEL: FL2VA or REF2VA.")
        return (MiniMaxH3Bundle(
            fl2va_model_name=NONE_MODEL,
            ref2va_model_name=NONE_MODEL,
            clip_name="connected",
            video_vae_name="connected",
            audio_vae_name="connected",
            clip=text_encoder,
            video_vae=video_vae,
            audio_vae=audio_vae,
            fl2va_model_obj=fl2va_model,
            ref2va_model_obj=ref2va_model,
        ),)


def _infer_media_type(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, torch.Tensor):
        return "image"
    if isinstance(value, Mapping) and "waveform" in value:
        return "audio"
    if hasattr(value, "get_components"):
        return "video"
    return "video"


def _embedded_media_path(filename: str) -> str:
    """Resolve an uploaded input-folder file without allowing path escape."""
    value = str(filename or "").strip()
    if not value:
        raise ValueError("Embedded media filename is empty")
    if not folder_paths.exists_annotated_filepath(value):
        raise ValueError(f"Embedded media file does not exist: {value}")
    return folder_paths.get_annotated_filepath(value)


def _load_embedded_media(filename: str, media_type: str) -> Any:
    """Load an embedded gallery selection into ComfyUI's native media type."""
    path = _embedded_media_path(filename)
    if media_type == "image":
        image, _mask = nodes.LoadImage().load_image(filename)
        return image
    if media_type == "video":
        return InputImpl.VideoFromFile(path)
    if media_type == "audio":
        waveform, sample_rate = comfy_audio_nodes.load(path)
        return {"waveform": waveform.unsqueeze(0), "sample_rate": sample_rate}
    raise ValueError(f"Unsupported embedded media type: {media_type}")


def _audio_sample_rate(audio: Mapping) -> int:
    return int(audio.get("sample_rate") or audio.get("samplerate") or audio.get("sampler_rate") or 32000)


def _video_parts(value: Any) -> tuple[torch.Tensor, dict | None, float]:
    if hasattr(value, "get_components"):
        components = value.get_components()
        return components.images, components.audio, float(components.frame_rate or 24.0)
    if isinstance(value, Mapping):
        frames = value.get("images")
        if frames is None:
            frames = value.get("frames")
        if isinstance(frames, torch.Tensor):
            return frames, value.get("audio"), float(value.get("fps") or value.get("frame_rate") or 24.0)
    if isinstance(value, torch.Tensor) and value.ndim == 4:
        return value, None, 24.0
    raise ValueError("Unsupported reference video payload")


def _resample_video_frames(frames: torch.Tensor, source_fps: float) -> torch.Tensor:
    if not source_fps or abs(source_fps - h3.FPS) < 0.01:
        return frames
    count = max(1, round(frames.shape[0] * h3.FPS / source_fps))
    indexes = torch.linspace(0, frames.shape[0] - 1, count, device=frames.device).round().long()
    return frames[indexes]


def _encode_reference_audio(audio_vae, audio: Mapping):
    waveform = audio["waveform"]
    sample_rate = _audio_sample_rate(audio)
    vae_sample_rate = int(getattr(audio_vae, "audio_sample_rate", 32000))
    if sample_rate != vae_sample_rate:
        waveform = torchaudio.functional.resample(waveform, sample_rate, vae_sample_rate)
    latent = audio_vae.encode(waveform[:1].movedim(1, -1))
    return latent, latent.shape[-1]


def _resolve_reference_prompt(
    prompt: str,
    tag_by_input: dict[int, str],
    soundtrack_pairs: list[tuple[int, int]],
    video_count: int,
    standalone_audio_count: int,
) -> str:
    # A workflow may intentionally contain fewer/more @ references than the
    # currently connected media. Resolve valid placeholders, but preserve
    # stale internal placeholders so the user's original reference is not
    # silently discarded; the downstream model decides how to handle it.
    source_prompt = str(prompt or "")
    resolved = REFERENCE_PLACEHOLDER_RE.sub(
        lambda match: tag_by_input.get(int(match.group(1)), ""),
        source_prompt,
    )
    if soundtrack_pairs and (video_count > 1 or standalone_audio_count > 0):
        provenance = [
            f"<Audio {audio_index}> is the synchronized audio track of <Video {video_index}>."
            for audio_index, video_index in soundtrack_pairs
        ]
        return "\n".join((*provenance, resolved))
    return resolved


def _align_canvas_dimension(value: float) -> int:
    return max(h3.CANVAS_MULTIPLE, round(float(value) / h3.CANVAS_MULTIPLE) * h3.CANVAS_MULTIPLE)


def _canvas_dimensions(resolution: str, aspect_ratio: str, custom_width: int, custom_height: int) -> tuple[int, int]:
    if str(resolution) == RESOLUTION_CUSTOM:
        return _align_canvas_dimension(custom_width), _align_canvas_dimension(custom_height)

    megapixels = RESOLUTION_MEGAPIXELS.get(str(resolution), RESOLUTION_MEGAPIXELS[RESOLUTION_480])
    ratio_w, ratio_h = ASPECT_RATIOS.get(str(aspect_ratio), ASPECT_RATIOS[ASPECT_WIDESCREEN])
    total_pixels = megapixels * 1024 * 1024
    scale = math.sqrt(total_pixels / (ratio_w * ratio_h))
    return _align_canvas_dimension(ratio_w * scale), _align_canvas_dimension(ratio_h * scale)


def _frame_length(seconds: float, fps: float) -> int:
    target_frames = max(5.0, float(seconds) * float(fps))
    block_count = max(0, round((target_frames - 5) / 17))
    return block_count * 17 + 5


def _empty_image_conditioning(bundle, prompt, width, height, length, first_frame=None, last_frame=None):
    latent, frame_count = h3._empty_av_latent(width, height, length)
    images = []
    keyframes = []
    if first_frame is not None:
        image = h3._resize(first_frame[:1], width, height, "disabled")
        images.append(image)
        keyframes.append({"resolved_frame_index": 0, "image": image})
    if last_frame is not None:
        image = h3._resize(last_frame[:1], width, height, "center")
        images.append(image)
        keyframes.append({"resolved_frame_index": frame_count - 1, "image": image})

    tokens = bundle.clip.tokenize(prompt, images=images)
    conditioning = bundle.clip.encode_from_tokens_scheduled(tokens)
    if keyframes:
        for keyframe in keyframes:
            keyframe["latent"] = bundle.video_vae.encode(keyframe.pop("image"))
        conditioning = node_helpers.conditioning_set_values(conditioning, {
            "minimax_keyframes": keyframes,
            "minimax_frame_count": frame_count,
        })
    return conditioning, latent


def _reference_conditioning(bundle, prompt, width, height, length, ref_image_size, items: list[_MediaInput]):
    latent, frame_count = h3._empty_av_latent(width, height, length)
    ref_items = []
    ref_blocks = []
    tag_by_input: dict[int, str] = {}
    soundtrack_pairs: list[tuple[int, int]] = []
    images = [item for item in items if item.media_type == "image"]
    videos = [item for item in items if item.media_type == "video"]
    audios = [item for item in items if item.media_type == "audio"]
    audio_ordinal = 0

    # Match the official H3 presentation order: images, videos (with each
    # synchronized soundtrack immediately before its video), standalone audio.
    for picture_ordinal, item in enumerate(images, start=1):
        image = item.value
        if not isinstance(image, torch.Tensor) or image.ndim != 4:
            raise ValueError("Image references must be IMAGE tensors")
        image_h, image_w = image.shape[1], image.shape[2]
        size_mode = str(ref_image_size or REF_IMAGE_DEFAULT)
        legacy_short_edges = {"match": 480, "1k": 1024, "1.5k": 1088, "2k": 1088, "original": 1088}
        try:
            short_edge = int(size_mode)
        except ValueError:
            short_edge = legacy_short_edges.get(size_mode, int(REF_IMAGE_DEFAULT))
        if str(short_edge) not in REFERENCE_SHORT_EDGES:
            short_edge = min((int(value) for value in REFERENCE_SHORT_EDGES), key=lambda value: abs(value - short_edge))
        # Resize proportionally so the selected short edge is authoritative,
        # then choose the nearest H3-compatible 32-pixel canvas.
        scale = short_edge / max(1, min(image_w, image_h))
        target_w, target_h = _reference_aligned_size(image_w, image_h, scale)
        resized = h3._resize(image[:1], target_w, target_h, "disabled")
        ref_items.append({"type": "image", "data": resized})
        ref_blocks.append({"kind": "image", "latent_h": target_h // 16, "latent_w": target_w // 16, "latent": bundle.video_vae.encode(resized)})
        tag_by_input[item.input_index] = f"<Picture {picture_ordinal}>"

    for video_ordinal, item in enumerate(videos, start=1):
        frames, soundtrack, source_fps = _video_parts(item.value)
        frames = _resample_video_frames(frames, source_fps)
        video_h, video_w = frames.shape[1], frames.shape[2]
        canvas_w, canvas_h = h3.adapt_canvas(video_w, video_h)
        if video_w * video_h < canvas_w * canvas_h:
            canvas_w = max(h3.CANVAS_MULTIPLE, round(video_w / h3.CANVAS_MULTIPLE) * h3.CANVAS_MULTIPLE)
            canvas_h = max(h3.CANVAS_MULTIPLE, round(video_h / h3.CANVAS_MULTIPLE) * h3.CANVAS_MULTIPLE)
        frames = h3._resize(frames, canvas_w, canvas_h, "disabled")
        if frames.shape[0] > frame_count:
            frames = frames[:frame_count]
        count = frames.shape[0]
        if count < 5:
            raise ValueError("Reference videos need at least 5 frames")
        while count % 17 != 5:
            count -= 1
        frames = frames[:count]
        video_latent = bundle.video_vae.encode(frames)
        audio_latent = None
        audio_t = 0
        if soundtrack is not None:
            audio_latent, audio_t = _encode_reference_audio(bundle.audio_vae, soundtrack)
            audio_ordinal += 1
            soundtrack_pairs.append((audio_ordinal, video_ordinal))
            ref_items.append({"type": "audio"})
        sample_indexes = list(range(0, frames.shape[0], h3.FPS // 2))
        ref_items.append({
            "type": "video",
            "data": frames[sample_indexes],
            "timestamps": [i / 2.0 for i in range(len(sample_indexes))],
        })
        ref_blocks.append({
            "kind": "video_audio" if audio_t else "video",
            "latent_t": video_latent.shape[2],
            "latent_h": canvas_h // 16,
            "latent_w": canvas_w // 16,
            "ref_audio_t": audio_t,
            "latent": video_latent,
            "audio_latent": audio_latent,
        })
        tag_by_input[item.input_index] = f"<Video {video_ordinal}>"

    for item in audios:
        if not isinstance(item.value, Mapping) or "waveform" not in item.value:
            raise ValueError("Audio references must be AUDIO payloads")
        audio_latent, audio_t = _encode_reference_audio(bundle.audio_vae, item.value)
        audio_ordinal += 1
        ref_items.append({"type": "audio"})
        ref_blocks.append({"kind": "audio", "ref_audio_t": audio_t, "audio_latent": audio_latent})
        tag_by_input[item.input_index] = f"<Audio {audio_ordinal}>"

    if not ref_items or all(item.get("type") == "audio" for item in ref_items):
        raise ValueError("Reference mode needs at least one image or video")

    resolved_prompt = _resolve_reference_prompt(
        prompt,
        tag_by_input,
        soundtrack_pairs,
        len(videos),
        len(audios),
    )

    tokens = bundle.clip.tokenize(resolved_prompt, minimax_ref_items=ref_items)
    conditioning = bundle.clip.encode_from_tokens_scheduled(tokens)
    conditioning = node_helpers.conditioning_set_values(conditioning, {"minimax_refs": ref_blocks})
    return conditioning, latent, resolved_prompt


class FeiHouEasyH3:
    CATEGORY = "FeiHou Easy H3"
    FUNCTION = "generate"
    RETURN_TYPES = ("MODEL", "MINIMAX_H3_CONTEXT")
    RETURN_NAMES = ("model", "h3_context")
    DESCRIPTION = "MiniMax H3 generation with an embedded 9-image, 3-video and 3-audio media gallery."

    @classmethod
    def INPUT_TYPES(cls):
        prompt_schemes, default_prompt_scheme = _prompt_optimizer_scheme_choices()
        prompt_providers = _prompt_optimizer_provider_choices()
        # Old workflows may have saved an empty service while prompt optimization
        # was disabled.  Keep that transport value valid without exposing an
        # extra user-facing “disabled” provider; the Boolean switch controls
        # whether the backend is ever called.
        prompt_provider_values = prompt_providers if "" in prompt_providers else ["", *prompt_providers]
        optional = {}
        for index in range(1, MAX_MEDIA + 1):
            # Transport-only strings populated by the embedded-media frontend.
            # They remain declared for prompt validation, but are stripped from
            # the visible frontend definition so the gallery is the only UI.
            optional[f"media_{index}"] = ("STRING", {"default": "", "hidden": True})
            optional[f"media_type_{index}"] = ("STRING", {"default": "", "hidden": True})
        optional["prompt_optimizer_applied"] = ("BOOLEAN", {"default": False, "hidden": True})
        return {
            "required": {
                "h3_bundle": ("MINIMAX_H3_BUNDLE",),
                "mode": ([MODE_IMAGE, MODE_REFERENCE], {"default": MODE_IMAGE}),
                "prompt": ("STRING", {"multiline": True, "dynamicPrompts": True, "default": ""}),
                "resolution": (list(RESOLUTIONS), {"default": RESOLUTION_480}),
                "aspect_ratio": (list(ASPECT_RATIOS), {"default": ASPECT_WIDESCREEN}),
                "width": ("INT", {"default": 1344, "min": 32, "max": nodes.MAX_RESOLUTION, "step": 32}),
                "height": ("INT", {"default": 768, "min": 32, "max": nodes.MAX_RESOLUTION, "step": 32}),
                "seconds": ("FLOAT", {"default": 10.0, "min": MIN_SECONDS, "max": MAX_SECONDS, "step": 0.1}),
                "advanced": ("BOOLEAN", {"default": False}),
                "fps": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 120.0, "step": 1.0}),
                "keyframe_role": ([KEYFRAME_FIRST, KEYFRAME_LAST], {"default": KEYFRAME_FIRST}),
                "ref_image_size": (list(REFERENCE_SHORT_EDGES), {"default": REF_IMAGE_DEFAULT}),
                "reference_mention_mode": ([REFERENCE_MENTION_FILENAME, REFERENCE_MENTION_INDEX], {"default": REFERENCE_MENTION_INDEX}),
                "prompt_optimizer_enabled": ("BOOLEAN", {"default": False}),
                "prompt_optimizer_provider": (prompt_provider_values, {"default": prompt_providers[0]}),
                "prompt_optimizer_scene_guide": (prompt_schemes, {"default": default_prompt_scheme}),
            },
            "optional": optional,
        }

    @classmethod
    def VALIDATE_INPUTS(cls, prompt_optimizer_provider, prompt_optimizer_scene_guide):
        """Allow saved dynamic optimizer selections to survive settings changes.

        ComfyUI validates combo values before ``generate`` can inspect the
        optimizer switch.  These two lists are populated from user settings,
        so a workflow saved with a removed/renamed API model must still run
        when prompt optimization is off.  When it is on, the optimizer backend
        performs its own current-service validation with a useful error.
        """
        return True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    @staticmethod
    def _collect_media(kwargs: dict) -> list[_MediaInput]:
        items = []
        for index in range(1, MAX_MEDIA + 1):
            value = kwargs.get(f"media_{index}")
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            media_type = str(kwargs.get(f"media_type_{index}") or "").strip().lower()
            resolved_type = media_type if media_type in {"image", "video", "audio"} else _infer_media_type(value)
            if isinstance(value, str):
                value = _load_embedded_media(value, resolved_type)
            items.append(_MediaInput(index, resolved_type, value))
        return items

    @staticmethod
    def _keyframes(items, role):
        images = [item.value for item in items if item.media_type == "image"]
        if any(item.media_type != "image" for item in items):
            raise ValueError("Image mode accepts image resources only")
        if len(images) > 2:
            raise ValueError("Image mode accepts at most two images")
        if not images:
            return None, None
        if len(images) == 1:
            if role == KEYFRAME_LAST:
                return None, images[0]
            return images[0], None
        if role == KEYFRAME_LAST:
            return images[1], images[0]
        return images[0], images[1]

    @classmethod
    def generate(cls, h3_bundle, mode, prompt, resolution, aspect_ratio, width, height, seconds, advanced, fps, keyframe_role, ref_image_size, reference_mention_mode, prompt_optimizer_enabled=False, prompt_optimizer_provider="", prompt_optimizer_scene_guide="none", prompt_optimizer_applied=False, **kwargs):
        if not isinstance(h3_bundle, MiniMaxH3Bundle):
            raise ValueError("Connect a FeiHou Easy H3 Loader bundle")
        mode = str(mode)
        keyframe_role = KEYFRAME_LAST if str(keyframe_role) == KEYFRAME_LAST else KEYFRAME_FIRST
        width, height = _canvas_dimensions(resolution, aspect_ratio, width, height)
        seconds = min(MAX_SECONDS, max(MIN_SECONDS, float(seconds)))
        length = _frame_length(seconds, fps)
        optimizer_enabled = _as_bool(advanced) and _as_bool(prompt_optimizer_enabled)
        optimizer_already_applied = prompt_optimizer_applied is True or str(prompt_optimizer_applied).strip().lower() in {"1", "true", "yes", "on"}
        if optimizer_enabled and not optimizer_already_applied:
            try:
                prompt, _provider_id, _model = _run_configured_prompt_optimizer(
                    str(prompt or ""),
                    mode,
                    seconds,
                    str(prompt_optimizer_provider),
                    str(prompt_optimizer_scene_guide or "none"),
                    _media_counts_from_kwargs(kwargs),
                    _optimizer_resources_from_kwargs(kwargs),
                    reference_short_edge=ref_image_size,
                )
            except Exception as exc:
                raise RuntimeError(f"Easy H3 提示词扩写/反推失败: {exc}") from exc
        items = cls._collect_media(kwargs)
        if mode == MODE_REFERENCE and items:
            if len(items) > MAX_MEDIA:
                raise ValueError("Reference mode accepts at most fifteen media resources")
            counts = {"image": 0, "video": 0, "audio": 0}
            for item in items:
                if item.media_type not in counts:
                    raise ValueError("Unsupported media resource")
                counts[item.media_type] += 1
            if counts["image"] > MAX_IMAGES or counts["video"] > MAX_VIDEOS or counts["audio"] > MAX_AUDIOS:
                raise ValueError("Reference mode media limits are 9 images, 3 videos and 3 audio clips")
            if counts["image"] == 0 and counts["video"] == 0:
                raise ValueError("Reference mode needs an image or video in addition to audio")
            model = h3_bundle.model_for("ref2va")
            conditioning, latent, prompt_preview = _reference_conditioning(h3_bundle, prompt, width, height, length, ref_image_size, items)
        else:
            first_frame, last_frame = cls._keyframes(items, keyframe_role)
            model = h3_bundle.model_for("fl2va")
            conditioning, latent = _empty_image_conditioning(h3_bundle, prompt, width, height, length, first_frame, last_frame)
            prompt_preview = str(prompt or "")
        context = MiniMaxH3Context(
            conditioning=conditioning,
            latent=latent,
            video_vae=h3_bundle.video_vae,
            audio_vae=h3_bundle.audio_vae,
            fps=float(fps),
            prompt_preview=prompt_preview,
        )
        return model, context


class FeiHouEasyH3Output:
    CATEGORY = "FeiHou Easy H3"
    FUNCTION = "unpack"
    RETURN_TYPES = ("CONDITIONING", "LATENT", "VAE", "VAE", "FLOAT", "STRING")
    RETURN_NAMES = ("positive", "latent", "video_vae", "audio_vae", "fps", "prompt_preview")
    DESCRIPTION = "Unpack the non-model outputs from a FeiHou Easy H3 context."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "h3_context": ("MINIMAX_H3_CONTEXT",),
            },
        }

    @staticmethod
    def unpack(h3_context):
        if not isinstance(h3_context, MiniMaxH3Context):
            raise ValueError("Connect the H3 Context output from a FeiHou Easy H3 node")
        return (
            h3_context.conditioning,
            h3_context.latent,
            h3_context.video_vae,
            h3_context.audio_vae,
            h3_context.fps,
            h3_context.prompt_preview,
        )


class FeiHouEasyH3PromptPreview:
    CATEGORY = "FeiHou Easy H3"
    FUNCTION = "preview"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    OUTPUT_NODE = True
    DESCRIPTION = "Preview the final expanded or media-inferred prompt carried by H3 Context."

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"text": ("STRING", {"forceInput": True})}}

    @staticmethod
    def preview(text):
        value = str(text or "")
        return {"ui": {"text": [value]}, "result": (value,)}


_register_prompt_optimizer_route_when_ready()


NODE_CLASS_MAPPINGS = {
    "FeiHouEasyH3LoraStack": FeiHouEasyH3LoraStack,
    "FeiHouEasyH3Loader": FeiHouEasyH3Loader,
    "FeiHouEasyH3ModelAdapter": FeiHouEasyH3ModelAdapter,
    "FeiHouEasyH3": FeiHouEasyH3,
    "FeiHouEasyH3Output": FeiHouEasyH3Output,
    "FeiHouEasyH3PromptPreview": FeiHouEasyH3PromptPreview,
}
