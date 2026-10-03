"""Easy H3 dual sampling: LOW pass -> learned latent upscale -> HIGH pass.

The two nodes only *prepare* each pass; sampling itself runs in ComfyUI's
native SamplerCustomAdvanced, so the 1st-pass result can be decoded and saved
before the 2nd pass starts.

  Dual Sample 1st  -> guider / sampler / sigmas / latent  -> SamplerCustomAdvanced
                                                             | denoised_output (= 1st latent)
  Dual Sample 2nd  <- 1st latent + h3_context  -> guider / sampler / sigmas / latent
                                                             -> SamplerCustomAdvanced (= 2nd latent)

Recipe (published learned two-pass): 1st pass = first N of a ``simple`` schedule;
the x0 prediction is enlarged by the learned 3D latent resizer; the canvas-sized
conditioning is rebuilt at the HIGH canvas; 2nd pass = published refine sigmas.
The dual-clock Euler sampler, resizer and semantic bridge live in third_party/.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

import torch
import torch.nn.functional as F

import comfy.nested_tensor
import comfy.samplers
import folder_paths
from comfy_extras import nodes_custom_sampler as custom_sampler
from comfy_extras import nodes_minimax_h3 as h3

from .third_party import h3_dual_clock as dual_clock
from .third_party import h3_learned_upscale as learned
from .third_party import h3_semantic_bridge as bridge


LOGGER = logging.getLogger(__name__)

SHIFT_VIDEO = 12.0
SHIFT_AUDIO = 3.0
VAE_DOWNSAMPLE = 16
NONE_OPTION = "无（不使用）"
BICUBIC_OPTION = "bicubic（无模型，会有重影，不推荐）"
PUBLISHED_REFINE_SIGMAS = learned.UPSTREAM_REFINE_VIDEO_SIGMAS
DEFAULT_TRIGGER_WORDS = (
    "Enhance this video with sharp, crisp details while preserving a natural photorealistic appearance,prfight2svg\n"
    "prfin1svg,"
)


def _prepend_default_trigger_words(prompt: Any, enabled: bool = True) -> str:
    text = str(prompt or "").strip()
    if not enabled:
        return text
    if text == DEFAULT_TRIGGER_WORDS or text.startswith(DEFAULT_TRIGGER_WORDS + "\n"):
        return text
    return f"{DEFAULT_TRIGGER_WORDS}\n{text}" if text else DEFAULT_TRIGGER_WORDS


def _refine_sigmas(steps: int, start_sigma: float) -> torch.Tensor:
    """3/4/5 steps: published values.  Other counts: evenly spaced in flow time
    from where the 1st pass stopped down to zero, on the same shift-12 curve."""
    steps = int(steps)
    if steps in PUBLISHED_REFINE_SIGMAS:
        return torch.tensor(PUBLISHED_REFINE_SIGMAS[steps], dtype=torch.float32)
    t_start = start_sigma / (SHIFT_VIDEO - (SHIFT_VIDEO - 1.0) * start_sigma)
    ts = torch.linspace(t_start, 0.0, steps + 1, dtype=torch.float64)
    sigmas = dual_clock.shift_sigma(ts, SHIFT_VIDEO).float()
    sigmas[-1] = 0.0
    return sigmas


# ---------------------------------------------------------------- helpers

def _node_value(value: Any, index: int = 0) -> Any:
    if hasattr(value, "result"):
        value = value.result
    return value[index]


def _split_av(samples: Any):
    if getattr(samples, "is_nested", False):
        parts = list(samples.unbind())
        return parts[0], (parts[1] if len(parts) > 1 else None)
    return samples, None


def _bridge_models() -> dict[str, str]:
    default = Path(folder_paths.models_dir) / "semantic_bridge"
    roots = [Path(p) for p in folder_paths.folder_names_and_paths.get("semantic_bridge", ([], set()))[0]]
    if default not in roots:
        roots.append(default)
    found: dict[str, str] = {}
    for root in roots:
        if root.is_dir():
            for path in sorted(root.rglob("*.safetensors")):
                if ".cache" not in path.relative_to(root).parts:
                    found.setdefault(path.relative_to(root).as_posix(), str(path.resolve()))
    return found


def _upscaler_choices() -> list[str]:
    try:
        files = list(folder_paths.get_filename_list("latent_upscale_models"))
    except Exception:
        files = []
    return files + [BICUBIC_OPTION]


class _AnyTypeName(str):
    def __ne__(self, other):
        return False


_ANY = _AnyTypeName("*")


def _context_class():
    from .nodes import MiniMaxH3Context
    return MiniMaxH3Context


def _with_default_trigger_words(context: Any, enabled: bool) -> Any:
    """Rebuild the current-canvas conditioning with the hidden trigger prefix.

    The Easy H3 context stores a canvas-aware rebuilder so that the second pass
    can re-encode references at a larger canvas. Reusing that builder here
    keeps image/video/audio reference metadata identical to the main node.
    """
    if not enabled:
        return context
    rebuilder = getattr(context, "highres_rebuilder", None)
    if rebuilder is None or not callable(getattr(rebuilder, "build", None)):
        raise ValueError("Default Trigger Words needs the H3 context rebuild information; reconnect the main Easy H3 context.")
    video, _audio = _split_av(context.latent["samples"])
    width = int(video.shape[-1]) * VAE_DOWNSAMPLE
    height = int(video.shape[-2]) * VAE_DOWNSAMPLE
    conditioning, _template = rebuilder.build(width, height, DEFAULT_TRIGGER_WORDS)
    return replace(
        context,
        conditioning=conditioning,
        prompt_preview=_prepend_default_trigger_words(getattr(context, "prompt_preview", ""), True),
    )


CANVAS_KEYS = ("minimax_refs", "minimax_keyframes", "minimax_frame_count")


def _guider(model, conditioning, cfg: float = 1.0, negative=None):
    """cfg 1 = BasicGuider (distilled/turbo); cfg > 1 = CFGGuider with a negative."""
    if negative is None or float(cfg) <= 1.0:
        return _node_value(custom_sampler.BasicGuider.execute(model, conditioning))
    guider = comfy.samplers.CFGGuider(model)
    guider.set_conds(conditioning, negative)
    guider.set_cfg(float(cfg))
    return guider


def _encode_negative(clip, text: str):
    """Plain text encoding of the negative prompt (no reference media)."""
    return clip.encode_from_tokens_scheduled(clip.tokenize(str(text or "")))


def _with_canvas(negative, positive):
    """Give the negative the same reference/keyframe payload as the positive,
    so CFG compares text only and both branches see the same canvas."""
    payload = {}
    for _, metadata in positive:
        if isinstance(metadata, dict):
            for key in CANVAS_KEYS:
                if key in metadata:
                    payload[key] = metadata[key]
            break
    return [[embedding, {**metadata, **payload}] for embedding, metadata in negative]


def _bicubic_upscale(latent: dict, scale: float) -> tuple[dict, int, int]:
    video, audio = _split_av(latent["samples"])
    geometry = learned.learned_upscale_geometry(
        int(video.shape[-1]), int(video.shape[-2]), "scale_by", float(scale), 1.0, 0, 0, "preserve_source", 2.0,
    )
    th, tw = int(geometry["output_latent_height"]), int(geometry["output_latent_width"])
    b, c, t, h, w = video.shape
    folded = video.movedim(2, 1).reshape(b * t, c, h, w)
    out = F.interpolate(folded.float(), size=(th, tw), mode="bicubic", align_corners=False).to(video.dtype)
    out = out.reshape(b, t, c, th, tw).movedim(2, 1)
    result = dict(latent)
    result["samples"] = comfy.nested_tensor.NestedTensor((out, audio))
    return result, int(geometry["output_width"]), int(geometry["output_height"])


def _reencode_keyframes(conditioning, context, width: int, height: int):
    """Fallback when no rebuilder exists: re-encode canvas-sized keyframes only."""
    sources = {}
    for entry in getattr(context, "keyframe_images", None) or ():
        try:
            index, crop, image = entry
            sources[int(index)] = (image, str(crop))
        except (TypeError, ValueError):
            continue
    vae = context.video_vae
    rebuilt = []
    for embedding, metadata in conditioning:
        if not isinstance(metadata, dict) or not metadata.get("minimax_keyframes"):
            rebuilt.append([embedding, metadata])
            continue
        metadata = dict(metadata)
        keyframes = []
        for keyframe in metadata["minimax_keyframes"]:
            keyframe = dict(keyframe)
            latent = keyframe.get("latent")
            if isinstance(latent, torch.Tensor) and latent.ndim == 5 \
                    and tuple(latent.shape[-2:]) != (height // VAE_DOWNSAMPLE, width // VAE_DOWNSAMPLE):
                index = int(keyframe.get("resolved_frame_index", 0))
                if index in sources:
                    image, crop = sources[index]
                else:
                    image = vae.decode(latent)
                    if image.ndim == 5:
                        image = image.reshape(-1, *image.shape[-3:])
                    crop = "center" if index > 0 else "disabled"
                keyframe["latent"] = vae.encode(h3._resize(image, width, height, crop))
            keyframes.append(keyframe)
        metadata["minimax_keyframes"] = keyframes
        rebuilt.append([embedding, metadata])
    return rebuilt


def _reconcile(upscaled: dict, template: dict | None) -> dict:
    """Enlarged video + first-pass audio (template audio only when it is locked)."""
    video, audio = _split_av(upscaled["samples"])
    result = {k: v for k, v in upscaled.items() if k not in ("noise_mask", "batch_index")}
    if template is not None:
        t_video, t_audio = _split_av(template["samples"])
        if tuple(t_video.shape) != tuple(video.shape):
            raise ValueError(
                f"二采条件的画幅与放大后的 latent 不一致：{tuple(t_video.shape)} vs {tuple(video.shape)}"
            )
        mask = template.get("noise_mask")
        if mask is not None:
            _, audio_mask = _split_av(mask)
            if isinstance(audio_mask, torch.Tensor) and bool(torch.any(audio_mask < 1.0)):
                audio = t_audio
            result["noise_mask"] = mask
    result["samples"] = comfy.nested_tensor.NestedTensor((video, audio))
    return result


def _apply_bridge(conditioning, config, source):
    if config is None:
        return conditioning
    return bridge.apply_bridge(conditioning, config, encoding_source=source)[0]


# ---------------------------------------------------------------- nodes

class FeiHouEasyH3DualSample1st:
    """Prepare the 1st (LOW canvas, high-noise) pass for a native SamplerCustomAdvanced."""

    CATEGORY = "FeiHou Easy H3"
    FUNCTION = "prepare"
    RETURN_TYPES = ("GUIDER", "SAMPLER", "SIGMAS", "LATENT", "MINIMAX_H3_CONTEXT")
    RETURN_NAMES = ("guider", "sampler", "sigmas", "latent", "h3_context")
    DESCRIPTION = (
        "一采准备：输出 guider/sampler/sigmas/latent 接原生 SamplerCustomAdvanced。"
        "一采只跑 simple 调度的前几步（高噪声段）；把采样器的 denoised_output 和这里的 h3_context 接到 Dual Sample 2nd。"
    )

    @classmethod
    def INPUT_TYPES(cls):
        bridges = [NONE_OPTION, *_bridge_models()]
        return {
            "required": {
                "model": ("MODEL", {"tooltip": "一采模型（小画幅、高噪声段）。"}),
                "h3_context": ("MINIMAX_H3_CONTEXT", {"tooltip": "接 Easy H3 的 h3_context；Easy H3 的分辨率就是一采画幅。"}),
                "total_steps": ("INT", {"default": 9, "min": 2, "max": 100, "tooltip": "simple 调度总步数。"}),
                "first_steps": ("INT", {"default": 4, "min": 1, "max": 99, "tooltip": "一采实际只跑前几步。"}),
                "sampler_name": (list(dual_clock.SAMPLER_OPTIONS), {"default": dual_clock.DEFAULT_SAMPLER_NAME, "tooltip": "dual_clock_euler：视频/音频各走各的时钟（推荐）。二采沿用同一个。"}),
                "semantic_bridge_on": ("BOOLEAN", {"default": True, "label_on": "开", "label_off": "关", "tooltip": "语义桥开关；关闭后下面两项不生效。"}),
                "semantic_bridge": (bridges, {"default": NONE_OPTION, "tooltip": "语义桥模型（models/semantic_bridge），一采和二采都会应用。"}),
                "bridge_strength": ("FLOAT", {"default": 0.10, "min": 0.0, "max": 1.0, "step": 0.01}),
                "cfg": ("FLOAT", {"default": 1.0, "min": 1.0, "max": 20.0, "step": 0.1, "round": 0.01, "tooltip": "用加速 LoRA 时保持 1（不跑负面，最快）；不用加速 LoRA 时调高，负面提示词才会生效。"}),
                "negative_prompt": ("STRING", {"multiline": True, "default": "", "tooltip": "负面提示词，仅 cfg > 1 时使用；一采二采共用。"}),
            }
        }

    @classmethod
    def IS_CHANGED(cls, semantic_bridge=NONE_OPTION, bridge_strength=0.1, semantic_bridge_on=True, **kwargs):
        if not semantic_bridge_on:
            return "off"
        path = _bridge_models().get(semantic_bridge)
        return bridge.file_sha(path) if path and bridge_strength else "none"

    @staticmethod
    def prepare(model, h3_context, total_steps, first_steps, sampler_name, semantic_bridge_on, semantic_bridge,
                bridge_strength, cfg=1.0, negative_prompt="", scheduler="simple", default_trigger_words=False):
        if not isinstance(h3_context, _context_class()):
            raise ValueError("h3_context 必须接 ComfyUI-FeiHou-Easy-H3 主节点的 h3_context")
        total_steps, first_steps = int(total_steps), int(first_steps)
        if not 1 <= first_steps < total_steps:
            raise ValueError("first_steps 必须小于 total_steps")

        config = None
        if semantic_bridge_on and semantic_bridge != NONE_OPTION and float(bridge_strength) > 0:
            path = _bridge_models().get(semantic_bridge)
            if not path:
                raise FileNotFoundError("找不到语义桥模型，请放到 models/semantic_bridge 后刷新")
            config = bridge.BridgeConfig(path, bridge.file_sha(path), float(bridge_strength))
            bridge.preflight_bridge(config)

        effective_context = _with_default_trigger_words(h3_context, bool(default_trigger_words))
        patched, sampler, _ = dual_clock.setup_dual_clock_sampling(
            model, effective_context.latent, 1, SHIFT_VIDEO, SHIFT_AUDIO, sampler_name, dual_clock.DEFAULT_SCHEDULER_NAME,
        )
        full = dual_clock._scheduler_sigmas(patched.get_model_object("model_sampling"), scheduler, total_steps, SHIFT_VIDEO)
        full = full.detach().float().cpu()
        sigmas = full[: first_steps + 1].clone()
        conditioning = _apply_bridge(effective_context.conditioning, config, "easy_h3_low")

        negative = None
        if float(cfg) > 1.0:
            negative = _apply_bridge(_encode_negative(h3_context.clip, negative_prompt), config, "easy_h3_negative")
        plan = {"model": model, "sampler_name": sampler_name, "bridge": config, "start_sigma": float(sigmas[-1]),
                "cfg": float(cfg), "negative": negative, "tail": full[first_steps:].clone()}
        context = replace(effective_context, dual_plan=plan)
        low_negative = _with_canvas(negative, conditioning) if negative is not None else None
        return _guider(patched, conditioning, cfg, low_negative), sampler, sigmas, effective_context.latent, context


class FeiHouEasyH3DualSample2nd:
    """Upscale the 1st latent and prepare the 2nd (HIGH canvas, low-noise) pass."""

    CATEGORY = "FeiHou Easy H3"
    FUNCTION = "prepare"
    RETURN_TYPES = ("GUIDER", "SAMPLER", "SIGMAS", "LATENT", "MINIMAX_H3_CONTEXT")
    RETURN_NAMES = ("guider", "sampler", "sigmas", "latent", "h3_context")
    DESCRIPTION = (
        "二采准备：把 1st latent 做学习型 latent 放大，在大画幅重建条件，输出 guider/sampler/sigmas/latent "
        "接原生 SamplerCustomAdvanced（用它的 output 解码）。"
    )

    @classmethod
    def INPUT_TYPES(cls):
        upscalers = _upscaler_choices()
        return {
            "required": {
                "h3_context": ("MINIMAX_H3_CONTEXT", {"tooltip": "接 Dual Sample 1st 的 h3_context。"}),
                "1st_latent": ("LATENT", {"tooltip": "接一采 SamplerCustomAdvanced 的 denoised_output（不是 output）。"}),
                "latent_upscaler": (upscalers, {"default": upscalers[0], "tooltip": "models/latent_upscale_models 里的 H3 学习型 3D 放大模型。"}),
                "upscale_by": ("FLOAT", {"default": 1.5, "min": 1.0, "max": 4.0, "step": 0.05, "tooltip": "二采画幅 = 一采画幅 × 倍数（按 32 对齐、保持比例）。"}),
                "second_steps": ("INT", {"default": 5, "min": 1, "max": 50, "tooltip": "二采步数。3/4/5 用发布的 sigmas；其它步数从一采停下的位置均匀续到 0。"}),
            },
            "optional": {
                "model": ("MODEL", {"tooltip": "二采模型；不接则沿用一采模型。"}),
                "wait_1st_video": (_ANY, {"tooltip": "接一采 Video Combine 的输出：保证一采视频先保存，再开始二采。"}),
            },
        }

    @staticmethod
    def prepare(h3_context, latent_upscaler, upscale_by, second_steps, model=None, default_trigger_words=False, **kwargs):
        first_latent = kwargs.get("1st_latent")
        plan = getattr(h3_context, "dual_plan", None) if isinstance(h3_context, _context_class()) else None
        if not plan:
            raise ValueError("h3_context 必须接 Dual Sample 1st 的 h3_context 输出")
        if not isinstance(first_latent, dict) or not getattr(first_latent.get("samples"), "is_nested", False):
            raise ValueError("1st_latent 必须是一采采样器输出的 H3 音视频 latent（denoised_output）")

        # learned latent upscale (video only; audio carried over)
        if latent_upscaler == BICUBIC_OPTION:
            upscaled, width, height = _bicubic_upscale(first_latent, float(upscale_by))
        else:
            upscaled, width, height, _ = learned.learned_upscale_h3_av_latent(
                first_latent, latent_upscaler, "scale_by", float(upscale_by), 1.0, 0, 0,
                "preserve_source", 1.05, "fp16", "offload_after",
            )

        # HIGH canvas conditioning: rebuild when it depends on the canvas
        rebuilder = getattr(h3_context, "highres_rebuilder", None)
        trigger_prefix = DEFAULT_TRIGGER_WORDS if bool(default_trigger_words) else ""
        if trigger_prefix and (rebuilder is None or not callable(getattr(rebuilder, "build", None))):
            raise ValueError("Default Trigger Words needs the H3 context rebuild information; reconnect the main Easy H3 context.")
        template = None
        if rebuilder is not None and (rebuilder.canvas_dependent or trigger_prefix):
            conditioning, template = rebuilder.build(width, height, trigger_prefix)
        else:
            conditioning = _reencode_keyframes(h3_context.conditioning, h3_context, width, height)
        conditioning = _apply_bridge(conditioning, plan["bridge"], "easy_h3_high")
        latent = _reconcile(upscaled, template)

        refine_model = model if model is not None else plan["model"]
        patched, sampler, _ = dual_clock.setup_dual_clock_sampling(
            refine_model, latent, 1, SHIFT_VIDEO, SHIFT_AUDIO, plan["sampler_name"], dual_clock.DEFAULT_SCHEDULER_NAME,
        )
        tail = plan.get("tail")
        if int(second_steps) not in PUBLISHED_REFINE_SIGMAS and tail is not None and int(second_steps) == tail.numel() - 1:
            sigmas = tail.clone()  # the rest of the 1st-pass schedule
        else:
            sigmas = _refine_sigmas(second_steps, float(plan.get("start_sigma", PUBLISHED_REFINE_SIGMAS[5][0])))
        context = replace(h3_context, conditioning=conditioning, latent=latent)
        LOGGER.info("Easy H3 dual sample: 2nd canvas %dx%d, sigmas %s", width, height, sigmas.tolist())
        negative = plan.get("negative")
        high_negative = _with_canvas(negative, conditioning) if negative is not None else None
        return _guider(patched, conditioning, plan.get("cfg", 1.0), high_negative), sampler, sigmas, latent, context


# ---------------------------------------------------------------- Sample Enhancers

_MEDIA_TYPES = ("VAE", "VAE", "FLOAT")
_MEDIA_NAMES = ("video_vae", "audio_vae", "fps")


def _media(context):
    return context.video_vae, context.audio_vae, float(context.fps or 24.0)


class FeiHouEasyH3SampleEnhancer1st:
    """1st pass: guider / sampler / sigmas / latent for the official SamplerCustomAdvanced."""

    CATEGORY = "FeiHou Easy H3"
    FUNCTION = "prepare"
    RETURN_TYPES = ("GUIDER", "SAMPLER", "SIGMAS", "LATENT", "MINIMAX_H3_CONTEXT") + _MEDIA_TYPES
    RETURN_NAMES = ("guider", "sampler", "sigmas", "latent", "h3_context") + _MEDIA_NAMES
    DESCRIPTION = (
        "一采：直接接 Easy H3 的 h3_context，输出 guider/sampler/sigmas/latent 给官方自定义采样器（高级），噪波外接。"
        "采样器的 denoised_output 可直接解码预览（vae/fps 由本节点输出），并接 2nd Sample Enhancer。"
    )

    @classmethod
    def INPUT_TYPES(cls):
        bridges = [NONE_OPTION, *_bridge_models()]
        return {
            "required": {
                "model": ("MODEL", {"tooltip": "一采模型（小画幅、高噪声段）。"}),
                "h3_context": ("MINIMAX_H3_CONTEXT", {"tooltip": "接 Easy H3 主节点的 h3_context；Easy H3 的分辨率就是一采画幅。"}),
                "sampler_name": (list(dual_clock.SAMPLER_OPTIONS), {"default": dual_clock.DEFAULT_SAMPLER_NAME, "tooltip": "dual_clock_euler：视频/音频各走各的时钟（推荐）；其它为 ComfyUI 原生采样器。二采沿用。"}),
                "scheduler": (list(dual_clock.SCHEDULER_OPTIONS), {"default": "simple", "tooltip": "一采调度器。native_flow = shift 12 下均匀的 H3 原生流调度（与 simple 几乎相同）；beta57 = beta(0.5, 0.7)；二采按 second_steps 规则续接。"}),
                "total_steps": ("INT", {"default": 9, "min": 2, "max": 200, "tooltip": "调度总步数。"}),
                "first_steps": ("INT", {"default": 4, "min": 1, "max": 199, "tooltip": "一采只跑前几步（高噪声段）。"}),
                "cfg": ("FLOAT", {"default": 1.0, "min": 1.0, "max": 20.0, "step": 0.1, "round": 0.01, "tooltip": "加速 LoRA 保持 1（不算负面）；不用加速 LoRA 时调高。二采沿用。"}),
                "negative_prompt": ("STRING", {"multiline": True, "default": "", "tooltip": "负面提示词，仅 cfg > 1 时使用；自动带上与正面相同的参考图/关键帧，二采沿用。"}),
                "semantic_bridge_on": ("BOOLEAN", {"default": True, "label_on": "开", "label_off": "关", "tooltip": "语义桥开关；关闭后下面两项不生效。"}),
                "semantic_bridge": (bridges, {"default": NONE_OPTION, "tooltip": "语义桥模型（models/semantic_bridge），一采和二采都会应用。"}),
                "bridge_strength": ("FLOAT", {"default": 0.10, "min": 0.0, "max": 1.0, "step": 0.01}),
                "default_trigger_words": ("BOOLEAN", {"default": True, "label_on": "Default Trigger Words", "label_off": "Default Trigger Words", "tooltip": "默认开启：把内置触发词加到提示词开头；关闭后不添加。触发词不会在节点中展开显示。"}),
            }
        }

    @classmethod
    def IS_CHANGED(cls, semantic_bridge=NONE_OPTION, bridge_strength=0.1, semantic_bridge_on=True, **kwargs):
        if not semantic_bridge_on:
            return "off"
        path = _bridge_models().get(semantic_bridge)
        return bridge.file_sha(path) if path and bridge_strength else "none"

    @staticmethod
    def prepare(model, h3_context, sampler_name, scheduler, total_steps, first_steps, cfg, negative_prompt,
                semantic_bridge_on, semantic_bridge, bridge_strength, default_trigger_words=True):
        outputs = FeiHouEasyH3DualSample1st.prepare(
            model, h3_context, total_steps, first_steps, sampler_name, semantic_bridge_on, semantic_bridge,
            bridge_strength, cfg, negative_prompt, scheduler, default_trigger_words,
        )
        return (*outputs, *_media(h3_context))


class FeiHouEasyH3SampleEnhancer2nd:
    """2nd pass: learned upscale + HIGH canvas guider / sampler / sigmas / latent, plus decode/output info."""

    CATEGORY = "FeiHou Easy H3"
    FUNCTION = "prepare"
    RETURN_TYPES = ("GUIDER", "SAMPLER", "SIGMAS", "LATENT", "MINIMAX_H3_CONTEXT") + _MEDIA_TYPES + ("AUDIO", "FEIHOU_H3_DURATION_CONTROL")
    RETURN_NAMES = ("guider", "sampler", "sigmas", "latent", "h3_context") + _MEDIA_NAMES + ("audio_1", "duration_control")
    DESCRIPTION = (
        "二采：1st latent 学习型放大 + 大画幅重建条件，输出 guider/sampler/sigmas/latent 给官方自定义采样器（高级）；"
        "vae/fps/audio_1/duration_control 直接接解码、时长裁剪和视频合并。"
    )

    @classmethod
    def INPUT_TYPES(cls):
        upscalers = _upscaler_choices()
        return {
            "required": {
                "h3_context": ("MINIMAX_H3_CONTEXT", {"tooltip": "接 1st Sample Enhancer 的 h3_context。"}),
                "1st_latent": ("LATENT", {"tooltip": "接一采自定义采样器的 denoised_output（不是 output）。"}),
                "latent_upscaler": (upscalers, {"default": upscalers[0], "tooltip": "models/latent_upscale_models 里的 H3 学习型 3D 放大模型。"}),
                "upscale_by": ("FLOAT", {"default": 1.5, "min": 1.0, "max": 4.0, "step": 0.05, "tooltip": "二采画幅 = 一采画幅 × 倍数（按 32 对齐、保持比例）。"}),
                "second_steps": ("INT", {"default": 5, "min": 1, "max": 200, "tooltip": "3/4/5 用发布的 sigmas；等于剩余步数时接着一采的调度；其它步数从一采停下处均匀续到 0。"}),
                "default_trigger_words": ("BOOLEAN", {"default": True, "label_on": "Default Trigger Words", "label_off": "Default Trigger Words", "tooltip": "默认开启：把内置触发词加到二采提示词开头；关闭后不添加。触发词不会在节点中展开显示。"}),
            },
            "optional": {
                "model": ("MODEL", {"tooltip": "二采模型；不接则沿用一采模型。"}),
                "wait_1st_video": (_ANY, {"tooltip": "接一采 Video Combine 的输出：保证一采视频先保存，再开始二采。"}),
            },
        }

    @staticmethod
    def prepare(h3_context, latent_upscaler, upscale_by, second_steps, model=None, **kwargs):
        default_trigger_words = kwargs.pop("default_trigger_words", True)
        guider, sampler, sigmas, latent, context = FeiHouEasyH3DualSample2nd.prepare(
            h3_context, latent_upscaler, upscale_by, second_steps, model=model,
            default_trigger_words=default_trigger_words, **kwargs,
        )
        return (guider, sampler, sigmas, latent, context, *_media(context),
                context.audio_1, context.duration_control)
