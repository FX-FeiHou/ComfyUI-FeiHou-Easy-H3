# SPDX-License-Identifier: GPL-3.0-or-later
# Adapted for ComfyUI-FeiHou-Easy-H3 from comfyui-minimax-h3-audio-T8
# (h3_t8/learned_latent_upscale_advanced.py, h3_t8/latent_upscale.py), Copyright (C) 2026 MiniMax H3 Audio T8 contributors,
# distributed under GNU GPL-3.0-or-later (see LICENSES/). Changes: module
# layout, imports and user-visible names only; numerical behavior is unchanged.
from __future__ import annotations

import gc
import hashlib
import json
import math
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F

import comfy.model_management as model_management
import comfy.model_patcher
import comfy.nested_tensor
import comfy.samplers
import comfy.utils
import folder_paths

from .h3_dual_clock import shift_sigma


def _resize_mask_tensor(
    mask: torch.Tensor,
    source_width: int,
    source_height: int,
    output_width: int,
    output_height: int,
) -> tuple[torch.Tensor, str]:
    if not isinstance(mask, torch.Tensor) or mask.ndim < 2:
        return mask, "preserved_non_tensor_or_rank_lt_2"
    if tuple(mask.shape[-2:]) != (source_height, source_width):
        return mask, "preserved_nonmatching_spatial_shape"
    if (source_width, source_height) == (output_width, output_height):
        return mask, "preserved_noop"

    original_dtype = mask.dtype
    if mask.ndim == 2:
        work = mask.unsqueeze(0).unsqueeze(0)
        restore_shape = "hw"
    elif mask.ndim == 3:
        work = mask.unsqueeze(1)
        restore_shape = "bhw"
    else:
        work = mask
        restore_shape = "unchanged"
    if not torch.is_floating_point(work):
        work = work.to(torch.float32)
    resized = comfy.utils.common_upscale(
        work,
        output_width,
        output_height,
        "nearest-exact",
        "disabled",
    )
    if restore_shape == "hw":
        resized = resized[0, 0]
    elif restore_shape == "bhw":
        resized = resized[:, 0]
    if original_dtype == torch.bool:
        resized = resized >= 0.5
    elif resized.dtype != original_dtype:
        resized = resized.round().to(original_dtype)
    return resized, "resized_nearest_exact"


def _nested_parts(value: Any, field_name: str) -> tuple[torch.Tensor, torch.Tensor]:
    if not getattr(value, "is_nested", False):
        raise ValueError(f"{field_name} is not a nested tensor")
    parts = tuple(value.unbind())
    if len(parts) != 2:
        raise ValueError(
            f"Expected {field_name} to contain exactly video and audio parts, got {len(parts)}"
        )
    video, audio = parts
    if not isinstance(video, torch.Tensor) or video.ndim != 5:
        raise ValueError(
            f"Expected nested H3 video latent [B,C,T,H,W], got {getattr(video, 'shape', None)}"
        )
    if not isinstance(audio, torch.Tensor) or audio.ndim != 4:
        raise ValueError(
            f"Expected nested H3 audio latent [B,C,F,T], got {getattr(audio, 'shape', None)}"
        )
    return video, audio



PIXELS_PER_H3_LATENT = 16
PIXEL_ALIGNMENT = 32
H3_OFFICIAL_REFERENCE_PIXELS = 1920 * 1088
KNOWN_MODEL_SHA256 = "043e5a48e161610ef6c3ea974645220354d06fa618abca15f76d084812eb55c2"
SIZE_MODES = ("scale_by", "target_megapixels", "target_dimensions")
ASPECT_POLICIES = ("preserve_source", "honor_dimensions_exp")
PRECISIONS = ("fp16", "bf16", "fp32")
RELEASE_POLICIES = ("offload_after", "clear_after", "keep_loaded")
AUDIO_POLICIES = ("auto", "first_pass", "highres_template")
SECOND_PASS_AUDIO_SOURCES = ("legacy_policy", "first_pass", "highres_template")
UPSTREAM_WORKFLOW_COMMIT = "64fc9d4c7e2c03e8c61d6886182e3309365a1962"
UPSTREAM_REFINE_VIDEO_SIGMAS = {
    3: (0.9035, 0.6316, 0.3158, 0.0),
    4: (0.9035, 0.8000, 0.6316, 0.3158, 0.0),
    5: (0.9231, 0.8780, 0.8000, 0.6316, 0.3158, 0.0),
}


LATENTS_MEAN = (
    0.858090341091156,
    -0.9606591463088989,
    1.0661640167236328,
    -0.5090325474739075,
    -0.2727581858634949,
    -1.3675414323806763,
    -0.2553254961967468,
    -0.26907554268836975,
    -0.5376840829849243,
    -0.0464097298681736,
    0.6657370328903198,
    0.19690127670764923,
    -0.5460608005523682,
    -0.4035342037677765,
    -0.23683024942874908,
    0.25928452610969543,
    -0.30133944749832153,
    0.211341992020607,
    -1.1206848621368408,
    0.3581933379173279,
    -0.04225143790245056,
    0.2604829967021942,
    0.22864092886447906,
    0.7056031823158264,
)
LATENTS_STD = (
    1.2223774194717407,
    1.2767263650894165,
    1.6831774711608887,
    1.7549455165863037,
    1.5636216402053833,
    2.194143533706665,
    0.9653137922286987,
    1.0569885969161987,
    0.841948926448822,
    0.7729952931404114,
    1.8955937623977661,
    0.946841835975647,
    0.7996809482574463,
    0.44988900423049927,
    0.7197399735450745,
    0.6936293244361877,
    2.961095094680786,
    2.7694199085235596,
    3.0496184825897217,
    2.1088054180145264,
    3.276226282119751,
    3.1627357006073,
    2.2816812992095947,
    2.6127843856811523,
)


def _group_norm(channels: int) -> nn.GroupNorm:
    return nn.GroupNorm(32, channels)


class _ResBlockEmb3D(nn.Module):
    def __init__(self, channels: int = 512, embed_dim: int = 64):
        super().__init__()
        self.in_layers = nn.Sequential(
            _group_norm(channels),
            nn.SiLU(),
            nn.Conv3d(channels, channels, 3, padding=1),
        )
        self.emb_layers = nn.Sequential(nn.SiLU(), nn.Linear(embed_dim, channels * 2))
        self.out_norm = _group_norm(channels)
        self.out_layers = nn.Sequential(
            nn.SiLU(),
            nn.Dropout(0.1),
            nn.Conv3d(channels, channels, 3, padding=1),
        )

    def forward(self, x: torch.Tensor, embedding: torch.Tensor) -> torch.Tensor:
        hidden = self.in_layers(x)
        scale, shift = self.emb_layers(embedding).chunk(2, dim=1)
        scale = scale[:, :, None, None, None]
        shift = shift[:, :, None, None, None]
        hidden = self.out_norm(hidden) * (1.0 + scale) + shift
        return x + self.out_layers(hidden)


class _TemporalConv(nn.Module):
    def __init__(self, channels: int = 512):
        super().__init__()
        self.norm = _group_norm(channels)
        self.dwconv = nn.Conv3d(
            channels,
            channels,
            kernel_size=(5, 1, 1),
            padding=(2, 0, 0),
            groups=channels,
        )
        self.pwconv = nn.Conv3d(channels, channels, kernel_size=1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x + self.pwconv(self.dwconv(F.silu(self.norm(x))))


class MiniMaxH3LearnedResizer3D(nn.Module):
    """Network reconstructed from the published 3D checkpoint tensor contract."""

    def __init__(self):
        super().__init__()
        self.conv_in = nn.Conv3d(24, 512, 3, padding=1)
        self.embed = nn.Sequential(nn.Linear(1, 64), nn.SiLU(), nn.Linear(64, 64))
        self.in_blocks = self._make_blocks()
        self.out_blocks = self._make_blocks()
        self.norm_out = _group_norm(512)
        self.conv_out = nn.Conv3d(512, 24, 3, padding=1)
        self.device = torch.device("cpu")

    @staticmethod
    def _make_blocks() -> nn.ModuleList:
        blocks: list[nn.Module] = []
        for index in range(12):
            blocks.append(_ResBlockEmb3D())
            if index % 2 == 0:
                blocks.append(_TemporalConv())
        return nn.ModuleList(blocks)

    @staticmethod
    def _run_blocks(
        hidden: torch.Tensor,
        embedding: torch.Tensor,
        blocks: nn.ModuleList,
    ) -> torch.Tensor:
        for block in blocks:
            if isinstance(block, _ResBlockEmb3D):
                hidden = block(hidden, embedding)
            else:
                hidden = block(hidden)
        return hidden

    def forward(
        self,
        latent: torch.Tensor,
        effective_scale: float,
        target_size: tuple[int, int, int],
    ) -> torch.Tensor:
        if tuple(latent.shape[-3:]) == tuple(target_size):
            return latent
        scale_value = latent.new_tensor([[float(effective_scale) - 1.0]])
        embedding = self.embed(scale_value).expand(latent.shape[0], -1)
        hidden = self.conv_in(latent)
        hidden = self._run_blocks(hidden, embedding, self.in_blocks)
        hidden = F.interpolate(
            hidden,
            size=target_size,
            mode="trilinear",
            align_corners=False,
        )
        hidden = self._run_blocks(hidden, embedding, self.out_blocks)
        return self.conv_out(F.silu(self.norm_out(hidden)))


def _precision_dtype(precision: str) -> torch.dtype:
    mapping = {"fp16": torch.float16, "bf16": torch.bfloat16, "fp32": torch.float32}
    try:
        return mapping[precision]
    except KeyError as exc:
        raise ValueError(f"Unknown precision {precision!r}; expected one of {PRECISIONS}") from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expected_state_contract() -> dict[str, tuple[int, ...]]:
    with torch.device("meta"):
        model = MiniMaxH3LearnedResizer3D()
    return {key: tuple(value.shape) for key, value in model.state_dict().items()}


EXPECTED_STATE_CONTRACT = _expected_state_contract()


def validate_learned_resizer_state_dict(
    state_dict: dict[str, torch.Tensor],
) -> dict[str, Any]:
    actual = set(state_dict)
    expected = set(EXPECTED_STATE_CONTRACT)
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    shape_mismatches = []
    for key in sorted(actual & expected):
        if tuple(state_dict[key].shape) != EXPECTED_STATE_CONTRACT[key]:
            shape_mismatches.append(
                {
                    "key": key,
                    "expected": EXPECTED_STATE_CONTRACT[key],
                    "actual": tuple(state_dict[key].shape),
                }
            )
    dtype_counts: dict[str, int] = {}
    for value in state_dict.values():
        dtype_counts[str(value.dtype)] = dtype_counts.get(str(value.dtype), 0) + 1
    if missing or unexpected or shape_mismatches or len(state_dict) != 322:
        raise ValueError(
            "Selected file is not the supported MiniMax H3 3D latent-upscaler checkpoint: "
            f"keys={len(state_dict)}, missing={missing[:4]}, unexpected={unexpected[:4]}, "
            f"shape_mismatches={shape_mismatches[:2]}"
        )
    if any(not torch.is_floating_point(value) for value in state_dict.values()):
        raise ValueError("The learned H3 latent-upscaler checkpoint must contain only floating tensors")
    return {
        "tensor_count": len(state_dict),
        "dtype_counts": dtype_counts,
        "channels": 24,
        "base_channels": 512,
        "residual_blocks_per_side": 12,
        "temporal_kernel": 5,
    }


@dataclass
class _CachedModel:
    patcher: comfy.model_patcher.ModelPatcher
    path: Path
    sha256: str
    precision: str
    contract: dict[str, Any]


_MODEL_CACHE: dict[tuple[str, int, int, str], _CachedModel] = {}
_MODEL_CACHE_LOCK = threading.RLock()


def _cache_key(path: Path, precision: str) -> tuple[str, int, int, str]:
    stat = path.stat()
    return str(path.resolve()).casefold(), int(stat.st_size), int(stat.st_mtime_ns), precision


def _drop_cached_entry(entry: _CachedModel) -> None:
    with _MODEL_CACHE_LOCK:
        for key, value in list(_MODEL_CACHE.items()):
            if value is entry:
                del _MODEL_CACHE[key]


def clear_learned_resizer_cache() -> int:
    with _MODEL_CACHE_LOCK:
        entries = list(_MODEL_CACHE.values())
        _MODEL_CACHE.clear()
    for entry in entries:
        model_management.unload_model_and_clones(entry.patcher)
    gc.collect()
    model_management.soft_empty_cache()
    return len(entries)


def _load_cached_model(model_name: str, precision: str) -> tuple[_CachedModel, bool]:
    model_path = Path(folder_paths.get_full_path_or_raise("latent_upscale_models", model_name))
    key = _cache_key(model_path, precision)
    with _MODEL_CACHE_LOCK:
        cached = _MODEL_CACHE.get(key)
        if cached is not None:
            return cached, True

        file_hash = _sha256_file(model_path)
        state_dict = comfy.utils.load_torch_file(str(model_path), safe_load=True)
        try:
            contract = validate_learned_resizer_state_dict(state_dict)
            contract["state_contract_match"] = True
        except Exception as error:
            # User-selected model identity is diagnostic only.  The actual
            # architecture load below remains authoritative and may raise its
            # native PyTorch error when the file is incompatible.
            contract = {
                "state_contract_match": False,
                "state_contract_diagnostic": f"{type(error).__name__}: {error}",
            }
        contract["reference_file_match"] = file_hash.casefold() == KNOWN_MODEL_SHA256
        contract["model_identity_policy"] = "diagnostic_only_not_a_load_gate"
        with torch.device("meta"):
            network = MiniMaxH3LearnedResizer3D()
        network.load_state_dict(state_dict, strict=True, assign=True)
        del state_dict
        dtype = _precision_dtype(precision)
        if next(network.parameters()).dtype != dtype:
            network.to(device=torch.device("cpu"), dtype=dtype)
        network.eval()
        load_device = model_management.get_torch_device()
        offload_device = model_management.unet_offload_device()
        patcher = comfy.model_patcher.CoreModelPatcher(
            network,
            load_device=load_device,
            offload_device=offload_device,
        )
        entry = _CachedModel(
            patcher=patcher,
            path=model_path,
            sha256=file_hash,
            precision=precision,
            contract=contract,
        )
        _MODEL_CACHE[key] = entry
        return entry, False


def _round_multiple(value: float, multiple: int = PIXEL_ALIGNMENT) -> int:
    return max(multiple, int(math.floor(value / multiple + 0.5)) * multiple)


def _best_aligned_size(
    ideal_width: float,
    ideal_height: float,
    source_aspect: float,
) -> tuple[int, int]:
    width_floor = max(PIXEL_ALIGNMENT, math.floor(ideal_width / PIXEL_ALIGNMENT) * PIXEL_ALIGNMENT)
    width_ceil = max(PIXEL_ALIGNMENT, math.ceil(ideal_width / PIXEL_ALIGNMENT) * PIXEL_ALIGNMENT)
    height_floor = max(PIXEL_ALIGNMENT, math.floor(ideal_height / PIXEL_ALIGNMENT) * PIXEL_ALIGNMENT)
    height_ceil = max(PIXEL_ALIGNMENT, math.ceil(ideal_height / PIXEL_ALIGNMENT) * PIXEL_ALIGNMENT)

    def score(size: tuple[int, int]) -> tuple[float, float]:
        width, height = size
        aspect_error = abs(math.log((width / height) / source_aspect))
        size_error = math.hypot(
            (width - ideal_width) / max(ideal_width, 1.0),
            (height - ideal_height) / max(ideal_height, 1.0),
        )
        return aspect_error, size_error

    return min(
        (
            (width, height)
            for width in {width_floor, width_ceil}
            for height in {height_floor, height_ceil}
        ),
        key=score,
    )


def learned_upscale_geometry(
    source_latent_width: int,
    source_latent_height: int,
    size_mode: str,
    scale_by: float,
    target_megapixels: float,
    target_width: int,
    target_height: int,
    aspect_policy: str,
    max_anisotropy: float,
) -> dict[str, Any]:
    if size_mode not in SIZE_MODES:
        raise ValueError(f"Unknown size_mode {size_mode!r}; expected one of {SIZE_MODES}")
    if aspect_policy not in ASPECT_POLICIES:
        raise ValueError(f"Unknown aspect_policy {aspect_policy!r}; expected one of {ASPECT_POLICIES}")
    source_width = int(source_latent_width) * PIXELS_PER_H3_LATENT
    source_height = int(source_latent_height) * PIXELS_PER_H3_LATENT
    source_aspect = source_width / source_height
    if size_mode == "scale_by":
        if not math.isfinite(scale_by) or not 1.0 <= float(scale_by) <= 4.0:
            raise ValueError("scale_by must be finite and within [1.0, 4.0]")
        ideal_width = source_width * float(scale_by)
        ideal_height = source_height * float(scale_by)
        output_width, output_height = _best_aligned_size(
            ideal_width, ideal_height, source_aspect
        )
    elif size_mode == "target_megapixels" or aspect_policy == "preserve_source":
        if size_mode == "target_megapixels":
            target_area = float(target_megapixels) * 1_000_000.0
            if not math.isfinite(target_area) or target_area <= 0:
                raise ValueError("target_megapixels must be positive and finite")
        else:
            target_area = float(target_width) * float(target_height)
        ideal_width = math.sqrt(target_area * source_aspect)
        ideal_height = ideal_width / source_aspect
        output_width, output_height = _best_aligned_size(
            ideal_width, ideal_height, source_aspect
        )
    else:
        ideal_width = float(target_width)
        ideal_height = float(target_height)
        output_width = _round_multiple(ideal_width)
        output_height = _round_multiple(ideal_height)

    if output_width < source_width or output_height < source_height:
        raise ValueError(
            "Learned latent resize only supports non-shrinking geometry: "
            f"source={source_width}x{source_height}, target={output_width}x{output_height}"
        )
    scale_x = output_width / source_width
    scale_y = output_height / source_height
    if max(scale_x, scale_y) > 4.0:
        raise ValueError("The learned model is limited to at most 4x on either spatial axis")
    anisotropy = max(scale_x, scale_y) / min(scale_x, scale_y)
    if not math.isfinite(max_anisotropy) or not 1.0 <= float(max_anisotropy) <= 2.0:
        raise ValueError("max_anisotropy must be finite and within [1.0, 2.0]")
    if anisotropy > float(max_anisotropy):
        raise ValueError(
            f"Requested anisotropic scale {scale_x:.5f}x{scale_y:.5f} has ratio "
            f"{anisotropy:.5f}, above max_anisotropy={float(max_anisotropy):.5f}"
        )
    output_pixels = output_width * output_height
    exceeds_official_reference_area = output_pixels > H3_OFFICIAL_REFERENCE_PIXELS
    return {
        "source_width": source_width,
        "source_height": source_height,
        "output_width": output_width,
        "output_height": output_height,
        "output_latent_width": output_width // PIXELS_PER_H3_LATENT,
        "output_latent_height": output_height // PIXELS_PER_H3_LATENT,
        "scale_x": scale_x,
        "scale_y": scale_y,
        "effective_scale": math.sqrt(scale_x * scale_y),
        "anisotropy": anisotropy,
        "aspect_error_percent": abs((output_width / output_height) / source_aspect - 1.0)
        * 100.0,
        "size_mode": size_mode,
        "aspect_policy": aspect_policy,
        "output_pixels": output_pixels,
        "official_reference_pixels": H3_OFFICIAL_REFERENCE_PIXELS,
        "exceeds_official_reference_area": exceeds_official_reference_area,
        "memory_warning": (
            "Output exceeds the 1920x1088 official reference area. Execution is allowed; "
            "the user is responsible for VRAM, host-memory, runtime, and output validation."
            if exceeds_official_reference_area
            else None
        ),
    }


def _memory_snapshot(device: torch.device) -> dict[str, float | str | None]:
    snapshot: dict[str, float | str | None] = {"device": str(device)}
    try:
        snapshot["free_mib"] = float(model_management.get_free_memory(device)) / (1024**2)
    except Exception:
        snapshot["free_mib"] = None
    if device.type == "cuda" and torch.cuda.is_available():
        snapshot["allocated_mib"] = torch.cuda.memory_allocated(device) / (1024**2)
        snapshot["reserved_mib"] = torch.cuda.memory_reserved(device) / (1024**2)
    return snapshot


def _nested_mask_parts(value) -> tuple[torch.Tensor, torch.Tensor]:
    """Read native H3 mask metadata without imposing sample-latent ranks.

    ComfyUI's SetLatentNoiseMask stores a video mask as pixel-space
    ``[frames,1,H,W]`` while already-normalized H3 routes may store
    ``[B,1,T,H,W]``.  Both are valid noise-mask metadata; only ``samples`` must
    satisfy the strict native H3 video/audio latent shapes.
    """

    if not getattr(value, "is_nested", False):
        raise ValueError("noise_mask must be a nested H3 video/audio tensor")
    parts = tuple(value.unbind())
    if len(parts) != 2 or not all(isinstance(part, torch.Tensor) for part in parts):
        raise ValueError("noise_mask must contain exactly video and audio tensors")
    return parts


def learned_upscale_h3_av_latent(
    latent: dict,
    model_name: str,
    size_mode: str,
    scale_by: float,
    target_megapixels: float,
    target_width: int,
    target_height: int,
    aspect_policy: str,
    max_anisotropy: float,
    precision: str,
    release_policy: str,
) -> tuple[dict, int, int, str]:
    if release_policy not in RELEASE_POLICIES:
        raise ValueError(
            f"Unknown release_policy {release_policy!r}; expected one of {RELEASE_POLICIES}"
        )
    if not isinstance(latent, dict) or "samples" not in latent:
        raise ValueError("Expected a MiniMax H3 LATENT dictionary containing samples")
    video, audio = _nested_parts(latent["samples"], "samples")
    if tuple(video.shape[:2]) != (1, 24) or tuple(audio.shape[:3]) != (1, 32, 2):
        raise ValueError(
            "Learned H3 latent upscale currently requires batch-1 native H3 AV shapes; "
            f"video={tuple(video.shape)}, audio={tuple(audio.shape)}"
        )
    if not torch.isfinite(video).all() or not torch.isfinite(audio).all():
        raise ValueError("Input H3 AV latent contains NaN or Inf")
    geometry = learned_upscale_geometry(
        int(video.shape[-1]),
        int(video.shape[-2]),
        size_mode,
        scale_by,
        target_megapixels,
        target_width,
        target_height,
        aspect_policy,
        max_anisotropy,
    )
    if tuple(video.shape[-2:]) == (
        geometry["output_latent_height"],
        geometry["output_latent_width"],
    ):
        report = {
            "schema_version": 1,
            "node": "FeiHouEasyH3LearnedLatentUpscale",
            "status": "noop",
            "geometry": geometry,
            "model_loaded": False,
            "audio_preserved": True,
        }
        return (
            latent,
            int(geometry["output_width"]),
            int(geometry["output_height"]),
            json.dumps(report, ensure_ascii=False, sort_keys=True),
        )

    entry: _CachedModel | None = None
    cache_hit = False
    failed = True
    released = False
    cache_cleared = False
    device = model_management.get_torch_device()
    before = _memory_snapshot(device)
    try:
        entry, cache_hit = _load_cached_model(model_name, precision)
        target_h = int(geometry["output_latent_height"])
        target_w = int(geometry["output_latent_width"])
        dtype = _precision_dtype(precision)
        activation_estimate = (
            int(video.shape[0])
            * 512
            * int(video.shape[2])
            * target_h
            * target_w
            * torch.empty((), dtype=dtype).element_size()
            * 4
        )
        model_management.load_models_gpu(
            [entry.patcher],
            memory_required=activation_estimate,
            force_full_load=True,
        )
        compute_device = entry.patcher.load_device
        work = video.to(device=compute_device, dtype=dtype)
        mean = work.new_tensor(LATENTS_MEAN).view(1, 24, 1, 1, 1)
        std = work.new_tensor(LATENTS_STD).view(1, 24, 1, 1, 1)
        with torch.inference_mode():
            normalized = (work - mean) / std
            output_video = entry.patcher.model(
                normalized,
                float(geometry["effective_scale"]),
                (int(video.shape[2]), target_h, target_w),
            )
            output_video = output_video * std + mean
        if not torch.isfinite(output_video).all():
            raise RuntimeError("Learned H3 latent upscaler produced NaN or Inf")
        output_video = output_video.to(
            device=model_management.intermediate_device(), dtype=video.dtype
        )
        del work, normalized, mean, std
        output = latent.copy()
        output["samples"] = comfy.nested_tensor.NestedTensor((output_video, audio))
        mask_status = "absent"
        if latent.get("noise_mask") is not None:
            video_mask, audio_mask = _nested_mask_parts(latent["noise_mask"])
            resized_mask, mask_status = _resize_mask_tensor(
                video_mask,
                int(video.shape[-1]),
                int(video.shape[-2]),
                target_w,
                target_h,
            )
            output["noise_mask"] = comfy.nested_tensor.NestedTensor(
                (resized_mask, audio_mask)
            )
        failed = False
        if release_policy != "keep_loaded":
            model_management.unload_model_and_clones(entry.patcher)
            released = True
        if release_policy == "clear_after":
            _drop_cached_entry(entry)
            cache_cleared = True
            gc.collect()
            model_management.soft_empty_cache()
        after = _memory_snapshot(device)
        report = {
            "schema_version": 1,
            "node": "FeiHouEasyH3LearnedLatentUpscale",
            "status": "ok",
            "model": {
                "name": model_name,
                "path": str(entry.path),
                "sha256": entry.sha256,
                "precision": precision,
                "cache_hit": cache_hit,
                "contract": entry.contract,
            },
            "geometry": geometry,
            "source_video_shape": list(video.shape),
            "output_video_shape": list(output_video.shape),
            "audio_shape": list(audio.shape),
            "audio_preserved": True,
            "noise_mask": mask_status,
            "release_policy": release_policy,
            "gpu_weights_released": released,
            "cpu_cache_cleared": cache_cleared,
            "memory_before": before,
            "memory_after_release": after,
        }
        return (
            output,
            int(geometry["output_width"]),
            int(geometry["output_height"]),
            json.dumps(report, ensure_ascii=False, sort_keys=True),
        )
    finally:
        if entry is not None and not released and (failed or release_policy != "keep_loaded"):
            model_management.unload_model_and_clones(entry.patcher)
        if entry is not None and not cache_cleared and (failed or release_policy == "clear_after"):
            _drop_cached_entry(entry)
        if failed or release_policy == "clear_after":
            gc.collect()
            model_management.soft_empty_cache()
