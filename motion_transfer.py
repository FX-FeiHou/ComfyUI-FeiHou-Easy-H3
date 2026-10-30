"""FeiHou Easy H3 Motion Transfer: one-pass, segment-by-segment reference-video generation.

  Setup (reference mode) -> Motion Segment -> official SamplerCustomAdvanced -> Motion Collect -> Video Combine

Two ways to cut the reference video 1 (e.g. the RGB dance):

* **Shot blocks** (``shot_prompts`` connected): the markdown shot list gives
  each block its time range and its own prompt; a block of up to 15 s is one
  segment, a longer continuous shot is split at its @split points (or evenly)
  into segments of at most 15 s, and the block is saved as one video.  Consecutive segments inside a block,
  and a block marked ``@continue``, are anchored to the previous segment's last
  frames through the tail guide, so the seam stays continuous; a block that
  follows a hard cut starts from nothing.  With 分段方式 上下文窗口 a long
  block with reference video is instead one segment sampled jointly with
  overlapping context windows (``h3_context_windows``), so nothing continues
  from decoded frames inside the block.
* **Fixed length** (no shot list): every ``segment_frames`` frames, as before.

Motion Segment cuts every reference video (RGB, depth, ...) and its soundtrack
to the segment's window and outputs noise / guider / sampler / sigmas / latent
for the official sampler.  Motion Collect decodes the sampled segment, adds its
frames to the block, and — until the last segment — blocks its outputs and asks
the browser to queue the next run.  After a block's last segment it outputs the
finished block, remembers its tail frame and advances the shot list; Video
Combine V2 joins the blocks into the complete film.
"""

from __future__ import annotations

import logging
import math
import os
import random
from collections import OrderedDict
from dataclasses import dataclass, replace
from fractions import Fraction

import torch

import comfy.nested_tensor
import comfy.samplers
import comfy.utils
import folder_paths
import node_helpers
import nodes
from comfy.ldm.minimax.model import FRAME_RESCALE
from comfy_api.latest import InputImpl, Types
from comfy_execution.graph_utils import ExecutionBlocker
from comfy_extras import nodes_audio as comfy_audio_nodes
from comfy_extras import nodes_custom_sampler as custom_sampler
from comfy_extras import nodes_minimax_h3 as h3

from . import h3_context_windows as context_windows
from .dual_sample import (
    NONE_OPTION, SHIFT_AUDIO, SHIFT_VIDEO, VAE_DOWNSAMPLE, FeiHouEasyH3DualSample1st,
    _encode_negative, _guider, _node_value, _split_av, _with_canvas,
)
from .nodes import H3HighresRebuilder, MiniMaxH3Context
from .production_continuation import SAVE_KEY, _audio_slice
from .production_pack import SHOT_TYPE
from .shot_prompts import advance_block, finish_series, load_tail, output_root, save_tail
from .third_party import h3_dual_clock as dual_clock


LOGGER = logging.getLogger(__name__)
STATE_TYPE = "FEIHOU_H3_MOTION_STATE"
OVERLAP_CHOICES = ["22", "39", "5"]
START_AUTO = "自动续接"
START_RESTART = "从头开始"
AUDIO_AUTO = "自动"
AUDIO_SOURCE = "原声音频"
AUDIO_GENERATED = "生成音频"
# Motion Segment: does reference video 1's soundtrack go into the model as <Audio 1>?
SOUNDTRACK_TO_MODEL = "送入模型"
SOUNDTRACK_COLLECT_ONLY = "不送入模型"
# Motion Segment: one sampling pass, or the 1st pass of the two-pass chain
# (its outputs go to SamplerCustomAdvanced, then the 2nd Sample Enhancer).
SAMPLING_SINGLE = "单次采样"
SAMPLING_TWO_PASS = "二段式（一采）"
# Two-pass is not a setting: the node is the 1st pass when its h3_context output
# reaches a 2nd Sample Enhancer in the queued prompt (Set/Get nodes are already
# resolved to plain links there).
SECOND_PASS_CLASSES = {"FeiHouEasyH3SampleEnhancer2nd", "FeiHouEasyH3RHSampleEnhancer2nd",
                       "FeiHouEasyH3DualSample2nd", "FeiHouEasyH3RHDualSample2nd"}
# Motion Segment: how a block longer than one segment is sampled.  帧窗口: in-block
# segments one after another, each continuing the previous one's decoded tail.
# 上下文窗口: the whole block in one sampler run, overlapping windows blended at
# every step (h3_context_windows); blocks without reference video (talk) keep 帧窗口.
SEGMENTATION_CHAIN = "帧窗口"
# Saved before the rename; still means 帧窗口.
SEGMENTATION_LEGACY = {"接续": SEGMENTATION_CHAIN}
# Context windows are switched off for now: the fields stay (saved workflows
# keep their values) but are hidden in the UI and every block runs 帧窗口.
CONTEXT_WINDOWS_ENABLED = False
SEGMENTATION_CONTEXT = "上下文窗口"
# Context-window sizes offered on Motion Segment.  Windows are 5 + 17N frames,
# from about 5 s (124) up to 15 s (362); overlaps are whole 17-frame groups,
# because windows start on the 17-frame clip grid.
def _window_label(frames: int) -> str:
    return f"{frames} 帧 · {frames / h3.FPS:.2f} 秒"


CONTEXT_WINDOW_CHOICES = [_window_label(5 + 17 * groups) for groups in range(7, 22)]
CONTEXT_WINDOW_DEFAULT = _window_label(345)
# 固定分段的每段帧数, shown with its length in seconds; saved bare numbers still work.
SEGMENT_FRAME_RANGE = (39, 497)
SEGMENT_FRAME_CHOICES = [_window_label(frames) for frames in range(39, SEGMENT_FRAME_RANGE[1] + 1, 17)]
SEGMENT_FRAME_DEFAULT = _window_label(124)


def _window_frames(value) -> int:
    """Frames of a window option ("362 帧 · 15.08 秒"; a bare number also works)."""
    return int(str(value).split()[0])
CONTEXT_OVERLAP_CHOICES = ["34", "68", "102"]
CONTEXT_OVERLAP_DEFAULT = "68"
SEGMENT_SAMPLERS = [*comfy.samplers.SAMPLER_NAMES,
                    *(name for name in dual_clock.SAMPLER_OPTIONS if name not in comfy.samplers.SAMPLER_NAMES)]
# An in-block segment (块内分镜) holds at most 15 s of the block; the overlap
# context frames of a continued segment come on top.  A 15 s block is one
# segment, 20 s two, 40 s three.
SEGMENT_MAX_FRAMES = round(15.0 * h3.FPS)
# One motion-transfer run in progress (ComfyUI process memory).
_RUN: dict = {}
# Encoded reference windows (VAE + text encoder, about a minute each) of recent
# segments: re-running a shot block with another seed reuses them.
_WINDOW_CACHE: OrderedDict = OrderedDict()
# A context-window block holds a few windows per canvas.
WINDOW_CACHE_SIZE = 16


def _detached(value):
    """Rebuild lists/dicts around the same tensors (moved to the CPU)."""
    if isinstance(value, torch.Tensor):
        return value.detach().cpu()
    if isinstance(value, dict):
        return {key: _detached(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_detached(item) for item in value)
    return value


def _fresh(value):
    """New containers around cached tensors, so callers may add keys freely."""
    if isinstance(value, dict):
        return {key: _fresh(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_fresh(item) for item in value)
    return value


def _build_window(h3_context, builder, start, count, width, height, **options):
    """``builder.build`` with a small cache keyed by request, models and window."""
    if not getattr(builder, "signature", ""):
        return builder.build(start, count, width, height, **options)
    key = (builder.signature, id(h3_context.clip), id(h3_context.video_vae),
           int(start), int(count), int(width), int(height), tuple(sorted(options.items())))
    cached = _WINDOW_CACHE.get(key)
    if cached is not None:
        _WINDOW_CACHE.move_to_end(key)
        LOGGER.info("Easy H3 motion transfer: 参考窗口 %d-%d 与上次相同，复用已编码结果（跳过 VAE 与文本编码）",
                    start, start + count - 1)
        conditioning, latent = cached
        return _fresh(conditioning), _fresh(latent)
    conditioning, latent = builder.build(start, count, width, height, **options)
    _WINDOW_CACHE[key] = (_detached(conditioning), _detached(latent))
    while len(_WINDOW_CACHE) > WINDOW_CACHE_SIZE:
        _WINDOW_CACHE.popitem(last=False)
    return _fresh(conditioning), _fresh(latent)


_RGB_TO_YCC = torch.tensor([[0.299, 0.587, 0.114],
                            [-0.168736, -0.331264, 0.5],
                            [0.5, -0.418688, -0.081312]])
_YCC_TO_RGB = torch.linalg.inv(_RGB_TO_YCC)
# Colour matching works on a small copy of both clips; the correction is then
# applied to the full-size frames.
COLOR_ANALYSIS_HEIGHT = 120
COLOR_MIN_SHOT_FRAMES = 6
# A shot is corrected only when enough unchanged background is left to measure.
COLOR_MIN_BACKGROUND = 0.2
# Typical per-pixel difference above this: the background itself was replaced
# (motion transfer to a new scene), so there is nothing to match against.
COLOR_MAX_TYPICAL_DIFF = 0.12


def _ycc_stats(frames: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """Per-channel YCbCr mean/std over a whole clip, from a pixel subsample."""
    step = max(1, int(math.sqrt(frames.shape[1] * frames.shape[2] / 65536)))
    pixels = frames[:, ::step, ::step, :3].reshape(-1, 3).float() @ _RGB_TO_YCC.T
    return pixels.mean(0), pixels.std(0).clamp(min=1e-4)


def _apply_ycc(frames: torch.Tensor, source_mean, scale, target_mean, strength: float) -> torch.Tensor:
    """(ycc - source_mean) * scale + target_mean, blended by ``strength``, in chunks."""
    out = torch.empty_like(frames)
    for start in range(0, int(frames.shape[0]), 16):
        chunk = frames[start:start + 16, ..., :3].float()
        ycc = chunk @ _RGB_TO_YCC.T
        matched = (ycc - source_mean) * scale + target_mean
        ycc = ycc + strength * (matched - ycc)
        out[start:start + 16] = (ycc @ _YCC_TO_RGB.T).clamp(0, 1).to(frames.dtype)
    return out


def match_colors(frames: torch.Tensor, reference: torch.Tensor, strength: float = 1.0) -> torch.Tensor:
    """Whole-clip YCbCr mean/std transfer (no masking)."""
    strength = max(0.0, min(1.0, float(strength)))
    if strength == 0.0 or reference is None or int(reference.shape[0]) == 0:
        return frames
    source_mean, source_std = _ycc_stats(frames)
    target_mean, target_std = _ycc_stats(reference.cpu())
    return _apply_ycc(frames, source_mean, (target_std / source_std).clamp(0.5, 2.0), target_mean, strength)


def _analysis_copy(frames: torch.Tensor, size: tuple[int, int]) -> torch.Tensor:
    """[N, H, W, 3] -> [N, 3, h, w] float on CPU, area-downscaled."""
    parts = []
    for start in range(0, int(frames.shape[0]), 32):
        chunk = frames[start:start + 32, ..., :3].float().cpu().movedim(-1, 1)
        parts.append(torch.nn.functional.interpolate(chunk, size=size, mode="area"))
    return torch.cat(parts)


def _colour_histograms(clip: torch.Tensor, bins: int = 8) -> torch.Tensor:
    """Normalised joint RGB histograms of a [N, 3, h, w] clip."""
    levels = (clip.clamp(0, 0.999) * bins).long()
    index = (levels[:, 0] * bins + levels[:, 1]) * bins + levels[:, 2]
    counts = torch.stack([torch.bincount(frame.flatten(), minlength=bins ** 3) for frame in index]).float()
    return counts / counts.sum(1, keepdim=True)


def _shot_bounds(reference: torch.Tensor) -> list[tuple[int, int]]:
    """Hard cuts inside the source window ([N, 3, h, w]); one-frame flashes are not cuts.

    Colour histograms barely move with camera or subject motion but jump at a
    cut (measured on concert footage: cuts 0.57-0.99, hand-held chaos up to 0.40).
    A missed cut only shares one correction between two shots; a false one could
    show a step inside a shot, so the threshold leans high.
    """
    count = int(reference.shape[0])
    if count < 2:
        return [(0, count)]
    hist = _colour_histograms(reference)
    distance = lambda a, b: 0.5 * float((hist[a] - hist[b]).abs().sum())
    jumps = [distance(index, index + 1) for index in range(count - 1)]
    threshold = max(0.5, 3 * float(torch.tensor(jumps).median()))
    cuts = []
    for index, jump in enumerate(jumps):
        if jump < threshold:
            continue
        # A one-frame flash makes two jumps (into it and out of it); around
        # either one, the frames one step further away still look alike.
        if index + 2 < count and distance(index, index + 2) < 0.5 * jump:
            continue
        if index >= 1 and distance(index - 1, index + 1) < 0.5 * jump:
            continue
        cuts.append(index + 1)
    bounds = list(zip([0] + cuts, cuts + [count]))
    while len(bounds) > 1 and min(end - start for start, end in bounds) < COLOR_MIN_SHOT_FRAMES:
        short = min(range(len(bounds)), key=lambda i: bounds[i][1] - bounds[i][0])
        if short == 0:
            neighbour = 1
        elif short == len(bounds) - 1:
            neighbour = short - 1
        else:
            before, after = bounds[short - 1], bounds[short + 1]
            neighbour = short - 1 if before[1] - before[0] >= after[1] - after[0] else short + 1
        first, second = sorted((short, neighbour))
        bounds[first:second + 1] = [(bounds[first][0], bounds[second][1])]
    return bounds


def _background(generated: torch.Tensor, reference: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """Pixels that should be unchanged ([N, 1, h, w] bool) and each frame's typical difference.

    The replaced person differs far more from the source than the rest of the
    frame does: the threshold follows each frame's own typical difference, and
    the changed area is widened a little so its edges are not measured.
    """
    def blur(clip):
        return torch.nn.functional.avg_pool2d(clip, 3, 1, 1, count_include_pad=False)

    diff = (blur(generated) - blur(reference)).abs().mean(1, keepdim=True)
    typical = diff.flatten(1).median(1).values
    changed = diff > (typical * 3).clamp(min=0.06).view(-1, 1, 1, 1)
    changed = torch.nn.functional.max_pool2d(changed.float(), 5, 1, 2) > 0
    return ~changed, typical


def match_colors_to_source(frames: torch.Tensor, reference: torch.Tensor, strength: float = 1.0):
    """Correct H3 colour drift per shot, measured only on the unchanged background.

    ``frames``/``reference``: the generated block and the same window of
    reference video 1, [N, H, W, 3] in 0-1. Returns ``(frames, report)``; a shot
    without enough comparable background is left as it is.
    """
    strength = max(0.0, min(1.0, float(strength)))
    count = min(int(frames.shape[0]), int(reference.shape[0]))
    report = []
    if strength == 0.0 or count == 0:
        return frames, report
    height = COLOR_ANALYSIS_HEIGHT
    size = (height, max(8, round(height * int(frames.shape[2]) / int(frames.shape[1]))))
    generated = _analysis_copy(frames[:count], size)
    source = _analysis_copy(reference[:count], size)
    background, typical = _background(generated, source)
    out = frames.clone()
    for start, end in _shot_bounds(source):
        mask = background[start:end, 0]
        share = float(mask.float().mean())
        shot = {"frames": (start, end), "background": round(share, 2), "applied": False}
        report.append(shot)
        if share < COLOR_MIN_BACKGROUND or float(typical[start:end].median()) > COLOR_MAX_TYPICAL_DIFF:
            continue
        ours = generated[start:end].movedim(1, -1)[mask] @ _RGB_TO_YCC.T
        theirs = source[start:end].movedim(1, -1)[mask] @ _RGB_TO_YCC.T
        source_mean, source_std = ours.mean(0), ours.std(0).clamp(min=1e-4)
        target_mean, target_std = theirs.mean(0), theirs.std(0).clamp(min=1e-4)
        scale = (target_std / source_std).clamp(0.67, 1.5)
        corrected = ours + strength * (((ours - source_mean) * scale + target_mean) - ours)
        shot.update(applied=True, error_before=round(float((ours - theirs).abs().mean()) * 255, 1),
                    error_after=round(float((corrected - theirs).abs().mean()) * 255, 1))
        out[start:end] = _apply_ycc(frames[start:end], source_mean, scale, target_mean, strength)
    return out, report


def _color_matched(frames, motion_state, start: int, enabled: bool, strength: float):
    window = motion_state.get("source_window")
    if not enabled:
        return frames
    if window is None:
        LOGGER.warning("Easy H3 motion transfer: 颜色匹配需要重新运行 Setup（旧的 h3_context 没有原片帧），本块未调色")
        return frames
    frames, report = match_colors_to_source(frames, window(start, int(frames.shape[0])), strength)
    for shot in report:
        first, last = shot["frames"]
        if shot["applied"]:
            LOGGER.info("Easy H3 motion transfer: 颜色匹配 镜头帧 %d-%d，背景占 %.0f%%，背景色差 %.1f -> %.1f",
                        first, last - 1, shot["background"] * 100, shot["error_before"], shot["error_after"])
        else:
            LOGGER.info("Easy H3 motion transfer: 颜色匹配 镜头帧 %d-%d 跳过（可对比的背景只有 %.0f%% 或背景本身已换掉）",
                        first, last - 1, shot["background"] * 100)
    return frames


def _grid_frames(frames: int) -> int:
    """Largest valid H3 frame count (5 + 17N) that is <= frames."""
    frames = max(5, int(frames))
    return frames - (frames - 5) % 17


def _ceil_grid(frames: int) -> int:
    """Smallest valid H3 frame count (5 + 17N) that is >= frames."""
    frames = max(5, int(frames))
    return 5 + 17 * math.ceil((frames - 5) / 17)


@dataclass(frozen=True)
class _Segment:
    """One queue run: the reference window it sees and what it contributes.

    ``start`` / ``length`` are the reference window and generated frame count
    (always 5 + 17N); ``skip`` head frames repeat the previous segment through
    the tail guide and are dropped after decoding; ``keep`` frames belong to
    the block.
    """

    start: int
    length: int
    skip: int
    keep: int


def _block_segments(frames: int, start: int, overlap: int, continues: bool,
                    splits: list[int] | tuple[int, ...] | None = None) -> list[_Segment]:
    """Split one shot block into sampling segments of at most 15 s.

    ``splits`` (frames from the block start, from @split) are where the
    framing changes; without them a longer block is split evenly.
    Segment 0 starts from nothing unless the block continues the previous one,
    in which case ``overlap`` context frames come first.  Every later segment
    starts ``overlap`` frames before its own window, so the tail guide and the
    reference video show where the previous segment ended.
    """
    frames = max(1, int(frames))
    points = sorted({int(point) for point in (splits or ()) if 0 < int(point) < frames})
    if points:
        bounds = [0, *points, frames]
        longest = max(right - left for left, right in zip(bounds, bounds[1:]))
        if longest > SEGMENT_MAX_FRAMES + 1:
            raise ValueError(f"动作迁移：@split 分出的块内分镜有 {longest / h3.FPS:.2f} 秒，超过 15 秒。")
    else:
        count = max(1, math.ceil(frames / SEGMENT_MAX_FRAMES))
        bounds = [int(round(index * frames / count)) for index in range(count + 1)]
    bounds[-1] = frames
    plan: list[_Segment] = []
    for index in range(len(bounds) - 1):
        keep = bounds[index + 1] - bounds[index]
        if keep < 1:
            continue
        segment_start = int(start) + bounds[index]
        skip = min(overlap if (index > 0 or continues) else 0, max(0, segment_start))
        plan.append(_Segment(segment_start - skip, _ceil_grid(keep + skip), skip, keep))
    return plan


def _apply_tail_guide(conditioning, video_vae, tail, frames: int, width: int, height: int, length: int):
    """Anchor the start of this segment to the previous segment's last frames."""
    if tail is None or not hasattr(tail, "shape") or int(tail.shape[0]) == 0:
        return conditioning
    lead = tail[-min(int(frames), int(tail.shape[0])):]
    guide = {"resolved_frame_index": 0, "latent": video_vae.encode(h3._resize(lead, width, height, "center"))}
    return node_helpers.conditioning_set_values(
        conditioning, {"minimax_keyframes": [guide], "minimax_frame_count": int(length)})


def _clip_conditioning(h3_context, builder, start: int, length: int, width: int, height: int,
                       windows=None, tail=None, skip: int = 0, first=None, **options):
    """Conditioning + empty latent of one sampled clip.

    Without ``windows`` (or with one) this is the clip's own reference window,
    anchored to ``tail`` when ``skip`` > 0, otherwise to the @first picture
    ``first`` (one frame at frame 0, kept in the output).  With several context
    windows every window gets its own reference window (the anchor goes to the
    first), and the result carries the whole window set for the windowed guider.
    """
    if skip:
        anchor, anchor_frames = tail, skip
    elif first is not None:
        anchor, anchor_frames = first, 1
    else:
        anchor, anchor_frames = None, 0
    if not windows or len(windows) < 2:
        conditioning, latent = _build_window(h3_context, builder, start, length, width, height, **options)
        if anchor_frames:
            conditioning = _apply_tail_guide(conditioning, h3_context.video_vae, anchor, anchor_frames,
                                             width, height, length)
        return conditioning, latent
    conditionings = []
    for index, window in enumerate(windows):
        conditioning, _latent = _build_window(h3_context, builder, start + window.frame_start, window.frames,
                                              width, height, **options)
        if index == 0 and anchor_frames:
            conditioning = _apply_tail_guide(conditioning, h3_context.video_vae, anchor, anchor_frames,
                                             width, height, window.frames)
        conditionings.append(conditioning)
    latent, _frames = h3._empty_av_latent(width, height, length)
    window_set = context_windows.WindowSet(list(windows), conditionings, int(length))
    return context_windows.windowed_conditioning(window_set), latent


def _segment_count(source_frames: int, length: int, step: int) -> int:
    if source_frames <= length:
        return 1
    return 1 + math.ceil((source_frames - length) / step)


def _masked_continuation(latent: dict, previous: dict, overlap: int) -> dict:
    """Copy the previous segment's tail tokens into the head of ``latent`` and freeze them."""
    old_video, old_audio = _split_av(previous["samples"])
    new_video, new_audio = _split_av(latent["samples"])
    video_tokens = h3.video_latent_t(overlap)
    audio_tokens = round(overlap * FRAME_RESCALE)
    if old_video.shape[-2:] != new_video.shape[-2:]:
        raise ValueError("动作迁移：前后两段的画幅不一致，无法接续。")
    old_video = old_video.to(new_video)
    old_audio = old_audio.to(new_audio)
    video = torch.cat((old_video[:, :, -video_tokens:], new_video[:, :, video_tokens:]), dim=2)
    audio = torch.cat((old_audio[..., -audio_tokens:], new_audio[..., audio_tokens:]), dim=-1)
    video_mask = torch.ones_like(video)
    video_mask[:, :, :video_tokens] = 0
    audio_mask = torch.ones_like(audio)
    audio_mask[..., :audio_tokens] = 0
    output = dict(latent)
    output["samples"] = comfy.nested_tensor.NestedTensor((video, audio))
    output["noise_mask"] = comfy.nested_tensor.NestedTensor((video_mask, audio_mask))
    return output


# Motion Collect hands every block's audio out at one format: Video Combine V2
# joins the block videos without re-encoding, and a 32 kHz talk block next to a
# 44.1 kHz source-soundtrack block made the joined film's audio drift and stretch.
FILM_AUDIO_RATE = 48000
FILM_AUDIO_CHANNELS = 2


def _film_audio(audio: dict | None) -> dict | None:
    """Resample to FILM_AUDIO_RATE and make it stereo (mono is duplicated, extra channels dropped)."""
    if not isinstance(audio, dict) or audio.get("waveform") is None:
        return audio
    waveform = audio["waveform"].float()
    rate = int(audio["sample_rate"])
    if rate != FILM_AUDIO_RATE:
        import torchaudio
        waveform = torchaudio.functional.resample(waveform, rate, FILM_AUDIO_RATE)
    channels = int(waveform.shape[-2])
    if channels < FILM_AUDIO_CHANNELS:
        waveform = waveform.repeat_interleave(FILM_AUDIO_CHANNELS // max(channels, 1), dim=-2)[..., :FILM_AUDIO_CHANNELS, :]
    elif channels > FILM_AUDIO_CHANNELS:
        waveform = waveform[..., :FILM_AUDIO_CHANNELS, :]
    return {"waveform": waveform.contiguous(), "sample_rate": FILM_AUDIO_RATE}


def _unit_frames(frames: torch.Tensor) -> torch.Tensor:
    """Float frames in 0-1 (8-bit frames are scaled)."""
    return frames.float().div_(255) if frames.dtype == torch.uint8 else frames.float()


def _join_audio(parts: list) -> dict | None:
    parts = [part for part in parts if isinstance(part, dict)]
    if not parts:
        return None
    rate = int(parts[0]["sample_rate"])
    return {"waveform": torch.cat([part["waveform"].cpu() for part in parts], dim=-1), "sample_rate": rate}


def _prepare_fixed_segments(h3_context, builder, length: int, overlap: int, max_segments: int,
                            start: str, width: int, height: int, fps: float, soundtrack: bool = True):
    """Fixed-length segmentation: one ``segment_frames`` window per run (no shot list)."""
    step = length - overlap
    total = _segment_count(builder.source_frames, length, step)
    count = min(total, max_segments) if max_segments > 0 else total
    signature = ("fixed", length, overlap, count, width, height)
    # Setup may run again for every segment (e.g. with run-time prompt optimization):
    # an equal content signature still continues the same video.
    same_request = _RUN.get("builder") is builder or (
        bool(builder.signature) and _RUN.get("builder_signature") == builder.signature)
    # No user-facing switch any more: a partial run continues, a finished one and
    # any parameter/media change start over (change a param to force a restart).
    resume = (same_request and _RUN.get("signature") == signature
              and 0 < int(_RUN.get("next", 0)) < count and _RUN.get("previous") is not None)
    if not resume:
        _RUN.clear()
        _RUN.update(mode="legacy", signature=signature, next=0, previous=None, images=[], audio=[])
    _RUN.update(builder=builder, builder_signature=builder.signature)
    index = int(_RUN["next"])
    frame_start = index * step
    conditioning, latent = builder.build(frame_start, length, width, height, soundtrack=soundtrack)
    if index > 0:
        previous_video, _audio = _split_av(_RUN["previous"]["samples"])
        new_video, _audio = _split_av(latent["samples"])
        tail = _RUN.get("tail")
        if previous_video.shape[-2:] != new_video.shape[-2:] and tail is not None and int(tail.shape[0]):
            # Two-pass: the collected latent is the 2nd-pass canvas, this one the
            # 1st pass; continue from the decoded tail frames like shot blocks do.
            conditioning = _apply_tail_guide(conditioning, h3_context.video_vae, tail, overlap, width, height, length)
        else:
            latent = _masked_continuation(latent, _RUN["previous"], overlap)
    state = {"run": _RUN, "mode": "legacy", "index": index, "count": count, "total": total, "overlap": overlap,
             "source_frames": builder.source_frames, "source_audio": builder.source_audio,
             "source_window": getattr(builder, "source_window", None),
             "video_vae": h3_context.video_vae, "audio_vae": h3_context.audio_vae, "fps": fps,
             "window_start": frame_start, "window_length": length,
             "skip": 0 if index == 0 else overlap, "keep": length - (0 if index == 0 else overlap)}
    return conditioning, latent, state, index, count, total


def _prepare_shot_block(h3_context, builder, shot: dict, overlap: int, width: int, height: int, fps: float,
                        soundtrack: bool = True, context: tuple[int, int] | None = None):
    """One sampling segment of the current markdown shot block.

    ``context`` = (window frames, overlap frames): the whole block is one
    segment sampled with joint context windows (only blocks with reference
    video; a talk block without it keeps the sequential segments).
    """
    fingerprint = str(shot.get("fingerprint") or "")
    block = int(shot.get("block") or 1)
    blocks = int(shot.get("blocks") or 1)
    series = str(shot.get("series") or "")
    block_start = int(shot.get("start_frame") or 0)
    block_frames = int(shot.get("frames") or 0)
    continues = bool(shot.get("continue"))
    if block_frames < 1:
        raise ValueError("分镜提示词：本块的帧数无效，请检查 md 的 @range。")
    if context is not None and not shot.get("video", True):
        LOGGER.info("Easy H3 motion transfer: 分镜 %d 没有参考视频（口播块），上下文窗口不适用，按帧窗口分段", block)
        context = None
    if context is not None:
        skip = min(overlap if continues else 0, block_start)
        plan = [_Segment(block_start - skip, _ceil_grid(block_frames + skip), skip, block_frames)]
    else:
        plan = _block_segments(block_frames, block_start, overlap, continues, shot.get("splits"))
    same_run = (_RUN.get("mode") == "block" and _RUN.get("fingerprint") == fingerprint
                and _RUN.get("series") == series)
    if not same_run or int(_RUN.get("block") or 0) != block:
        # Keep the finished block's tail: the next block may continue it.
        carried = _RUN.get("tail") if same_run and int(_RUN.get("block") or 0) == block - 1 else None
        _RUN.clear()
        _RUN.update(mode="block", fingerprint=fingerprint, block=block, series=series,
                    segment=0, tail=carried, images=[], audio=[])
    index = int(_RUN.get("segment") or 0)
    if index >= len(plan):
        raise ValueError("动作迁移：分镜块的段号超出范围，请把「分镜提示词」的 start 设为从头开始后重新运行。")
    segment = plan[index]
    tail = _RUN.get("tail")
    if segment.skip and (tail is None or int(getattr(tail, "shape", (0,))[0]) == 0):
        if index == 0 and continues:
            tail = load_tail(series, block - 1)
            _RUN["tail"] = tail
    first_frame = getattr(builder, "first_frame", None)
    if segment.skip and (tail is None or int(getattr(tail, "shape", (0,))[0]) == 0):
        LOGGER.warning("动作迁移：分镜 %d 第 %d 段缺少上一段的尾帧，改为从头生成%s",
                       block, index + 1, "，用 @first 首帧图锚定开头" if first_frame is not None and index == 0
                       else "（接缝可能可见）")
        segment = _Segment(segment.start + segment.skip, _ceil_grid(segment.keep), 0, segment.keep)
    # @first anchors only a block's opening segment that has no tail; later segments follow their tail.
    first = first_frame if index == 0 and not segment.skip else None
    if first is not None:
        LOGGER.info("Easy H3 motion transfer: 分镜 %d 用 @first 首帧图作为第 0 帧关键帧", block)
    windows = None
    if context is not None:
        windows = context_windows.plan_windows(segment.length, *context)
        if len(windows) < 2:
            windows = None
        else:
            LOGGER.info("Easy H3 motion transfer: 分镜 %d 上下文窗口 %d 个（每个 %d 帧，起点 %s），整块一次采样",
                        block, len(windows), windows[0].frames,
                        ", ".join(str(segment.start + window.frame_start) for window in windows))
    # Reference videos keep the size set where they were loaded (VHS, or the Media card's 媒体设置).
    # ``end`` stops the window at the block's last frame instead of showing the next shot.
    conditioning, latent = _clip_conditioning(h3_context, builder, segment.start, segment.length, width, height,
                                              windows, _RUN.get("tail"), segment.skip, first,
                                              end=block_start + block_frames, soundtrack=soundtrack)
    state = {"run": _RUN, "mode": "block", "index": index, "count": len(plan), "skip": segment.skip,
             "windows": windows, "context_windows": len(windows) if windows else 0, "first": first,
             "sound": str(shot.get("sound") or ""),
             "keep": segment.keep, "overlap": overlap, "block": block, "blocks": blocks, "series": series,
             "is_last": (bool(shot["is_last"]) if "is_last" in shot else block >= blocks),
             "fingerprint": fingerprint, "block_start": block_start, "block_frames": block_frames,
             "source_frames": builder.source_frames, "source_audio": builder.source_audio,
             "source_window": getattr(builder, "source_window", None),
             "video_vae": h3_context.video_vae, "audio_vae": h3_context.audio_vae, "fps": fps,
             "window_start": segment.start, "window_length": segment.length}
    return conditioning, latent, state, index, len(plan), blocks


def _window_context(h3_context, builder, state, conditioning, latent, soundtrack: bool):
    """This segment as an H3 context for the 1st / 2nd Sample Enhancers.

    Its conditioning and latent are the segment window's; the rebuilder
    re-encodes the same window (and the tail guide) at another canvas, so the
    2nd pass upscales this segment exactly like a single Easy H3 run.
    """
    start, length = int(state["window_start"]), int(state["window_length"])
    skip = int(state.get("skip") or 0)
    end = (int(state["block_start"]) + int(state["block_frames"])) if state.get("mode") == "block" else None
    tail = state["run"].get("tail") if skip else None
    windows = state.get("windows")
    first = state.get("first")

    def build(width, height, trigger_prefix=""):
        options = {"soundtrack": soundtrack, "trigger_prefix": trigger_prefix}
        if end is not None:
            options["end"] = end
        return _clip_conditioning(h3_context, builder, start, length, width, height, windows, tail,
                                  skip if tail is not None else 0, first, **options)

    return replace(h3_context, conditioning=conditioning, latent=latent, dual_plan=None,
                   highres_rebuilder=H3HighresRebuilder(build=build, canvas_dependent=True))


def _first_pass(model, context, sampler_name, scheduler, steps, first_steps, cfg):
    """The 1st Sample Enhancer on this segment's window: guider, sampler, sigmas, latent, context with the 2nd-pass plan.

    Same code path as the 1st Sample Enhancer node, without semantic bridge and
    default trigger words.
    """
    return FeiHouEasyH3DualSample1st.prepare(model, context, int(steps), int(first_steps), sampler_name,
                                             False, NONE_OPTION, 0.0, float(cfg), "", scheduler, False)


def _segment_noise():
    """The node has no seed: the noise output is random every run.

    Workflows set the seed on their own noise node (e.g. FeiHou Toolbox 随机种子噪波)
    wired to the samplers; this output only keeps older single-pass links working.
    """
    return custom_sampler.Noise_RandomNoise(random.randrange(1 << 63))


def _feeds_second_pass(prompt, unique_id, output_index: int) -> bool:
    """Does this node's ``output_index`` output go into a 2nd Sample Enhancer?"""
    if not isinstance(prompt, dict) or unique_id is None:
        return False
    for node in prompt.values():
        if not isinstance(node, dict) or node.get("class_type") not in SECOND_PASS_CLASSES:
            continue
        for value in (node.get("inputs") or {}).values():
            if (isinstance(value, (list, tuple)) and len(value) == 2 and str(value[0]) == str(unique_id)
                    and str(value[1]) == str(output_index)):
                return True
    return False


class FeiHouEasyH3MotionSegment:
    """Prepare one motion-transfer segment for the official SamplerCustomAdvanced."""

    CATEGORY = "FeiHou Easy H3"
    FUNCTION = "prepare"
    # New outputs are appended so saved links keep their slots.
    RETURN_TYPES = ("NOISE", "GUIDER", "SAMPLER", "SIGMAS", "LATENT", STATE_TYPE, "VAE", "VAE", "FLOAT",
                    "MINIMAX_H3_CONTEXT")
    RETURN_NAMES = ("noise", "guider", "sampler", "sigmas", "latent", "motion_state", "video_vae", "audio_vae", "fps",
                    "h3_context")
    DESCRIPTION = (
        "动作迁移分段：按参考视频 1 的长度自动分段，每次排队准备一段——所有参考视频及其音轨截到同一时间窗，"
        "后一段用上一段结尾接续。noise/guider/sampler/sigmas/latent 接官方自定义采样器（高级）。"
        "单次采样：采样器 output 与 motion_state 接「动作迁移收束」。"
        "二段式（一采）：这里就是一采（等同 1st Sample Enhancer），采样器 denoised_output 和 h3_context 接 2nd Sample Enhancer，"
        "二采采样器 output 与 motion_state 接「动作迁移收束」。Setup 需选「参考生视频」。"
    )

    @classmethod
    def INPUT_TYPES(cls):
        return cls._input_types_hidden({
            "required": {
                "model": ("MODEL", {"tooltip": "采样模型（Setup 的模型，可先接注意力补丁）。本节点内部用官方 ModelSamplingMiniMaxH3 设置 shift。"}),
                "h3_context": ("MINIMAX_H3_CONTEXT", {"tooltip": "接 Setup 的 h3_context（参考生视频模式）。画幅、提示词、参考图都取自 Setup。"}),
                "sampler_name": (SEGMENT_SAMPLERS, {"default": "euler",
                                 "tooltip": "采样器。dual_clock_euler（视频/音频各走各的时钟）只用于二段式，二采沿用这里的选择。"}),
                "scheduler": (comfy.samplers.SCHEDULER_NAMES, {"default": "simple"}),
                "steps": ("INT", {"default": 8, "min": 1, "max": 200,
                                  "tooltip": "单次采样：每段采样步数（加速 LoRA 常用 4~8）。二段式：调度总步数，一采跑前 first_steps 步。"}),
                "first_steps": ("INT", {"default": 4, "min": 1, "max": 199,
                                        "tooltip": "仅二段式（本节点的 h3_context 接到 2nd Sample Enhancer 时自动按二段式）：一采只跑前几步（高噪声段），必须小于 steps。"}),
                "shift_video": ("FLOAT", {"default": SHIFT_VIDEO, "min": 0.01, "max": 100.0, "step": 0.01,
                                         "tooltip": "单次采样的视频 sigma shift；二段式固定用 H3 默认 12 / 3（与 1st Sample Enhancer 相同）。"}),
                "shift_audio": ("FLOAT", {"default": SHIFT_AUDIO, "min": 0.01, "max": 100.0, "step": 0.01}),
                "cfg": ("FLOAT", {"default": 1.0, "min": 1.0, "max": 20.0, "step": 0.1, "round": 0.01,
                                  "tooltip": "加速 LoRA 保持 1（BasicGuider）；大于 1 时用 CFGGuider（空负面）。"}),
            },
            "optional": {
                "shot_prompts": (SHOT_TYPE, {"forceInput": True, "tooltip":
                    "接「分镜提示词」节点的 production_shot（同一个输出再拉一根线过来）。接上后按 md 的分镜块跑："
                    "15 秒以内的块整块采样，更长的按 @split 或平均分成每段不超过 15 秒，块内和 @continue 的块用尾部帧接续；不接则按每段帧数固定分段。"}),
                "reference_soundtrack": ([SOUNDTRACK_TO_MODEL, SOUNDTRACK_COLLECT_ONLY], {"default": SOUNDTRACK_TO_MODEL,
                    "tooltip": "参考视频 1 的原声要不要送进模型。送入模型：原声作为 <Audio 1> 参与生成（人物替换跟唱、对口型用），音色参考等音频卡顺延成 <Audio 2> 起。"
                               "不送入模型：视频只做动作参考，原声不编码（参考 token 更少），音频卡的第一条就是 <Audio 1>；原声照样交给「动作迁移收束」按 @sound / audio_output 输出。"}),
                # Saved workflows store widgets by position: the frontend
                # (feihou_segment_layout) maps older saves onto this order by name.
                "segmentation": ([SEGMENTATION_CHAIN, SEGMENTATION_CONTEXT], {"default": SEGMENTATION_CHAIN,
                    "tooltip": "分段方式（只对接了分镜提示词、长于一个窗口的跳舞/动作块生效）。"
                               "帧窗口：块内按 15 秒分段逐段采样，后一段锁定上一段解码出的尾部帧接上，段数越多细节越掉、噪点越多。"
                               "上下文窗口：整块一次采样，每一步把整块 latent 切成互相重叠的窗口分别预测、在重叠处线性混合，"
                               "每个窗口拿自己时间段的参考视频（参考图和提示词每个窗口都带），窗口之间不传解码画面，画质不随长度累积变差；"
                               "单块一次跑完（不再自动排队块内下一段），@split 不再分段。二段式的一采和 2nd Sample Enhancer 都按窗口采样。"
                               "没有参考视频的口播块自动按帧窗口处理。"}),
                "frame_window": ("BOOLEAN", {"default": True, "label_on": "帧窗口分段", "label_off": "整段一次采完",
                    "tooltip": "只在没接「分镜提示词」时生效。帧窗口分段：按 segment_frames 把参考视频 1 逐段采样，"
                               "后一段接上一段结尾 overlap_frames 帧，直到整段跑完。整段一次采完：参考视频 1 整段一次采样，"
                               "segment_frames 和 overlap_frames 不起作用（视频长时很吃显存）。接了分镜提示词时按 md 分块。"}),
                "segment_frames": (SEGMENT_FRAME_CHOICES, {"default": SEGMENT_FRAME_DEFAULT,
                                           "tooltip": "固定分段的每段帧数（24fps），H3 只接受 5+17N 帧：124 帧 ≈ 5.17 秒，其它可选 107、141、158…。"
                                                      "接「分镜提示词」时本项不决定段长：15 秒以内的块整块一段，更长的按 @split（没写就平均）分成每段不超过 15 秒，它只用于重叠帧数的上限校验。"}),
                "overlap_frames": (OVERLAP_CHOICES, {"default": "22",
                                                    "tooltip": "相邻两段重叠帧数：上一段结尾这么多帧的 latent 被锁定作为下一段开头。22 帧 = 7 个视频 token。"}),
                "context_window_frames": (CONTEXT_WINDOW_CHOICES, {"default": CONTEXT_WINDOW_DEFAULT,
                    "tooltip": "仅上下文窗口：每个窗口的帧数（24fps，都是 H3 要求的 5+17N），124 ≈ 5.2 秒到 362 ≈ 15.1 秒。"
                               "默认 345 ≈ 14.4 秒，和平时单段一样长，显存也和单段相当；窗口越短，窗口数越多、越慢。"}),
                "context_overlap_frames": (CONTEXT_OVERLAP_CHOICES, {"default": CONTEXT_OVERLAP_DEFAULT,
                    "tooltip": "仅上下文窗口：相邻窗口至少重叠的帧数，在重叠处线性混合。34 ≈ 1.4 秒、68 ≈ 2.8 秒（默认）、102 ≈ 4.3 秒；"
                               "窗口从 17 帧的格点开始，所以重叠是 17 的倍数。越大接缝越稳；窗口起点在块内均匀分布，实际重叠往往更大。"}),
            },
        })

    @classmethod
    def _input_types_hidden(cls, types):
        types["hidden"] = {"prompt": "PROMPT", "unique_id": "UNIQUE_ID"}
        return types

    @classmethod
    def VALIDATE_INPUTS(cls, context_window_frames=CONTEXT_WINDOW_DEFAULT, segmentation=SEGMENTATION_CHAIN,
                        segment_frames=SEGMENT_FRAME_DEFAULT):
        # Saved before the option list: a bare frame count (aligned down to 5+17N when used).
        try:
            frames = _window_frames(segment_frames)
        except (TypeError, ValueError):
            return f"segment_frames 无效：{segment_frames!r}"
        if not SEGMENT_FRAME_RANGE[0] <= frames <= SEGMENT_FRAME_RANGE[1]:
            return f"segment_frames 必须在 {SEGMENT_FRAME_RANGE[0]}～{SEGMENT_FRAME_RANGE[1]} 帧之间：{segment_frames!r}"
        # Workflows saved with the earlier bare-number options ("345") or 接续 still run.
        if SEGMENTATION_LEGACY.get(str(segmentation), str(segmentation)) not in (SEGMENTATION_CHAIN, SEGMENTATION_CONTEXT):
            return f"segmentation 必须是「{SEGMENTATION_CHAIN}」或「{SEGMENTATION_CONTEXT}」：{segmentation!r}"
        try:
            frames = _window_frames(context_window_frames)
        except (TypeError, ValueError):
            return f"context_window_frames 无效：{context_window_frames!r}"
        return True if _window_label(frames) in CONTEXT_WINDOW_CHOICES else \
            f"context_window_frames 必须是 5+17N 且在 124～362 帧之间：{context_window_frames!r}"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")  # the segment index advances on every run

    @staticmethod
    def _prepare_passthrough(model, h3_context, builder, shot: dict, sampler_name, width, height, fps,
                             first_pass=None):
        """「原片直出」block: one run, no sampling steps, the source frames go to Collect.

        An empty sigma list makes ComfyUI's guider return the latent at once,
        without loading the model, so the official sampler node costs nothing.
        """
        block = int(shot.get("block") or 1)
        blocks = int(shot.get("blocks") or 1)
        series = str(shot.get("series") or "")
        fingerprint = str(shot.get("fingerprint") or "")
        block_start = int(shot.get("start_frame") or 0)
        block_frames = int(shot.get("frames") or 0)
        if block_frames < 1:
            raise ValueError("分镜提示词：本块的帧数无效，请检查 md 的 @range。")
        same_run = (_RUN.get("mode") == "block" and _RUN.get("fingerprint") == fingerprint
                    and _RUN.get("series") == series)
        carried = _RUN.get("tail") if same_run and int(_RUN.get("block") or 0) == block - 1 else None
        _RUN.clear()
        _RUN.update(mode="block", fingerprint=fingerprint, block=block, series=series,
                    segment=0, tail=carried, images=[], audio=[])
        state = {"run": _RUN, "mode": "block", "passthrough": True, "index": 0, "count": 1, "skip": 0,
                 "sound": str(shot.get("sound") or ""),
                 "keep": block_frames, "overlap": 0, "block": block, "blocks": blocks, "series": series,
                 "is_last": bool(shot["is_last"]) if "is_last" in shot else block >= blocks,
                 "fingerprint": fingerprint, "block_start": block_start, "block_frames": block_frames,
                 "source_frames": builder.source_frames, "source_audio": builder.source_audio,
                 "source_window": getattr(builder, "source_window", None),
                 "video_vae": h3_context.video_vae, "audio_vae": h3_context.audio_vae, "fps": fps,
                 "width": width, "height": height,
                 "window_start": block_start, "window_length": block_frames}
        sigmas = torch.zeros(0)
        context = h3_context
        if first_pass is not None:
            # Two-pass: the 2nd Sample Enhancer needs a 1st-pass context; its
            # sampling is wasted work, Collect outputs the source frames anyway.
            guider, sampler, _sigmas, _latent, context = first_pass(h3_context)
        else:
            sampler = _node_value(custom_sampler.KSamplerSelect.execute(sampler_name))
            guider = _guider(model, h3_context.conditioning, 1.0, None)
        LOGGER.info("Easy H3 motion transfer: shot block %d/%d 原片直出（不采样），原片帧 %d-%d，%dx%d",
                    block, blocks, block_start, block_start + block_frames - 1, width, height)
        return (_segment_noise(), guider, sampler, sigmas, h3_context.latent, state,
                h3_context.video_vae, h3_context.audio_vae, fps, context)

    @staticmethod
    def prepare(model, h3_context, sampler_name, scheduler, steps, shift_video, shift_audio, cfg,
                segment_frames=SEGMENT_FRAME_DEFAULT,
                overlap_frames="22", start=START_AUTO, shot_prompts=None,
                reference_soundtrack=SOUNDTRACK_TO_MODEL, sampling_mode=SAMPLING_SINGLE, first_steps=4,
                segmentation=SEGMENTATION_CHAIN, context_window_frames=CONTEXT_WINDOW_DEFAULT,
                context_overlap_frames=CONTEXT_OVERLAP_DEFAULT, frame_window=True, prompt=None, unique_id=None,
                **_removed):
        if not isinstance(h3_context, MiniMaxH3Context):
            raise ValueError("h3_context 必须接 FeiHou Easy H3 Setup 的 h3_context。")
        builder = getattr(h3_context, "segment_builder", None)
        if builder is None:
            raise ValueError("动作迁移需要 Setup 选择「参考生视频」模式（并重新运行 Setup）。")
        if builder.source_frames < 5:
            raise ValueError("动作迁移需要至少一个参考视频：参考视频 1 决定总长度。")

        length = _grid_frames(_window_frames(segment_frames))
        whole = not frame_window and not isinstance(shot_prompts, dict)
        if whole:
            # 整段一次采完: one segment as long as reference video 1.
            length = _ceil_grid(builder.source_frames)
            LOGGER.info("Easy H3 motion transfer: 帧窗口关闭，参考视频 1 整段 %d 帧一次采样", length)
        overlap = int(overlap_frames)
        if overlap >= length:
            raise ValueError("重叠帧数必须小于每段帧数。")
        video, _audio = _split_av(h3_context.latent["samples"])
        width, height = int(video.shape[-1]) * VAE_DOWNSAMPLE, int(video.shape[-2]) * VAE_DOWNSAMPLE
        fps = float(h3.FPS)
        soundtrack = str(reference_soundtrack) != SOUNDTRACK_COLLECT_ONLY

        # The graph decides: h3_context into a 2nd Sample Enhancer = this node is the 1st pass.
        # (sampling_mode only matters without a prompt, e.g. in tests or old API calls.)
        context_index = list(FeiHouEasyH3MotionSegment.RETURN_NAMES).index("h3_context")
        two_pass = (_feeds_second_pass(prompt, unique_id, context_index) if prompt is not None
                    else str(sampling_mode) == SAMPLING_TWO_PASS)
        if not two_pass and sampler_name not in comfy.samplers.SAMPLER_NAMES:
            raise ValueError(f"动作迁移分段：{sampler_name} 只用于二段式。二段式要把本节点的 h3_context 输出接到 2nd Sample Enhancer（不能接 Setup 的 h3_context）；单次采样请选 euler 等 ComfyUI 原生采样器。")
        if two_pass and not 1 <= int(first_steps) < int(steps):
            raise ValueError("动作迁移分段：二段式的 first_steps 必须小于 steps（总步数）。")

        def first_pass(context):
            return _first_pass(model, context, sampler_name, scheduler, steps, first_steps, cfg)

        shot = shot_prompts.get("motion") if isinstance(shot_prompts, dict) else None
        if shot and shot.get("passthrough"):
            return FeiHouEasyH3MotionSegment._prepare_passthrough(model, h3_context, builder, shot, sampler_name,
                                                                  width, height, fps,
                                                                  first_pass if two_pass else None)
        context = None
        if str(segmentation) == SEGMENTATION_CONTEXT and not CONTEXT_WINDOWS_ENABLED:
            LOGGER.info("Easy H3 motion transfer: 上下文窗口暂时关闭，按帧窗口分段")
        elif str(segmentation) == SEGMENTATION_CONTEXT:
            context = (_grid_frames(_window_frames(context_window_frames)), int(context_overlap_frames))
            if not shot:
                LOGGER.warning("Easy H3 motion transfer: 上下文窗口需要接「分镜提示词」，固定分段仍按帧窗口采样")
        if shot:
            conditioning, latent, state, index, count, total = _prepare_shot_block(
                h3_context, builder, shot, overlap, width, height, fps, soundtrack, context)
        else:
            conditioning, latent, state, index, count, total = _prepare_fixed_segments(
                h3_context, builder, length, overlap, 0, start, width, height, fps, soundtrack)

        context = _window_context(h3_context, builder, state, conditioning, latent, soundtrack)
        noise = _segment_noise()
        if two_pass:
            guider, sampler, sigmas, latent, context = first_pass(context)
            LOGGER.info("Easy H3 motion transfer: %s %d/%d (of %d), reference frames %d-%d, keep %d, 一采 %dx%d，%d/%d 步",
                        "shot block" if shot else "segment", index + 1, count, total,
                        state["window_start"], state["window_start"] + state["window_length"] - 1,
                        state["keep"], width, height, int(first_steps), int(steps))
            return (noise, guider, sampler, sigmas, latent, state, h3_context.video_vae, h3_context.audio_vae, fps,
                    context)

        shifted = _node_value(h3.MiniMaxH3SigmaShift.execute(model, float(shift_video), float(shift_audio)))
        sigmas = _node_value(custom_sampler.BasicScheduler.execute(shifted, scheduler, int(steps), 1.0))
        sampler = _node_value(custom_sampler.KSamplerSelect.execute(sampler_name))
        negative = _encode_negative(h3_context.clip, "") if float(cfg) > 1.0 else None
        guider = _guider(shifted, conditioning, cfg, _with_canvas(negative, conditioning) if negative is not None else None)

        LOGGER.info("Easy H3 motion transfer: %s %d/%d (of %d), reference frames %d-%d, keep %d, %dx%d",
                    "shot block" if shot else "segment", index + 1, count, total,
                    state["window_start"], state["window_start"] + state["window_length"] - 1,
                    state["keep"], width, height)
        return (noise, guider, sampler, sigmas, latent, state, h3_context.video_vae, h3_context.audio_vae, fps,
                context)


class FeiHouEasyH3MotionCollect:
    """Collect sampled motion-transfer segments; output the joined video, or each segment for Video Combine V2."""

    CATEGORY = "FeiHou Easy H3"
    FUNCTION = "collect"
    OUTPUT_NODE = True
    RETURN_TYPES = ("IMAGE", "AUDIO", "AUDIO", "LATENT", "FLOAT")
    RETURN_NAMES = ("images", "audio", "generated_audio", "series_latent", "fps")
    DESCRIPTION = (
        "动作迁移收束：接官方自定义采样器的 output 和「动作迁移分段」的 motion_state。每段解码、去掉开头重叠帧并自动排队下一段。"
        "接「分镜提示词」时，每个分镜块跑完输出这一块的 images/audio，series_latent 接 Video Combine V2 的 latent，"
        "由 V2 逐块保存、最后拼成「_完整」视频；不接分镜（固定分段）时，中间段不输出，最后一段完成后输出拼好、裁到源长度的整条视频。"
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "latent": ("LATENT", {"tooltip": "接官方自定义采样器（高级）的 output。"}),
                "motion_state": (STATE_TYPE, {"tooltip": "接「动作迁移分段」的 motion_state。"}),
                "audio_output": ([AUDIO_AUTO, AUDIO_SOURCE, AUDIO_GENERATED], {"default": AUDIO_AUTO,
                                 "tooltip": "audio 输出。自动：每块按分镜 md 的 @sound（原声 / 生成），没写 @sound 的块和固定分段用原声（参考视频 1 没有音轨时用生成音频）。"
                                            "原声音频 / 生成音频：所有块一律用这一种，忽略 @sound。generated_audio 始终是生成音频。"}),
                "auto_queue": ("BOOLEAN", {"default": True, "label_on": "自动排队下一段", "label_off": "手动排队",
                                           "tooltip": "开启后每段完成自动排队下一段，直到本块或整条跑完；关闭则每段手动点一次运行。"}),
            },
            "optional": {
                "color_match": ("BOOLEAN", {"default": False, "label_on": "颜色匹配原片", "label_off": "不调色",
                                            "tooltip": "修正 H3 生成时的整体偏色（对比、饱和、色温）：每块按原片切点分镜头，只拿没被替换的背景和参考视频 1 同一时间段比较，"
                                                       "算出的校正套用到整个镜头（含替换后的人物），人物不会被拉向原表演者的颜色。"
                                                       "可对比的背景太少（人占满画面）或背景本身被换掉（换新场景）的镜头自动跳过；每镜一套参数，不会闪烁。"}),
                "save_segments": ("BOOLEAN", {"default": False, "label_on": "保存每段视频", "label_off": "不保存分段",
                                              "tooltip": "开启后块内每一段采样完就把这一段（已去掉开头接续用的重叠帧，配这一块选用的声音）存成 mp4 到输出目录，"
                                                         "方便逐段检查；整块视频照常由 Video Combine V2 保存。"}),
                "filename_prefix": ("STRING", {"default": "FeiHou_H3_motion/segment",
                                               "tooltip": "分段视频的文件名前缀（相对输出目录），后面会加 _b块号_seg段号。"}),
                "color_match_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05,
                                                   "tooltip": "调色强度：1 = 完全对齐原片，0.5 = 一半。换上去的人颜色和原片差很多（如金色紧身裤换黑裤）时可适当调低。"}),
            },
        }

    @staticmethod
    def _save_segment(frames, audio, fps, prefix, label):
        """Write one collected segment as an H.264 mp4 in the output folder."""
        frames = frames.float().clamp(0, 1)
        height, width = int(frames.shape[1]), int(frames.shape[2])
        folder, filename, counter, subfolder, _ = folder_paths.get_save_image_path(
            str(prefix or "FeiHou_H3_motion/segment"), folder_paths.get_output_directory(), width, height)
        name = f"{filename}_{counter:05}_{label}.mp4"
        video = InputImpl.VideoFromComponents(Types.VideoComponents(
            images=frames, audio=audio, frame_rate=Fraction(round(float(fps) * 1000), 1000)))
        video.save_to(os.path.join(folder, name), format=Types.VideoContainer.MP4, codec=Types.VideoCodec.H264)
        LOGGER.info("Easy H3 motion transfer: saved segment %s to %s", label, os.path.join(subfolder, name))

    @staticmethod
    def _audio_for(run, motion_state, audio_output, start, frames, fps):
        """Soundtrack of output frames [start, start + frames): source audio or generated audio."""
        if audio_output == AUDIO_SOURCE and motion_state["source_audio"] is not None:
            return _audio_slice(motion_state["source_audio"], start, frames, fps)
        if audio_output == AUDIO_SOURCE:
            LOGGER.warning("Easy H3 motion transfer: reference video 1 has no soundtrack; using generated audio")
        generated = _join_audio(run["audio"])
        return _audio_slice(generated, start, frames, fps) if generated is not None else None

    @staticmethod
    def _block_audio_output(motion_state, audio_output):
        """自动 follows the block's @sound (原声 / 生成), else the source; the other choices force one."""
        if str(audio_output) != AUDIO_AUTO:
            return audio_output
        sound = str(motion_state.get("sound") or "")
        return AUDIO_GENERATED if sound == "generated" else AUDIO_SOURCE

    @staticmethod
    def _collect_shot_block(latent, motion_state, audio_output, auto_queue, color_match=False, color_match_strength=1.0,
                            save_segments=False, filename_prefix=""):
        """Collect one sampling segment of a shot block; save the block on its last segment."""
        audio_output = FeiHouEasyH3MotionCollect._block_audio_output(motion_state, audio_output)
        if motion_state.get("passthrough"):
            return FeiHouEasyH3MotionCollect._collect_passthrough(motion_state, audio_output, auto_queue)
        run = motion_state["run"]
        index, count, fps = int(motion_state["index"]), int(motion_state["count"]), float(motion_state["fps"])
        if int(run.get("segment") or 0) != index:
            raise ValueError("动作迁移：分段状态已变化，请把「分镜提示词」的 start 设为从头开始后重新运行。")
        block, blocks = int(motion_state["block"]), int(motion_state["blocks"])
        block_start, block_frames = int(motion_state["block_start"]), int(motion_state["block_frames"])
        overlap = int(motion_state.get("overlap") or 0)
        last_segment = index + 1 >= count

        sampled = {"samples": latent["samples"]}
        if int(motion_state.get("context_windows") or 0) > 1:
            # A whole long block: decode it in clip-aligned pieces (identical
            # frames) and keep 8-bit frames, so the float video never exists at once.
            vae = motion_state["video_vae"]
            video, _audio = _split_av(latent["samples"])
            decoded = torch.cat([
                (piece.detach().cpu().clamp(0, 1) * 255).round().to(torch.uint8)
                for piece in context_windows.decode_video_chunked(
                    lambda z: nodes.VAEDecode().decode(vae, {"samples": z})[0], video)])
        else:
            decoded = nodes.VAEDecode().decode(motion_state["video_vae"], sampled)[0]
        decoded_frames = int(decoded.shape[0])
        skip = min(max(0, int(motion_state.get("skip") or 0)), max(0, decoded_frames - 1))
        keep = max(1, min(int(motion_state.get("keep") or decoded_frames), decoded_frames - skip))
        segment = decoded[skip:skip + keep].detach().cpu()
        if segment.dtype == torch.uint8:
            run.setdefault("images", []).append(segment)
        else:
            run.setdefault("images", []).append((segment.clamp(0, 1) * 255).round().to(torch.uint8))
        sound = _node_value(comfy_audio_nodes.VAEDecodeAudio.execute(motion_state["audio_vae"], sampled))
        run.setdefault("audio", []).append(_audio_slice(sound, skip, keep, fps))
        if save_segments:
            source_audio = motion_state.get("source_audio")
            if audio_output == AUDIO_SOURCE and source_audio is not None:
                segment_audio = _audio_slice(source_audio, int(motion_state.get("window_start") or 0) + skip, keep, fps)
            else:
                segment_audio = run["audio"][-1]
            FeiHouEasyH3MotionCollect._save_segment(_unit_frames(segment), segment_audio, fps, filename_prefix,
                                                    f"b{block:03d}_seg{index + 1:02d}")
        # The next segment (or a continued block) anchors to these frames.
        run["tail"] = _unit_frames(segment[-overlap:]) if overlap else None
        run["segment"] = index + 1
        del decoded, sound
        ui = {"feihou_motion": [{"next": bool(auto_queue), "done": index + 1, "count": count,
                                 "block": block, "blocks": blocks}]}
        LOGGER.info("Easy H3 motion transfer: block %d/%d segment %d/%d collected (%d frames)",
                    block, blocks, index + 1, count, keep)

        if not last_segment:
            blocked = ExecutionBlocker(None)
            return {"ui": ui, "result": (blocked, blocked, blocked, blocked, blocked)}

        frames = torch.cat(run["images"], dim=0)[:block_frames].float().div_(255)
        frame_count = int(frames.shape[0])
        frames = _color_matched(frames, motion_state, block_start, color_match, color_match_strength)
        generated = _join_audio(run["audio"])
        if generated is not None:
            generated = _audio_slice(generated, 0, frame_count, fps)
        source_audio = motion_state.get("source_audio")
        if audio_output == AUDIO_SOURCE and source_audio is not None:
            audio = _audio_slice(source_audio, block_start, frame_count, fps)
        else:
            if audio_output == AUDIO_SOURCE:
                LOGGER.warning("Easy H3 motion transfer: reference video 1 has no soundtrack; using generated audio")
            audio = _audio_slice(generated, 0, frame_count, fps) if generated is not None else None
        return FeiHouEasyH3MotionCollect._finish_block(
            run, motion_state, frames, audio, generated, ui, frames[-overlap:] if overlap else None)

    @staticmethod
    def _finish_block(run, motion_state, frames, audio, generated, ui, tail):
        """Save the tail for a continued block, record it for V2, advance 镜号."""
        block, blocks = int(motion_state["block"]), int(motion_state["blocks"])
        series, fingerprint = str(motion_state["series"]), str(motion_state["fingerprint"])
        last_block = bool(motion_state.get("is_last", block >= blocks))
        if not last_block and tail is not None and int(tail.shape[0]):
            save_tail(series, block, tail)
        manifest = os.path.join(output_root(), f"{series}.json")
        series_latent = {SAVE_KEY: {"save_latent": False, "manifest": manifest, "index": block,
                                    "total": blocks, "info": {"series": series}}}
        run["images"], run["audio"], run["segment"] = [], [], 0
        progress = advance_block(fingerprint) or {}
        # The 镜号 widget is updated only now that the block is really finished:
        # an interrupted block, or the next segment of a long block, keeps it.
        finished = last_block or bool(progress.get("done"))
        ui["feihou_motion"][0]["shot_next"] = None if finished else int(progress.get("block") or block + 1)
        ui["feihou_motion"][0]["shot_done"] = finished
        LOGGER.info("Easy H3 motion transfer: block %d/%d done (%d frames, series %s)",
                    block, blocks, int(frames.shape[0]), series)
        if last_block:
            run.clear()
            ui["feihou_motion"][0]["next"] = False
        if finished:
            finish_series(series, fingerprint)
        return {"ui": ui, "result": (frames, _film_audio(audio), _film_audio(generated), series_latent,
                                     float(motion_state["fps"]))}

    @staticmethod
    def _collect_passthrough(motion_state, audio_output, auto_queue):
        """「原片直出」block: the source frames of the block, fitted to the canvas, plus the source audio."""
        run = motion_state["run"]
        block, blocks = int(motion_state["block"]), int(motion_state["blocks"])
        block_start, block_frames = int(motion_state["block_start"]), int(motion_state["block_frames"])
        width, height, fps = int(motion_state["width"]), int(motion_state["height"]), float(motion_state["fps"])
        window = motion_state.get("source_window")
        if window is None:
            raise ValueError("原片直出需要重新运行 Setup（旧的 h3_context 没有原片帧）。")
        source = window(block_start, block_frames)
        if int(source.shape[0]) == 0:
            raise ValueError(f"原片直出：参考视频 1 在第 {block_start} 帧之后没有画面，请检查 @range。")
        # Center-crop fit like the reference video gets on the canvas, in float
        # (lanczos goes through 8-bit images and would truncate every pixel), in
        # chunks to bound memory.
        frames = torch.cat([
            comfy.utils.common_upscale(source[start:start + 32, ..., :3].float().movedim(-1, 1),
                                       width, height, "bicubic", "center").movedim(1, -1).detach().cpu().clamp(0, 1)
            for start in range(0, int(source.shape[0]), 32)])
        frame_count = int(frames.shape[0])
        source_audio = motion_state.get("source_audio")
        audio = _audio_slice(source_audio, block_start, frame_count, fps) if source_audio is not None else None
        if audio_output != AUDIO_SOURCE:
            LOGGER.info("Easy H3 motion transfer: 原片直出块没有生成音频，audio 输出使用原声")
        ui = {"feihou_motion": [{"next": bool(auto_queue), "done": 1, "count": 1, "block": block, "blocks": blocks}]}
        # A following @continue block anchors to the clean source tail; keep the
        # longest overlap any segment setting can ask for.
        tail = frames[-max(int(choice) for choice in OVERLAP_CHOICES):]
        run["tail"] = tail
        LOGGER.info("Easy H3 motion transfer: block %d/%d 原片直出 %d 帧（%dx%d）", block, blocks, frame_count, width, height)
        return FeiHouEasyH3MotionCollect._finish_block(run, motion_state, frames, audio, None, ui, tail)

    @staticmethod
    def collect(latent, motion_state, audio_output, auto_queue=True, color_match=False, color_match_strength=1.0,
                save_segments=False, filename_prefix="FeiHou_H3_motion/segment"):
        if not isinstance(motion_state, dict) or "run" not in motion_state:
            raise ValueError("motion_state 必须接「动作迁移分段」节点。")
        if not isinstance(latent, dict) or not getattr(latent.get("samples"), "is_nested", False):
            raise ValueError("latent 必须接官方自定义采样器（高级）的 output（H3 音视频 latent）。")
        if str(motion_state.get("mode") or "legacy") == "block":
            return FeiHouEasyH3MotionCollect._collect_shot_block(latent, motion_state, audio_output, auto_queue,
                                                                 color_match, color_match_strength,
                                                                 save_segments, filename_prefix)
        # Fixed segments have no @sound: 自动 keeps the source soundtrack.
        audio_output = AUDIO_SOURCE if str(audio_output) == AUDIO_AUTO else audio_output
        run = motion_state["run"]
        index, count, overlap, fps = motion_state["index"], motion_state["count"], motion_state["overlap"], motion_state["fps"]
        if run.get("next") != index:
            raise ValueError("动作迁移：分段状态已变化（可能同时运行了别的分段任务），请把「动作迁移分段」的 start 设为从头开始后重新运行。")
        sampled = {"samples": latent["samples"]}
        source_frames = int(motion_state["source_frames"])
        last = index + 1 >= count

        decoded = nodes.VAEDecode().decode(motion_state["video_vae"], sampled)[0]
        skip = 0 if index == 0 else min(overlap, int(decoded.shape[0]) - 1)
        segment = decoded[skip:].detach().cpu()
        sound = _node_value(comfy_audio_nodes.VAEDecodeAudio.execute(motion_state["audio_vae"], sampled))
        offset = sum(int(part.shape[0]) for part in run["images"])
        run["images"].append((segment.clamp(0, 1) * 255).round().to(torch.uint8))
        run["audio"].append(_audio_slice(sound, skip, int(decoded.shape[0]) - skip, fps))
        if save_segments:
            if audio_output == AUDIO_SOURCE and motion_state["source_audio"] is not None:
                segment_audio = _audio_slice(motion_state["source_audio"], offset, int(segment.shape[0]), fps)
            else:
                segment_audio = run["audio"][-1]
            FeiHouEasyH3MotionCollect._save_segment(segment, segment_audio, fps, filename_prefix, f"seg{index + 1:03d}")
        run["previous"] = {"samples": comfy.nested_tensor.NestedTensor(
            tuple(t.detach().to("cpu", copy=True) for t in latent["samples"].unbind()))}
        # Decoded tail: the next segment's anchor when its latent has another size (two-pass).
        run["tail"] = _unit_frames(decoded[-overlap:].detach().cpu()) if overlap else None
        run["next"] = index + 1
        del decoded, sound
        ui = {"feihou_motion": [{"next": bool(auto_queue) and not last, "done": index + 1, "count": count}]}

        if not last:
            LOGGER.info("Easy H3 motion transfer: segment %d/%d collected", index + 1, count)
            blocked = ExecutionBlocker(None)
            return {"ui": ui, "result": (blocked, blocked, blocked, blocked, blocked)}

        frames = torch.cat(run["images"], dim=0)
        frames = frames[:min(int(frames.shape[0]), source_frames)]
        frame_count = int(frames.shape[0])
        frames = frames.float().div_(255)
        frames = _color_matched(frames, motion_state, 0, color_match, color_match_strength)
        generated_audio = _join_audio(run["audio"])
        if generated_audio is not None:
            generated_audio = _audio_slice(generated_audio, 0, frame_count, fps)
        audio = FeiHouEasyH3MotionCollect._audio_for(run, motion_state, audio_output, 0, frame_count, fps)
        run.clear()
        LOGGER.info("Easy H3 motion transfer: all %d segment(s) done, %d frames", count, frame_count)
        return {"ui": ui, "result": (frames, _film_audio(audio), _film_audio(generated_audio), None, fps)}
