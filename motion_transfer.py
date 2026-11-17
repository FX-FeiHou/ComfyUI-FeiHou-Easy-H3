"""FeiHou Easy H3 Motion Transfer: one-pass, segment-by-segment reference-video generation.

  Setup (reference mode) -> Motion Segment -> official SamplerCustomAdvanced -> Motion Collect -> Video Combine

Two ways to cut the reference video 1 (e.g. the RGB dance):

* **Shot blocks** (``shot_prompts`` connected): the markdown shot list gives
  each block its time range and its own prompt; a block is split at its
  @split points, otherwise Motion Segment decides: 帧窗口分段 cuts it into
  ``segment_frames`` clips, 不分段 samples it whole; the block is saved
  as one video.  Consecutive segments inside a block,
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

import copy
import json
import hashlib
import logging
import math
import os
import random
import uuid
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
from . import remake as remake_mode
from .dual_sample import (
    NONE_OPTION, SHIFT_AUDIO, SHIFT_VIDEO, VAE_DOWNSAMPLE, FeiHouEasyH3DualSample1st,
    _encode_negative, _guider, _node_value, _split_av, _with_canvas,
)
from .nodes import H3HighresRebuilder, MiniMaxH3Context, _window_audio, deferred_text_encoding, has_pending_text, resolve_text_batch
from .production_continuation import SAVE_KEY, _audio_slice
from .production_pack import SHOT_TYPE
from .shot_prompts import (START_MANUAL, advance_block, finish_series, load_held, load_tail, output_root, save_held,
                           save_tail)
from .shot_prompts import next_run_numbers, start_mode
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
# Motion Segment 分段方式: how a block longer than one segment is sampled.
# 不分段: one run per block (an @split block is still cut at its splits).
# 帧窗口: in-block segments one after another, each continuing the previous
# one's decoded tail.  上下文窗口: the whole block in one sampler run,
# overlapping windows blended at every step (h3_context_windows); blocks
# without reference video (talk) keep 帧窗口.  segment_frames / overlap_frames
# are shared: segment length and tail overlap, or window length and window
# overlap (the frontend swaps the option lists and remembers each mode's values).
SEGMENTATION_WHOLE = "不分段"
SEGMENTATION_CHAIN = "帧窗口"
# Saved before the rename; still means 帧窗口.
SEGMENTATION_LEGACY = {"接续": SEGMENTATION_CHAIN}
CONTEXT_WINDOWS_ENABLED = True
SEGMENTATION_CONTEXT = "上下文窗口"
# 自动: each md block's @segment (or the md header's) decides; without one, 不分段.
SEGMENTATION_AUTO = "自动"
SEGMENTATION_CHOICES = [SEGMENTATION_AUTO, SEGMENTATION_WHOLE, SEGMENTATION_CHAIN, SEGMENTATION_CONTEXT]
# 不分段 / 自动 grey out the window fields: @split segments and @continue blocks join on this overlap.
WHOLE_OVERLAP = "22"
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
# The overlap of two windows on the 17-frame grid is 5 + 17k frames; saves from
# before 2026-11-13 wrote 34 / 68 / 102 for 39 / 73 / 107 (context_windows.LEGACY_OVERLAPS).
CONTEXT_OVERLAP_CHOICES = ["22", "39", "56", "73", "90", "107"]
CONTEXT_OVERLAP_DEFAULT = "73"
# overlap_frames serves both: tail overlap (帧窗口 / 不分段) or window overlap (上下文窗口).
SEGMENT_OVERLAP_CHOICES = [*OVERLAP_CHOICES, *(c for c in CONTEXT_OVERLAP_CHOICES if c not in OVERLAP_CHOICES)]
# A context-window block continued with @continue (or the fixed-length
# fallback) still anchors on the previous tail with this many frames.
CONTEXT_CHAIN_OVERLAP = 22
# 上下文窗口每段上限: a longer block is sampled in segments of at most this
# long, one segment per run; each segment starts on the previous one's last
# overlap_frames (the window overlap), anchored to its decoded frames and
# cross-faded into them, so a long film shows results early and an
# interrupted run keeps its finished segments.
CONTEXT_SEGMENT_WHOLE = "整块一次"
CONTEXT_SEGMENT_SECONDS = (20, 30, 45, 60)
CONTEXT_SEGMENT_CHOICES = [CONTEXT_SEGMENT_WHOLE, *(f"{seconds} 秒" for seconds in CONTEXT_SEGMENT_SECONDS)]
CONTEXT_SEGMENT_DEFAULT = "30 秒"
# Cross-fade between split context segments: this many frames in the middle of
# the overlap blend from the previous segment into the next one; before them the
# overlap shows the previous segment, after them the new one.  全部重叠 = the whole overlap.
CROSSFADE_ALL = "全部重叠"
CROSSFADE_CHOICES = ["6", "12", "18", "24", CROSSFADE_ALL]
CROSSFADE_DEFAULT = "12"


def _crossfade_frames(value) -> int | None:
    """Frames of a 叠化帧数 option, None for the whole overlap."""
    text = str(value or "").strip()
    if not text or text == CROSSFADE_ALL:
        return None
    return int(text)


def _context_segment_frames(value) -> int | None:
    """Frames of a 每段上限 option, None for 整块一次."""
    text = str(value or "").strip()
    if not text or text == CONTEXT_SEGMENT_WHOLE:
        return None
    return round(float(text.split()[0]) * h3.FPS)


def _segmentation(value) -> str:
    value = str(value)
    return SEGMENTATION_LEGACY.get(value, value)
# dual_clock_euler is offered only on ComfyUI builds without native H3 AV sampling (see h3_dual_clock).
SEGMENT_SAMPLERS = [*comfy.samplers.SAMPLER_NAMES,
                    *(name for name in dual_clock.SAMPLER_OPTIONS if name not in comfy.samplers.SAMPLER_NAMES)]
# An @split segment (块内分镜) holds at most 15 s of the block; the overlap
# context frames of a continued segment come on top.
SEGMENT_MAX_FRAMES = round(15.0 * h3.FPS)
# One motion-transfer run in progress (ComfyUI process memory).
_RUN: dict = {}
# Encoded reference windows (VAE + text encoder, about a minute each) of recent
# segments: re-running a shot block with another seed reuses them.
_WINDOW_CACHE: OrderedDict = OrderedDict()
# A context-window block holds a few windows per canvas.
WINDOW_CACHE_SIZE = 16


# Saved segments go where the joined video goes: the filename_prefix of the
# node fed by Collect's images (e.g. Video Combine V2), else this folder.
SEGMENT_PREFIX_FALLBACK = "FeiHou_H3_motion/segment"


def _segment_prefix(prompt, unique_id) -> str:
    if isinstance(prompt, dict) and unique_id is not None:
        for node in prompt.values():
            inputs = node.get("inputs") if isinstance(node, dict) else None
            if not isinstance(inputs, dict):
                continue
            takes_images = any(isinstance(value, (list, tuple)) and len(value) == 2
                               and str(value[0]) == str(unique_id) and str(value[1]) == "0"
                               for value in inputs.values())
            target = inputs.get("filename_prefix")
            if takes_images and isinstance(target, str) and target.strip():
                return target.strip()
    return SEGMENT_PREFIX_FALLBACK


def _queue_next_run(info: dict) -> bool:
    """Queue this film's next segment right behind the running prompt.

    The copy is the prompt that is running now, not the browser's current
    graph, and it goes to the front of the queue: another task queued in the
    meantime waits until this film is finished instead of running in between
    (which also mixed up the run state and, after switching workflow tabs,
    queued the other workflow).  Returns False when the running prompt cannot
    be found, so the browser falls back to queueing it.
    """
    try:
        from server import PromptServer
    except ImportError:
        return False
    server = getattr(PromptServer, "instance", None)
    queue = getattr(server, "prompt_queue", None)
    prompt_id = getattr(server, "last_prompt_id", None)
    if queue is None or not prompt_id:
        return False
    with queue.mutex:
        running = next((item for item in queue.currently_running.values() if item[1] == prompt_id), None)
        running = copy.deepcopy(running) if running is not None else None
    if running is None:
        return False
    prompt = running[2]
    # What the browser does to 分镜提示词 before queueing: 镜号 names the block to
    # run (this one while it has segments left), 从头开始 becomes 自动续接.
    if "shot_done" in info:
        shot_numbers = str(int(info["shot_next"])) if info.get("shot_next") else None
    else:
        shot_numbers = str(int(info["block"])) if info.get("block") else None
    for node in prompt.values():
        if not isinstance(node, dict) or node.get("class_type") != "FeiHouEasyH3ShotPrompts":
            continue
        inputs = node.setdefault("inputs", {})
        if start_mode(inputs.get("start")) == START_MANUAL:
            continue
        inputs["start"] = start_mode(inputs.get("start"))
        if shot_numbers is not None and not isinstance(inputs.get("shot_numbers"), list):
            inputs["shot_numbers"] = next_run_numbers(inputs.get("shot_numbers"), int(shot_numbers))
    number = -server.number
    server.number += 1
    queue.put((number, str(uuid.uuid4()), prompt, *running[3:]))
    LOGGER.info("Easy H3 motion transfer: queued the next run of this film at the front of the queue")
    return True


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


def _remember_window(key, conditioning, latent):
    _WINDOW_CACHE[key] = (_detached(conditioning), _detached(latent))
    while len(_WINDOW_CACHE) > WINDOW_CACHE_SIZE:
        _WINDOW_CACHE.popitem(last=False)


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
    if has_pending_text(conditioning):
        # The prompt is still to be encoded: _clip_conditioning caches it afterwards.
        return conditioning, latent, key
    _remember_window(key, conditioning, latent)
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
        LOGGER.info("Easy H3 motion transfer: 没有参考视频 1 的原片帧，颜色匹配跳过")
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
                    splits: list[int] | tuple[int, ...] | None = None,
                    segment_length: int | None = None, head_overlap: int | None = None) -> list[_Segment]:
    """Split one shot block into sampling segments.

    ``splits`` (frames from the block start, from @split) are where the
    framing changes.  Without them Motion Segment decides: ``segment_length``
    (帧窗口分段) samples clips of that many frames, overlap included; None
    (整段一次采完) samples the whole block at once, however long it is.
    Segment 0 starts from nothing unless the block continues the previous one,
    in which case ``overlap`` context frames come first.  Every later segment
    starts ``overlap`` frames before its own window, so the tail guide and the
    reference video show where the previous segment ended.  ``head_overlap``
    (default ``overlap``) is segment 0's overlap when the block continues.
    """
    frames = max(1, int(frames))
    head = int(overlap if head_overlap is None else head_overlap)
    points = sorted({int(point) for point in (splits or ()) if 0 < int(point) < frames})
    if points:
        bounds = [0, *points, frames]
        longest = max(right - left for left, right in zip(bounds, bounds[1:]))
        if longest > SEGMENT_MAX_FRAMES + 1:
            raise ValueError(f"动作迁移：@split 分出的块内分镜有 {longest / h3.FPS:.2f} 秒，超过 15 秒。")
    elif segment_length:
        bounds = [0]
        while bounds[-1] < frames:
            skip = min(overlap if len(bounds) > 1 else head if continues else 0, max(0, int(start) + bounds[-1]))
            bounds.append(min(frames, bounds[-1] + max(1, int(segment_length) - skip)))
    else:
        bounds = [0, frames]
    bounds[-1] = frames
    plan: list[_Segment] = []
    for index in range(len(bounds) - 1):
        keep = bounds[index + 1] - bounds[index]
        if keep < 1:
            continue
        segment_start = int(start) + bounds[index]
        skip = min(overlap if index > 0 else head if continues else 0, max(0, segment_start))
        plan.append(_Segment(segment_start - skip, _ceil_grid(keep + skip), skip, keep))
    return plan


def _cross_fade(images: list, head, frames: int | None = None) -> None:
    """Blend a segment's repeated head into the end of the frames collected so far.

    ``head`` re-renders the previous segment's last frames (anchored to them);
    mixing it in with a rising weight hides the step where the new segment
    takes over.  ``images`` holds uint8 parts; the last frames are replaced.
    ``frames``: length of the fade, centred in the overlap (None = the whole
    overlap); overlap frames after it are the new segment's own.
    """
    if int(images[-1].shape[0]) < int(head.shape[0]):
        images[:] = [torch.cat(images, dim=0)]
    count = min(int(head.shape[0]), int(images[-1].shape[0]))
    if count < 1:
        return
    previous = images[-1].clone()
    tail = previous[-count:].float()
    new = head[-count:].float()
    if head.dtype != torch.uint8:
        new = new.clamp(0, 1) * 255
    span = count if not frames else max(1, min(int(frames), count))
    lead = (count - span) // 2
    weight = torch.cat([torch.zeros(lead), torch.linspace(0, 1, span + 2, dtype=torch.float32)[1:-1],
                        torch.ones(count - lead - span)]).view(-1, 1, 1, 1)
    mixed = (tail * (1 - weight) + new[..., :tail.shape[-1]] * weight).round().clamp(0, 255).to(torch.uint8)
    previous[-count:] = mixed
    images[-1] = previous


def _apply_tail_guide(conditioning, video_vae, tail, frames: int, width: int, height: int, length: int):
    """Anchor the start of this segment to the previous segment's last frames."""
    if tail is None or not hasattr(tail, "shape") or int(tail.shape[0]) == 0:
        return conditioning
    lead = tail[-min(int(frames), int(tail.shape[0])):]
    guide = {"resolved_frame_index": 0, "latent": video_vae.encode(h3._resize(lead, width, height, "center"))}
    return node_helpers.conditioning_set_values(
        conditioning, {"minimax_keyframes": [guide], "minimax_frame_count": int(length)})


def _remake_options(remake: bool, face_blur=None, detector: str = remake_mode.FACE_DETECTOR, people=None) -> dict:
    """builder.build options of a Remake 替换 run (原片脸部模糊 when face_blur is a level or "auto";
    ``people`` = the key of the recognised @blur_people, None = the largest face)."""
    if not remake:
        return {}
    if not face_blur:
        return {"remake": True}
    options = {"remake": True, "face_blur": face_blur, "face_detector": str(detector)}
    if people:
        options["face_people"] = people
    return options


def _block_people(builder, shot, detector: str, recognizer: str = remake_mode.RECOGNIZER_DEFAULT):
    """Key of this block's @blur_people, recognised from their moments (None = not written).

    Only the moments inside the block's @range can be read (the reference video is
    loaded for this block only).
    """
    anchors = (shot or {}).get("blur_people") if isinstance(shot, dict) else None
    if not anchors:
        return None
    window = getattr(builder, "source_window", None)
    if window is None:
        raise ValueError("@blur_people 需要参考视频 1。")
    start = int(shot.get("start_frame") or 0)
    end = start + int(shot.get("frames") or 0)
    inside = [(int(frame), float(x), float(y)) for frame, x, y in anchors if start <= int(frame) < end]
    if not inside:
        raise ValueError(f"分镜 {shot.get('block')}：@blur_people 写的时间都不在本块 @range 里"
                         f"（{start / h3.FPS:.2f}–{end / h3.FPS:.2f} 秒），节点只读得到本块的画面。"
                         "请在这一块写一行 @blur_people，时间取本块里这个人正脸清楚的一刻。")
    if len(inside) < len(anchors):
        LOGGER.info("Easy H3 动作迁移预处理：分镜 %s 的 @blur_people 有 %d 个时间不在本块，只用本块里的 %d 个",
                    shot.get("block"), len(anchors) - len(inside), len(inside))
    key = "people:" + hashlib.sha1(json.dumps([str(getattr(builder, "signature", "")), detector, str(recognizer),
                                                inside]).encode()).hexdigest()
    if remake_mode.people(key) is None:
        vectors = remake_mode.resolve_people(lambda frame: window(frame, 1), inside, detector, recognizer)
        remake_mode.register_people(key, vectors, recognizer)
        LOGGER.info("Easy H3 动作迁移预处理：分镜 %s 只模糊 %d 个指定的人（%s）", shot.get("block"), len(inside),
                    "，".join(f"{frame / h3.FPS:.2f} 秒 ({x:.2f}, {y:.2f})" for frame, x, y in inside))
    return key


# H3 动作迁移预处理 modes (the node's 模式 widget); 自动 follows the md's @mode.
MODE_OFF, MODE_AUTO, MODE_SWAP = "关闭", "自动", "人物替换"
PREPROCESS_MODES = [MODE_OFF, MODE_AUTO, MODE_SWAP]
_MD_MODES = {"off": MODE_OFF, "swap": MODE_SWAP}


def _block_mode(preprocess, shot) -> str:
    """This run's mode: 关闭 or 人物替换.

    The node's 模式 decides; 自动 takes the md block's @mode (not written = 关闭).
    A preprocess dict from before the 模式 widget only had ``remake``.
    """
    if not isinstance(preprocess, dict):
        return MODE_OFF
    mode = preprocess.get("mode")
    if mode is None:
        mode = MODE_SWAP if preprocess.get("remake") else MODE_OFF
    if mode == MODE_AUTO:
        written = str(shot.get("mode") or "") if isinstance(shot, dict) else ""
        mode = _MD_MODES.get(written, MODE_OFF)
        if isinstance(shot, dict):
            LOGGER.info("Easy H3 动作迁移预处理：自动，分镜 %s 按 md 的 @mode：%s", shot.get("block"),
                        mode if written else "没写，关闭")
    elif mode not in (MODE_OFF, MODE_SWAP):
        LOGGER.warning("Easy H3 动作迁移预处理：模式「%s」已移除，按关闭", mode)
        mode = MODE_OFF
    return mode


def _preprocess_recognizer(preprocess) -> str:
    """The preprocess node's 人脸识别模型 (@blur_people), buffalo_l when not set."""
    value = preprocess.get("face_recognizer") if isinstance(preprocess, dict) else None
    return str(value or remake_mode.RECOGNIZER_DEFAULT)


def _block_face_blur(preprocess, shot) -> tuple:
    """(face_blur, detector) of this run: the md block's @face_blur over the preprocess node's setting.

    Without the preprocess node, or with it set to 关, nothing is blurred.
    """
    if not isinstance(preprocess, dict) or preprocess.get("face_blur") is None:
        if isinstance(shot, dict) and shot.get("face_blur") not in (None, "", "off"):
            LOGGER.info("Easy H3 动作迁移：md 写了 @face_blur %s，但没接「H3 动作迁移预处理」或它的脸部模糊是 关，不模糊",
                        shot.get("face_blur"))
        return None, remake_mode.FACE_DETECTOR
    detector = str(preprocess.get("face_detector") or remake_mode.FACE_DETECTOR)
    written = str(shot.get("face_blur") or "") if isinstance(shot, dict) else ""
    setting = remake_mode.parse_face_blur(written) if written else preprocess["face_blur"]
    return setting, detector


def _clip_conditioning(h3_context, builder, start: int, length: int, width: int, height: int,
                       windows=None, tail=None, skip: int = 0, first=None, **options):
    """Conditioning + empty latent of one sampled clip.

    Without ``windows`` (or with one) this is the clip's own reference window,
    anchored to ``tail`` when ``skip`` > 0, otherwise to the @first picture
    ``first`` (one frame at frame 0, kept in the output).  With several context
    windows every window gets its own reference window (the anchor goes to the
    first), and the result carries the whole window set for the windowed guider.
    """
    remake = bool(options.get("remake"))
    prefix = None
    if skip and remake and remake_mode.prefix_frames(skip) and tail is not None:
        # Remake: the previous segment's frames open this one held clean (noise_mask 0),
        # whole 17-frame groups of the overlap; the rest of the overlap is generated and dropped.
        lead = tail[-min(int(skip), int(tail.shape[0])):]
        count = remake_mode.prefix_frames(int(lead.shape[0]))
        if count:
            prefix = h3._resize(lead[:count], width, height, "center")
    if prefix is not None:
        anchor, anchor_frames = None, 0
    elif skip:
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
        if prefix is not None:
            latent = remake_mode.apply_prefix(h3_context.video_vae, latent, prefix)
        return conditioning, latent
    built = []
    # Reference media of every window first (VAE loaded once), then every
    # prompt (text encoder loaded once).
    with deferred_text_encoding() as batch:
        for window in windows:
            result = _build_window(h3_context, builder, start + window.frame_start, window.frames,
                                   width, height, **options)
            built.append(result)
    pending = [result[0] for result in built]
    if anchor_frames:
        # Still on the VAE: the anchor goes on before the prompts are encoded.
        pending.append(_apply_tail_guide(pending[0], h3_context.video_vae, anchor, anchor_frames,
                                         width, height, windows[0].frames))
    resolved = resolve_text_batch(batch, pending)
    conditionings = resolved[:len(built)]
    for result, conditioning in zip(built, conditionings):
        if len(result) > 2:
            _remember_window(result[2], conditioning, result[1])
    if anchor_frames:
        conditionings[0] = resolved[-1]
    latent, _frames = h3._empty_av_latent(width, height, length)
    if remake:
        # The whole segment's soundtrack, held clean; each window takes its part.
        audio = _window_audio(builder.source_audio, start, length) if options.get("soundtrack", True) else None
        latent = remake_mode.held_audio_latent(h3_context.audio_vae, audio, latent, int(length))
        if prefix is not None:
            latent = remake_mode.apply_prefix(h3_context.video_vae, latent, prefix)
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


def _frames_audio(audio, start: int, count: int, fps: float):
    return _audio_slice(audio, start, count, fps) if isinstance(audio, dict) else audio


def _mix_audio_head(audio, held, frames: int, fps: float):
    """Fade ``held`` (film audio of ``frames`` frames) out over the first ``frames`` of ``audio``."""
    if not isinstance(audio, dict) or not isinstance(held, dict):
        return audio
    audio = _film_audio(audio)
    wave = audio["waveform"].clone()
    old = _film_audio(held)["waveform"].to(wave)
    samples = min(int(round(frames * FILM_AUDIO_RATE / fps)), int(wave.shape[-1]), int(old.shape[-1]))
    if samples < 1:
        return audio
    ramp = torch.linspace(0, 1, samples + 2, dtype=wave.dtype)[1:-1]
    wave[..., :samples] = old[..., -samples:] * (1 - ramp) + wave[..., :samples] * ramp
    return {"waveform": wave, "sample_rate": FILM_AUDIO_RATE}


def _transition_frames(frames, audio, generated, motion_state, fps: float):
    """@transition crossfade at both ends of a finished block.

    Opening (``crossfade_lead``): the block was rendered that many frames early; those frames
    are blended with the frames the previous block held back (picture and sound).  Without
    them (e.g. a block run on its own) the early frames are dropped: a hard cut.
    Closing (``crossfade_next``): the next block cross-fades in, so this block keeps its last
    frames back for it and saves that much shorter; the joined film keeps its length.
    """
    block = int(motion_state.get("block") or 1)
    series = str(motion_state.get("series") or "")
    lead = int(motion_state.get("crossfade_lead") or 0)
    if lead:
        held = load_held(series, block - 1) if block > 1 else None
        held_frames = None if held is None else held[0]
        count = 0 if held_frames is None else min(lead, int(held_frames.shape[0]))
        if count < lead:
            if count == 0:
                LOGGER.warning("Easy H3 motion transfer: 分镜 %d 要叠化进入，但找不到上一块留下的尾帧"
                               "（单独重跑这一块时会这样），按硬切处理", block)
            drop = lead - count
            total = int(frames.shape[0])
            frames = frames[drop:]
            audio = _frames_audio(audio, drop, total - drop, fps)
            generated = _frames_audio(generated, drop, total - drop, fps)
        if count:
            old = held_frames[-count:].to(frames)
            weight = torch.linspace(0, 1, count + 2, dtype=frames.dtype)[1:-1].view(-1, 1, 1, 1)
            frames = frames.clone()
            frames[:count] = old * (1 - weight) + frames[:count] * weight
            audio = _mix_audio_head(audio, held[1], count, fps)
            LOGGER.info("Easy H3 motion transfer: 分镜 %d 开头 %d 帧和上一块叠化（%.2f 秒）", block, count, count / fps)
    hold = int(motion_state.get("crossfade_next") or 0)
    last_block = bool(motion_state.get("is_last", block >= int(motion_state.get("blocks") or 1)))
    if hold and not last_block:
        total = int(frames.shape[0])
        hold = min(hold, total - 1)
        if hold > 0:
            save_held(series, block, frames[-hold:], _film_audio(_frames_audio(audio, total - hold, hold, fps)))
            frames = frames[:total - hold]
            audio = _frames_audio(audio, 0, total - hold, fps)
            generated = _frames_audio(generated, 0, total - hold, fps)
            LOGGER.info("Easy H3 motion transfer: 分镜 %d 最后 %d 帧留给下一块叠化，本块存 %d 帧", block, hold, total - hold)
    return frames, audio, generated


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
                            start: str, width: int, height: int, fps: float, soundtrack: bool = True,
                            remake: bool = False, face_blur=None, face_detector: str = remake_mode.FACE_DETECTOR):
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
    options = _remake_options(remake, face_blur, face_detector)
    conditioning, latent = builder.build(frame_start, length, width, height, soundtrack=soundtrack, **options)
    if index > 0:
        previous_video, _audio = _split_av(_RUN["previous"]["samples"])
        new_video, _audio = _split_av(latent["samples"])
        tail = _RUN.get("tail")
        held = remake_mode.prefix_frames(overlap) if remake and tail is not None else 0
        if held and int(tail.shape[0]) >= held:
            # Remake: keep the held soundtrack; the previous frames open the segment held clean.
            prefix = h3._resize(tail[-overlap:][:held], width, height, "center")
            latent = remake_mode.apply_prefix(h3_context.video_vae, latent, prefix)
        elif remake and tail is not None and int(tail.shape[0]):
            conditioning = _apply_tail_guide(conditioning, h3_context.video_vae, tail, overlap, width, height, length)
        elif previous_video.shape[-2:] != new_video.shape[-2:] and tail is not None and int(tail.shape[0]):
            # Two-pass: the collected latent is the 2nd-pass canvas, this one the
            # 1st pass; continue from the decoded tail frames like shot blocks do.
            conditioning = _apply_tail_guide(conditioning, h3_context.video_vae, tail, overlap, width, height, length)
        else:
            latent = _masked_continuation(latent, _RUN["previous"], overlap)
    state = {"run": _RUN, "mode": "legacy", "index": index, "count": count, "total": total, "overlap": overlap,
             "source_frames": builder.source_frames, "source_audio": builder.source_audio,
             "source_window": getattr(builder, "source_window", None),
             "video_vae": h3_context.video_vae, "audio_vae": h3_context.audio_vae, "fps": fps,
             "window_start": frame_start, "window_length": length, "remake": remake, "face_blur": face_blur,
             "face_detector": face_detector,
             "skip": 0 if index == 0 else overlap, "keep": length - (0 if index == 0 else overlap)}
    return conditioning, latent, state, index, count, total


def _prepare_shot_block(h3_context, builder, shot: dict, overlap: int, width: int, height: int, fps: float,
                        soundtrack: bool = True, context: tuple[int, int] | None = None,
                        segment_length: int | None = None, context_segment: int | None = None,
                        remake: bool = False, face_blur=None, face_detector: str = remake_mode.FACE_DETECTOR,
                        face_people=None):
    """One sampling segment of the current markdown shot block.

    ``context`` = (window frames, overlap frames): the whole block is one
    segment sampled with joint context windows.  A talk block without
    reference video needs its dialogue lines time-tagged (``shot["speech"]``):
    each window then speaks only its own lines and the windows are placed so
    their overlaps fall in the pauses; without tags it keeps the sequential
    segments.
    ``context_segment`` (frames): a longer block is cut into segments of at
    most that many frames, chained on the window overlap; each one is still
    sampled with context windows.
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
    # @transition crossfade: render that many frames before the block too; Collect blends
    # them with the frames the previous block held back.
    lead = min(int(shot.get("crossfade") or 0), block_start) if not continues else 0
    splits = shot.get("splits")
    if lead:
        block_start -= lead
        block_frames += lead
        splits = [int(point) + lead for point in (splits or ())]
        LOGGER.info("Easy H3 motion transfer: 分镜 %d 叠化进入（@transition crossfade）：多采本块前面 %d 帧，"
                    "和上一块留下的最后 %d 帧叠化", block, lead, lead)
    speech = [(int(start), int(end)) for start, end in (shot.get("speech") or ())]
    if context is not None and not shot.get("video", True) and not speech:
        LOGGER.info("Easy H3 motion transfer: 分镜 %d 没有参考视频（口播块），台词没标时间，上下文窗口不适用，按帧窗口分段"
                    "（台词行写成 [00:02.0-00:05.5] …<d>…</d> 才能用上下文窗口）", block)
        context = None
    blend = False
    if context is not None and context_segment and block_frames > int(context_segment):
        chain = max(int(overlap), int(context[1]))
        plan = _block_segments(block_frames, block_start, chain, continues, None, int(context_segment),
                               head_overlap=overlap)
        overlap, blend = chain, True
        LOGGER.info("Easy H3 motion transfer: 分镜 %d 有 %.1f 秒，上下文窗口按每段最多 %.0f 秒分成 %d 段（段间重叠 %d 帧），每次运行采一段",
                    block, block_frames / h3.FPS, int(context_segment) / h3.FPS, len(plan), chain)
    elif context is not None:
        skip = min(overlap if continues else 0, block_start)
        plan = [_Segment(block_start - skip, _ceil_grid(block_frames + skip), skip, block_frames)]
    else:
        plan = _block_segments(block_frames, block_start, overlap, continues, splits, segment_length)
    same_run = (_RUN.get("mode") == "block" and _RUN.get("fingerprint") == fingerprint
                and _RUN.get("series") == series)
    if not same_run or int(_RUN.get("block") or 0) != block or shot.get("restart"):
        # Keep the finished block's tail: the next block may continue it.
        carried = _RUN.get("tail") if same_run and int(_RUN.get("block") or 0) == block - 1 else None
        _RUN.clear()
        _RUN.update(mode="block", fingerprint=fingerprint, block=block, series=series,
                    segment=0, tail=carried, images=[], audio=[])
    index = int(_RUN.get("segment") or 0)
    if index >= len(plan):
        raise ValueError("动作迁移：分镜块的段号超出范围，请把「分镜提示词」的 start 设为从头开始后重新运行。")
    segment = plan[index]
    # Later segments of a split context block are rendered on their own: only the source
    # and the pictures, no anchor to the previous segment's output (anchoring carried its
    # sharpening, saturation and grain into the next segment, worse with every segment).
    # Their overlap is rendered too and cross-faded into the previous segment by Collect.
    independent = blend and index > 0
    tail = _RUN.get("tail")
    if segment.skip and not independent and (tail is None or int(getattr(tail, "shape", (0,))[0]) == 0):
        if index == 0 and continues:
            tail = load_tail(series, block - 1)
            _RUN["tail"] = tail
    first_frame = getattr(builder, "first_frame", None)
    if segment.skip and not independent and (tail is None or int(getattr(tail, "shape", (0,))[0]) == 0):
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
        if speech:
            local = [(start - segment.start, end - segment.start) for start, end in speech
                     if end > segment.start and start < segment.start + segment.length]
            windows, crossed = context_windows.plan_speech_windows(segment.length, *context, local)
            if len(windows) > 1:
                LOGGER.info("Easy H3 motion transfer: 分镜 %d 上下文窗口按台词停顿排位：%s", block,
                            "每句台词都完整落在一个窗口里" if not crossed else
                            f"有 {crossed} 帧台词落在窗口重叠处（句间停顿不够长，接缝处可能吞字或重音；"
                            "把那里的停顿拉长到 1.5 秒以上，或调整台词时间）")
                if crossed:
                    LOGGER.warning("Easy H3 motion transfer: 分镜 %d 有 %d 帧台词落在上下文窗口的重叠处", block, crossed)
        else:
            windows = context_windows.plan_windows(segment.length, *context)
        if len(windows) < 2:
            windows = None
        else:
            sizes = sorted({window.frames for window in windows})
            LOGGER.info("Easy H3 motion transfer: 分镜 %d 上下文窗口 %d 个（%s，起点 %s），整块一次采样",
                        block, len(windows),
                        f"每个 {sizes[0]} 帧" if len(sizes) == 1 else
                        "长度 " + " / ".join(str(window.frames) for window in windows) + " 帧",
                        ", ".join(str(segment.start + window.frame_start) for window in windows))
    if blend:
        LOGGER.info("Easy H3 motion transfer: 分镜 %d 第 %d/%d 段，输出帧 %d-%d（%s）",
                    block, index + 1, len(plan), segment.start + segment.skip,
                    segment.start + segment.skip + segment.keep - 1,
                    f"开头 {segment.skip} 帧和上一段重叠：本段独立生成，拼接时渐变过渡" if independent
                    else f"开头 {segment.skip} 帧接上一段" if segment.skip else "第一段")
    # Reference videos keep the size set where they were loaded (VHS, or the Media card's 媒体设置).
    # ``end`` stops the window at the block's last frame instead of showing the next shot.
    conditioning, latent = _clip_conditioning(h3_context, builder, segment.start, segment.length, width, height,
                                              windows, None if independent else _RUN.get("tail"),
                                              0 if independent else segment.skip, first,
                                              end=block_start + block_frames, soundtrack=soundtrack,
                                              **_remake_options(remake, face_blur, face_detector, face_people))
    state = {"run": _RUN, "mode": "block", "index": index, "count": len(plan), "skip": segment.skip,
             "windows": windows, "context_windows": len(windows) if windows else 0, "first": first,
             "blend": blend, "independent": independent,
             "sound": str(shot.get("sound") or ""),
             "crossfade_lead": lead, "crossfade_next": int(shot.get("crossfade_next") or 0),
             "keep": segment.keep, "overlap": overlap, "block": block, "blocks": blocks, "series": series,
             "is_last": (bool(shot["is_last"]) if "is_last" in shot else block >= blocks),
             "fingerprint": fingerprint, "block_start": block_start, "block_frames": block_frames,
             "source_frames": builder.source_frames, "source_audio": builder.source_audio,
             "source_window": getattr(builder, "source_window", None),
             "video_vae": h3_context.video_vae, "audio_vae": h3_context.audio_vae, "fps": fps,
             "window_start": segment.start, "window_length": segment.length, "remake": remake,
             "face_blur": face_blur, "face_detector": face_detector, "face_people": face_people}
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
    # An independent segment (later part of a split context block) has no anchor in either pass.
    tail = state["run"].get("tail") if skip and not state.get("independent") else None
    windows = state.get("windows")
    first = state.get("first")

    def build(width, height, trigger_prefix=""):
        options = {"soundtrack": soundtrack, "trigger_prefix": trigger_prefix}
        options.update(_remake_options(bool(state.get("remake")), state.get("face_blur"),
                                       state.get("face_detector") or remake_mode.FACE_DETECTOR,
                                       state.get("face_people")))
        if end is not None:
            options["end"] = end
        return _clip_conditioning(h3_context, builder, start, length, width, height, windows, tail,
                                  skip if tail is not None else 0, first, **options)

    return replace(h3_context, conditioning=conditioning, latent=latent, dual_plan=None, motion_placeholder=False,
                   highres_rebuilder=H3HighresRebuilder(build=build, canvas_dependent=True))


def _first_pass(model, context, sampler_name, scheduler, steps, first_steps, cfg):
    """The 1st Sample Enhancer on this segment's window: guider, sampler, sigmas, latent, context with the 2nd-pass plan.

    Same code path as the 1st Sample Enhancer node, without semantic bridge and
    default trigger words.
    """
    return FeiHouEasyH3DualSample1st.prepare(model, context, int(steps), int(first_steps), sampler_name,
                                             False, NONE_OPTION, 0.0, float(cfg), "", scheduler, False)


def _remake_first_pass(result, first_steps):
    """Remake two-pass: the 1st pass runs the swap schedule's first steps, the 2nd pass the rest."""
    guider, sampler, _sigmas, latent, context = result
    full = remake_mode.swap_sigmas()
    split = max(1, min(int(first_steps), full.numel() - 2))
    plan = dict(context.dual_plan or {})
    plan.update(start_sigma=float(full[split]), tail=full[split:].clone(), remake=True)
    LOGGER.info("Easy H3 Remake 替换：二段式，一采 swap 曲线前 %d 步 %s，二采跑剩下的 %s",
                split, full[:split + 1].tolist(), full[split:].tolist())
    return guider, sampler, full[:split + 1].clone(), latent, replace(context, dual_plan=plan)


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
                                 "tooltip": "采样器，单次采样和二段式都用 euler（二采沿用这里的选择）。"
                                            "旧的 dual_clock_euler 会让口播音频多出一层像背景音乐的持续音，已隐藏，旧工作流里存的会按 euler 运行。"}),
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
                    "写了 @split 的块按切点分段；没写的按本节点的 segmentation：帧窗口按 segment_frames 分段，不分段则整块一次采样（不限时长），上下文窗口整块按窗口一次采样。"
                    "块内和 @continue 的块用尾部帧接续；不接则按每段帧数固定分段。"}),
                "reference_soundtrack": ([SOUNDTRACK_TO_MODEL, SOUNDTRACK_COLLECT_ONLY], {"default": SOUNDTRACK_TO_MODEL,
                    "tooltip": "参考视频 1 的原声要不要送进模型。送入模型：原声作为 <Audio 1> 参与生成（人物替换跟唱、对口型用），音色参考等音频卡顺延成 <Audio 2> 起。"
                               "不送入模型：视频只做动作参考，原声不编码（参考 token 更少），音频卡的第一条就是 <Audio 1>；原声照样交给「动作迁移收束」按 @sound / audio_output 输出。"}),
                # Saved workflows store widgets by position: the frontend
                # (feihou_segment_layout) maps older saves onto this order by name.
                "segmentation": (SEGMENTATION_CHOICES, {"default": SEGMENTATION_AUTO,
                    "tooltip": "分段方式，下面的 segment_frames / overlap_frames 跟着它换选项（每种方式各记各的值）。"
                               "自动：完全按分镜 md——每块的 @segment（或 md 开头写的 @segment，对所有块生效）决定，"
                               "例如 @segment context,window=192,overlap=39,chunk=30、@segment chain,length=124,overlap=22、@segment whole（没写的参数用推荐值；旧的中文写法 @分段 上下文窗口 窗口=192 也能读）；md 没写就按不分段。自动和不分段时下面三项变灰、不起作用。"
                               "不分段：没写 @split 的块整块一次采样（不限时长，视频长时很吃显存），写了 @split 的按切点分段；没接分镜提示词时参考视频 1 整段一次采。"
                               "帧窗口：块内按 @split 或 segment_frames 分段逐段采样，后一段锁定上一段解码出的尾部 overlap_frames 帧接上，段数越多细节越掉、噪点越多。"
                               "上下文窗口：整块一次采样，每一步把整块 latent 切成 segment_frames 长、至少重叠 overlap_frames 帧的窗口分别预测、在重叠处线性混合，"
                               "每个窗口拿自己时间段的参考视频（参考图和提示词每个窗口都带），窗口之间不传解码画面，画质不随长度累积变差；"
                               "单块一次跑完，@split 不再分段，@continue 的块按 22 帧尾帧接续。二段式的一采和 2nd Sample Enhancer 都按窗口采样。"
                               "没有参考视频的口播块自动按帧窗口处理。"}),
                "segment_frames": (SEGMENT_FRAME_CHOICES, {"default": SEGMENT_FRAME_DEFAULT,
                    "tooltip": "分段帧数（24fps，H3 只接受 5+17N 帧）。帧窗口：每段帧数，默认 124 ≈ 5.17 秒，可选 39～497；"
                               "上下文窗口：每个窗口帧数，默认 345 ≈ 14.4 秒，可选 124～362，越短窗口越多、越慢；不分段：不用。"
                               "接「分镜提示词」时只作用于没写 @split 的块。"}),
                "overlap_frames": (SEGMENT_OVERLAP_CHOICES, {"default": "22",
                    "tooltip": "重叠帧数。帧窗口 / 不分段：上一段结尾这么多帧的 latent 被锁定作为下一段开头（@split 段和 @continue 块），可选 22 / 39 / 5，22 帧 = 7 个视频 token；"
                               "上下文窗口：相邻窗口重叠的帧数，可选 22 / 39 / 56 / 73（默认）/ 90 / 107，越大接缝越稳、越慢。窗口从开头按这个重叠依次往后排，"
                               "最后一个窗口只跑到块尾（至少约 2 秒，不够时往前多重叠一些）。"}),
                "context_segment": (CONTEXT_SEGMENT_CHOICES, {"default": CONTEXT_SEGMENT_DEFAULT,
                    "tooltip": "上下文窗口每段上限（只在上下文窗口时用）。比它长的块按这个长度分段，每次运行采一段、存一段，跑完一段就能看效果，"
                               "中途停了前面的不白跑；每段只按原片和参考图独立生成，不锁定上一段的画面（不会一段比一段锐、艳、噪），后一段多生成开头和上一段重叠的 overlap_frames 帧，拼接时在重叠处从上一段渐变到这一段。"
                               "整块一次：整块一次采完（最平滑，长片要等很久）。总计算量基本不变，分段多出段间重叠的那几帧。"}),
                "crossfade_frames": (CROSSFADE_CHOICES, {"default": CROSSFADE_DEFAULT,
                    "tooltip": "叠化帧数（只在上下文窗口按每段上限分段时用）：段与段重叠处，取中间这么多帧从上一段渐变到下一段，"
                               "之前是上一段的画面，之后直接是下一段的画面。默认 12 帧 = 0.5 秒（24fps）；"
                               "6 帧更干脆，24 帧更柔和；全部重叠 = 整个重叠段都在渐变（较长，重影更明显）。"
                               "分段方式为自动时按 md 的 @segment context,...,fade=12。"}),
            },
        })

    @classmethod
    def _input_types_hidden(cls, types):
        types["hidden"] = {"prompt": "PROMPT", "unique_id": "UNIQUE_ID"}
        return types

    @classmethod
    def VALIDATE_INPUTS(cls, segmentation=SEGMENTATION_CHAIN, segment_frames=SEGMENT_FRAME_DEFAULT,
                        overlap_frames="22", context_segment=CONTEXT_SEGMENT_DEFAULT,
                        crossfade_frames=CROSSFADE_DEFAULT):
        if str(context_segment) not in CONTEXT_SEGMENT_CHOICES:
            return f"context_segment 必须是{'、'.join(CONTEXT_SEGMENT_CHOICES)}之一：{context_segment!r}"
        if str(crossfade_frames) not in CROSSFADE_CHOICES:
            return f"crossfade_frames 必须是{'、'.join(CROSSFADE_CHOICES)}之一：{crossfade_frames!r}"
        # Workflows saved with 接续 still run.
        mode = _segmentation(segmentation)
        if mode not in SEGMENTATION_CHOICES:
            return f"segmentation 必须是{'、'.join(f'「{choice}」' for choice in SEGMENTATION_CHOICES)}之一：{segmentation!r}"
        if mode == SEGMENTATION_AUTO:
            return True
        # Saved before the option list: a bare frame count (aligned down to 5+17N when used).
        try:
            frames = _window_frames(segment_frames)
        except (TypeError, ValueError):
            return f"segment_frames 无效：{segment_frames!r}"
        if mode == SEGMENTATION_CONTEXT:
            if _window_label(frames) not in CONTEXT_WINDOW_CHOICES:
                return f"上下文窗口的 segment_frames 必须是 5+17N 且在 124～362 帧之间：{segment_frames!r}"
            if context_windows.LEGACY_OVERLAPS.get(str(overlap_frames), str(overlap_frames)) not in CONTEXT_OVERLAP_CHOICES:
                return f"上下文窗口的 overlap_frames 必须是 {' / '.join(CONTEXT_OVERLAP_CHOICES)}：{overlap_frames!r}"
            return True
        if not SEGMENT_FRAME_RANGE[0] <= frames <= SEGMENT_FRAME_RANGE[1]:
            return f"segment_frames 必须在 {SEGMENT_FRAME_RANGE[0]}～{SEGMENT_FRAME_RANGE[1]} 帧之间：{segment_frames!r}"
        if str(overlap_frames) not in OVERLAP_CHOICES:
            return f"{mode}的 overlap_frames 必须是 {' / '.join(OVERLAP_CHOICES)}：{overlap_frames!r}"
        return True

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
            guider, sampler, _sigmas, _latent, context = first_pass(replace(h3_context, motion_placeholder=False))
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
                segmentation=SEGMENTATION_CHAIN, context_segment=CONTEXT_SEGMENT_DEFAULT,
                crossfade_frames=CROSSFADE_DEFAULT, prompt=None, unique_id=None, **_removed):
        if not isinstance(h3_context, MiniMaxH3Context):
            raise ValueError("h3_context 必须接 FeiHou Easy H3 Setup 的 h3_context。")
        builder = getattr(h3_context, "segment_builder", None)
        if builder is None:
            raise ValueError("动作迁移需要 Setup 选择「参考生视频」模式（并重新运行 Setup）。")
        shot = shot_prompts.get("motion") if isinstance(shot_prompts, dict) else None
        # A shot md block written @video none (reference generation, e.g. a talk
        # block) takes its length from @range, so the md needs no reference video.
        if builder.source_frames < 5 and not (shot and shot.get("video") is False):
            raise ValueError("动作迁移需要至少一个参考视频：参考视频 1 决定总长度。"
                             "只做参考生视频（不跟随视频）时，分镜 md 的每块写 @video none。")

        mode = _segmentation(segmentation)
        if mode == SEGMENTATION_AUTO:
            written = shot.get("segmentation") if shot else None
            if written:
                mode = written["mode"]
                segment_frames = written.get("segment_frames", segment_frames)
                overlap_frames = written.get("overlap_frames", overlap_frames)
                context_segment = written.get("context_segment", context_segment)
                crossfade_frames = written.get("crossfade_frames", crossfade_frames)
                LOGGER.info("Easy H3 motion transfer: 分段方式 自动，按 md 的 @segment：%s%s", mode,
                            "" if mode == SEGMENTATION_WHOLE else f" {segment_frames} 帧 / 重叠 {overlap_frames}"
                            + (f" / 每段 {context_segment}" if mode == SEGMENTATION_CONTEXT else ""))
            else:
                mode = SEGMENTATION_WHOLE
                LOGGER.info("Easy H3 motion transfer: 分段方式 自动，%s，按不分段（整块一次采样）",
                            "md 没写 @segment" if shot else "没接分镜提示词")
        if mode == SEGMENTATION_WHOLE:
            overlap_frames = WHOLE_OVERLAP
        # Calls made before the shared fields: frame_window off = 不分段,
        # context_window_frames / context_overlap_frames = the window fields.
        if mode == SEGMENTATION_CHAIN and _removed.get("frame_window") is False:
            mode = SEGMENTATION_WHOLE
        if mode == SEGMENTATION_CONTEXT and "context_window_frames" in _removed:
            segment_frames = _removed["context_window_frames"]
            overlap_frames = _removed.get("context_overlap_frames", CONTEXT_OVERLAP_DEFAULT)
        if mode == SEGMENTATION_CONTEXT and not CONTEXT_WINDOWS_ENABLED:
            LOGGER.info("Easy H3 motion transfer: 上下文窗口暂时关闭，按帧窗口分段")
            mode = SEGMENTATION_CHAIN
            segment_frames, overlap_frames = SEGMENT_FRAME_DEFAULT, "22"
        frame_window = mode != SEGMENTATION_WHOLE
        length = _grid_frames(_window_frames(segment_frames))
        context = None
        if mode == SEGMENTATION_CONTEXT:
            overlap_frames = context_windows.LEGACY_OVERLAPS.get(str(overlap_frames), str(overlap_frames))
            context = (length, int(overlap_frames))
            # The window overlap is not a tail overlap: @continue blocks and
            # the fixed-length fallback anchor on CONTEXT_CHAIN_OVERLAP frames.
            overlap_frames = CONTEXT_CHAIN_OVERLAP
        whole = not frame_window and not isinstance(shot_prompts, dict)
        if whole:
            # 不分段: one segment as long as reference video 1.
            length = _ceil_grid(builder.source_frames)
            LOGGER.info("Easy H3 motion transfer: 不分段，参考视频 1 整段 %d 帧一次采样", length)
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
        sampler_name = dual_clock.resolve_sampler_name(sampler_name)
        if not two_pass and sampler_name not in comfy.samplers.SAMPLER_NAMES:
            raise ValueError(f"动作迁移分段：{sampler_name} 只用于二段式。二段式要把本节点的 h3_context 输出接到 2nd Sample Enhancer（不能接 Setup 的 h3_context）；单次采样请选 euler 等 ComfyUI 原生采样器。")
        if two_pass and not 1 <= int(first_steps) < int(steps):
            raise ValueError("动作迁移分段：二段式的 first_steps 必须小于 steps（总步数）。")

        preprocess = getattr(h3_context, "motion_preprocess", None)
        # Remake 替换 is switched on only by the H3 动作迁移预处理 node.
        remake = _block_mode(preprocess, shot) == MODE_SWAP
        face_blur, face_detector = _block_face_blur(preprocess, shot)
        if shot and shot.get("video") is False and (remake or face_blur):
            # A talk block (@video none) has no source video to edit or blur: plain reference generation.
            LOGGER.info("Easy H3 动作迁移预处理：分镜 %s 写了 @video none（口播块），不送参考视频，本块不做人物替换和脸部模糊",
                        shot.get("block"))
            remake, face_blur = False, None
        if face_blur and not remake:
            LOGGER.warning("Easy H3 动作迁移预处理：脸部模糊只在 Remake 替换时生效，本次没开 Remake，不模糊")
            face_blur = None
        face_people = None
        if face_blur:
            LOGGER.info("Easy H3 动作迁移预处理：本段原片脸部模糊 %s（%s）",
                        "自动" if face_blur == "auto" else f"{face_blur} 档", face_detector)
            face_people = _block_people(builder, shot, face_detector, _preprocess_recognizer(preprocess))
        if remake:
            # The Remake LoRA comes from the workflow's LoRA list (bypass, strength 1).
            model = remake_mode.keyframe_until(model)
            if float(cfg) > 1.0:
                LOGGER.warning("Easy H3 Remake 替换：Remake 按 cfg 1 训练，cfg %.1f 可能过曝", float(cfg))

        def first_pass(context):
            result = _first_pass(model, context, sampler_name, scheduler, steps, first_steps, cfg)
            if remake:
                result = _remake_first_pass(result, first_steps)
            return result

        if shot and shot.get("passthrough"):
            return FeiHouEasyH3MotionSegment._prepare_passthrough(model, h3_context, builder, shot, sampler_name,
                                                                  width, height, fps,
                                                                  first_pass if two_pass else None)
        if context is not None and not shot:
            LOGGER.warning("Easy H3 motion transfer: 上下文窗口需要接「分镜提示词」，固定分段仍按帧窗口采样")
        if shot:
            conditioning, latent, state, index, count, total = _prepare_shot_block(
                h3_context, builder, shot, overlap, width, height, fps, soundtrack, context,
                length if frame_window else None,
                _context_segment_frames(context_segment) if context is not None else None, remake, face_blur, face_detector,
                face_people)
            state["crossfade"] = _crossfade_frames(crossfade_frames)
        else:
            conditioning, latent, state, index, count, total = _prepare_fixed_segments(
                h3_context, builder, length, overlap, 0, start, width, height, fps, soundtrack, remake, face_blur, face_detector)

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
        if remake:
            sigmas = remake_mode.swap_sigmas()
            LOGGER.info("Easy H3 Remake 替换：用 swap 曲线 %d 步 %s（steps 不起作用）", sigmas.numel() - 1, sigmas.tolist())
        else:
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
                                              "tooltip": "开启后块内每一段采样完就把这一段（已去掉开头接续用的重叠帧，配这一块选用的声音）存成 mp4，存到接在 images 输出上的合并节点（如 Video Combine V2）的 filename_prefix 目录，文件名后加 _b块号_seg段号，并在那个节点里预览；"
                                                         "方便逐段检查；整块视频照常由 Video Combine V2 保存。"}),
                "color_match_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05,
                                                   "tooltip": "调色强度：1 = 完全对齐原片，0.5 = 一半。换上去的人颜色和原片差很多（如金色紧身裤换黑裤）时可适当调低。"}),
            },
            "hidden": {"prompt": "PROMPT", "unique_id": "UNIQUE_ID"},
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
        # Preview parameters (the /view query) for the video node fed by Collect.
        return {"filename": name, "subfolder": subfolder, "type": "output", "format": "video/h264-mp4",
                "frame_rate": float(fps)}

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
        if motion_state.get("blend") and skip and run.get("images"):
            _cross_fade(run["images"], decoded[:skip].detach().cpu(), motion_state.get("crossfade"))
        if segment.dtype == torch.uint8:
            run.setdefault("images", []).append(segment)
        else:
            run.setdefault("images", []).append((segment.clamp(0, 1) * 255).round().to(torch.uint8))
        sound = _node_value(comfy_audio_nodes.VAEDecodeAudio.execute(motion_state["audio_vae"], sampled))
        run.setdefault("audio", []).append(_audio_slice(sound, skip, keep, fps))
        if save_segments and int(count) <= 1:
            # The block is this one segment: Video Combine saves the same video as the block.
            LOGGER.info("Easy H3 motion transfer: 分镜 %d 只有一段，不另存 _seg01（和整块视频相同）", block)
        if save_segments and int(count) > 1:
            source_audio = motion_state.get("source_audio")
            if audio_output == AUDIO_SOURCE and source_audio is not None:
                segment_audio = _audio_slice(source_audio, int(motion_state.get("window_start") or 0) + skip, keep, fps)
            else:
                segment_audio = run["audio"][-1]
            saved = FeiHouEasyH3MotionCollect._save_segment(_unit_frames(segment), segment_audio, fps, filename_prefix,
                                                            f"b{block:03d}_seg{index + 1:02d}")
        else:
            saved = None
        # The next segment (or a continued block) anchors to these frames.
        run["tail"] = _unit_frames(segment[-overlap:]) if overlap else None
        run["segment"] = index + 1
        del decoded, sound
        ui = {"feihou_motion": [{"next": bool(auto_queue), "done": index + 1, "count": count,
                                 "block": block, "blocks": blocks}]}
        if saved:
            ui["feihou_motion"][0]["segment_video"] = saved
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
        tail = frames[-overlap:] if overlap else None
        frames, audio, generated = _transition_frames(frames, audio, generated, motion_state, fps)
        return FeiHouEasyH3MotionCollect._finish_block(run, motion_state, frames, audio, generated, ui, tail)

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
                save_segments=False, prompt=None, unique_id=None, **_removed):
        # Segments go where the joined video goes (filename_prefix of the node fed by images).
        filename_prefix = _segment_prefix(prompt, unique_id) if save_segments else ""
        result = FeiHouEasyH3MotionCollect._collect(latent, motion_state, audio_output, auto_queue, color_match,
                                                    color_match_strength, save_segments, filename_prefix)
        info = result["ui"]["feihou_motion"][0]
        if info.get("next"):
            info["queued"] = _queue_next_run(info)
        return result

    @staticmethod
    def _collect(latent, motion_state, audio_output, auto_queue, color_match, color_match_strength,
                 save_segments, filename_prefix):
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
        # A single segment is the whole video: Video Combine saves it already.
        if save_segments and int(count) > 1:
            if audio_output == AUDIO_SOURCE and motion_state["source_audio"] is not None:
                segment_audio = _audio_slice(motion_state["source_audio"], offset, int(segment.shape[0]), fps)
            else:
                segment_audio = run["audio"][-1]
            saved = FeiHouEasyH3MotionCollect._save_segment(segment, segment_audio, fps, filename_prefix, f"seg{index + 1:03d}")
        else:
            saved = None
        run["previous"] = {"samples": comfy.nested_tensor.NestedTensor(
            tuple(t.detach().to("cpu", copy=True) for t in latent["samples"].unbind()))}
        # Decoded tail: the next segment's anchor when its latent has another size (two-pass).
        run["tail"] = _unit_frames(decoded[-overlap:].detach().cpu()) if overlap else None
        run["next"] = index + 1
        del decoded, sound
        ui = {"feihou_motion": [{"next": bool(auto_queue) and not last, "done": index + 1, "count": count}]}
        if saved:
            ui["feihou_motion"][0]["segment_video"] = saved

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


class FeiHouEasyH3MotionPreprocess:
    """H3 动作迁移预处理: 关闭 / 自动 / 人物替换 (Remake + face blur)."""

    CATEGORY = "FeiHou Easy H3"
    FUNCTION = "apply"
    RETURN_TYPES = ("MINIMAX_H3_CONTEXT",)
    RETURN_NAMES = ("h3_context",)
    DESCRIPTION = (
        "H3 动作迁移预处理：接在 Setup 和「动作迁移分段」之间（h3_context 进、h3_context 出）。不用时把节点设为旁路，分段节点就按 H3 原生跑。"
        "模式：关闭 = H3 原生；自动 = 每块按分镜 md 的 @mode（swap / off，没写就关闭）；"
        "人物替换 = Remake 替换（配 Viggle Remake LoRA）。"
        "口播块（@video none）一律关闭。"
        "脸部模糊：参考视频 1 送进模型前，逐帧检测人脸、把脸（眉毛到下巴的椭圆，柔边）高斯模糊，原片那张脸的长相信号变弱，"
        "半身和特写的脸更多从参考图来；头的位置、转头、眨眼、张嘴的明暗还在。成片音轨、颜色匹配和原片直出都用没模糊的原片。"
        "分镜 md 每块可写 @face_blur off/auto/1–5，写了就按它，没写就按这里。"
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "h3_context": ("MINIMAX_H3_CONTEXT", {"tooltip": "接 Setup 的 h3_context，输出接「动作迁移分段」的 h3_context。"}),
            "mode": (PREPROCESS_MODES, {"default": MODE_AUTO,
                "tooltip": "关闭：H3 原生（参考视频 1 当 <Video 1> 参考）。自动：每块按分镜 md 的 @mode，没写就关闭。"
                           "人物替换：Remake 替换——按 Viggle Remake 换人 LoRA（viggle_remake_swap）训练时的格式喂模型，参考视频 1 是要改的原片，"
                           "参考图是目标人物；采样固定用它的 6 步 swap 曲线，接续段用上一段的整 17 帧当干净前缀；"
                           "Remake LoRA 放在工作流前面的 LoRA 列表里（旁路、强度 1），关掉加速 LoRA，cfg 保持 1。"}),
            "face_blur_on": ("BOOLEAN", {"default": True, "label_on": "开启", "label_off": "关闭",
                "tooltip": "原片脸部模糊的总开关（只在 人物替换 时生效）。关闭时不模糊，md 里的 @face_blur 也不起作用，下面两项变灰。"}),
            "face_blur": ([c for c in remake_mode.FACE_BLUR_CHOICES if c != remake_mode.FACE_BLUR_OFF],
                          {"default": remake_mode.FACE_BLUR_AUTO,
                "tooltip": "自动：按每一帧脸在画面里的大小定档，远景小脸几乎不模糊，半身约 2–3 档，脸部特写到 5 档，相邻帧平滑过渡；"
                           "1–5 档：固定强度，越高越糊，原片长相去得越干净，表情和口型传过去得越少（5 档基本只剩头的位置和眼嘴的明暗）。"
                           "分镜 md 每块写 @face_blur 关/自动/1–5 时按 md。"}),
            "face_detector": (remake_mode.detector_choices(), {
                "tooltip": "人脸检测模型（models/ultralytics/bbox/）。推荐 face_yolov8m.pt：精度和速度最平衡；"
                           "face_yolov9c.pt 精度略高、慢一些；n/s 版本快但侧脸和小脸容易漏。"}),
            "face_recognizer": (remake_mode.recognizer_choices(), {"default": remake_mode.RECOGNIZER_DEFAULT,
                "tooltip": "人脸识别模型：md 写了 @blur_people（只模糊指定的人）时，用它认出每一帧里的脸是谁。"
                           "列出 models/insightface/models/ 下同时有 2d106det.onnx（对齐五官）和识别模型的包。"
                           "默认 buffalo_l/w600k_r50.onnx（ArcFace R50，WebFace600K 训练，正脸侧脸都认得准）；"
                           "antelopev2/glintr100.onnx 是更大的 R100，稍慢。没写 @blur_people 时不用。"}),
        }, "optional": {
            "shot_prompts": (SHOT_TYPE, {"forceInput": True, "tooltip":
                "可选：接「分镜提示词」的 production_shot。接上后预览按本块 md 的 @face_blur；不接时预览按本节点的档位（实际采样仍按 md）。"}),
        }, "hidden": {"unique_id": "UNIQUE_ID"}}

    def apply(self, h3_context, mode=MODE_AUTO, face_blur_on=True, face_blur=remake_mode.FACE_BLUR_AUTO,
              face_detector=remake_mode.FACE_DETECTOR, face_recognizer=remake_mode.RECOGNIZER_DEFAULT,
              shot_prompts=None, unique_id=None, remake=None, **_removed):
        """Talk blocks (@video none) skip every mode: Motion Segment turns them off there.
        ``remake`` is the switch saved before 模式 (on = 人物替换, off = 关闭)."""
        if not isinstance(h3_context, MiniMaxH3Context):
            raise ValueError("H3 动作迁移预处理：h3_context 要接 FeiHou Easy H3 Setup 的 h3_context。")
        if remake is not None and mode in (None, MODE_AUTO):  # an API call saved before 模式
            mode = MODE_SWAP if remake else MODE_OFF
        if mode in ("动作迁移", "动作引导（实验）"):  # saved before these modes were removed
            LOGGER.warning("H3 动作迁移预处理：%s模式已移除，按关闭", mode)
            mode = MODE_OFF
        if mode not in PREPROCESS_MODES:
            raise ValueError(f"H3 动作迁移预处理：模式只能是 {' / '.join(PREPROCESS_MODES)}，实际是 {mode!r}")
        setting = remake_mode.parse_face_blur(face_blur) if face_blur_on else None
        LOGGER.info("Easy H3 动作迁移预处理：%s，脸部模糊 %s，检测模型 %s",
                    mode,
                    f"{face_blur}（md 每块的 @face_blur 优先）" if setting else "关闭", face_detector)
        preprocess = {"mode": mode, "remake": mode == MODE_SWAP, "face_blur": setting, "face_detector": str(face_detector),
                      "face_recognizer": str(face_recognizer)}
        result = replace(h3_context, motion_preprocess=preprocess)
        shot = shot_prompts.get("motion") if isinstance(shot_prompts, dict) else None
        block_mode = _block_mode(preprocess, shot)
        remake = block_mode == MODE_SWAP
        preview = {"summary": "人物替换，脸部模糊已关闭" if remake else f"{block_mode}，不模糊"}
        if not remake and mode == MODE_AUTO and not shot:
            # 自动 without the md here: Motion Segment still follows each block's @mode.
            preview = {"summary": "操作是「自动」：实际采样时由「动作迁移分段」按每块 md 的 @mode 决定换不换人、模糊不模糊。"
                                  "本节点没接「分镜提示词」，所以这里看不到模糊预览；要预览就把分镜提示词也接到本节点。"}
        elif not remake and mode == MODE_AUTO and not shot.get("mode"):
            # 自动 with an md block that has no @mode: nothing is swapped or blurred; say why.
            preview = {"summary": "操作是「自动」，但本块 md 没写 @mode swap，所以关闭：不换人、不模糊。"
                                  "要换人：md 页头加一行 @mode swap，或把操作改成「人物替换」。"}
        if shot and shot.get("video") is False:
            preview = {"summary": f"分镜 {shot.get('block')} 是口播块（@video none），不送参考视频，不做人物替换和脸部模糊"}
        elif remake and setting:
            block_setting, detector = _block_face_blur(preprocess, shot)
            if block_setting:
                preview = self._preview(h3_context, block_setting, detector, shot, str(face_recognizer))
            else:
                preview = {"summary": "本块 md 写了 @face_blur 关，不模糊"}
        return {"ui": {"feihou_preprocess": [preview]}, "result": (result,)}

    @staticmethod
    def _preview(h3_context, setting, detector, shot, recognizer=remake_mode.RECOGNIZER_DEFAULT):
        """Detect the faces of the whole block (progress bar), then write the blurred preview video."""
        builder = getattr(h3_context, "segment_builder", None)
        window = getattr(builder, "source_window", None)
        if window is None:
            return {"summary": "没有参考视频 1，无法预览"}
        frames = window(0, 10 ** 9)
        count = int(frames.shape[0])
        if count == 0:
            return {"summary": "参考视频 1 没有画面"}
        video, _audio = _split_av(h3_context.latent["samples"])
        width, height = int(video.shape[-1]) * VAE_DOWNSAMPLE, int(video.shape[-2]) * VAE_DOWNSAMPLE
        canvas = lambda start, stop: remake_mode.cover(frames[start:stop], width, height)
        bar = comfy.utils.ProgressBar(count * 2)
        chunk = 32
        people_key = _block_people(builder, shot, detector, recognizer)
        vectors = remake_mode.people(people_key)
        faces = []
        for start in range(0, count, chunk):
            stop = min(count, start + chunk)
            progress = lambda done, s=start: bar.update_absolute(s + done, count * 2)
            if vectors:
                faces += remake_mode.frame_faces(canvas(start, stop), detector, progress=progress, recognizer=recognizer)
            else:
                faces += [[box] if box is not None else []
                          for box in remake_mode.face_boxes(canvas(start, stop), detector, progress=progress)]
            bar.update_absolute(stop, count * 2)
        if vectors:
            tracks = remake_mode.assign_people(faces, vectors)
        else:
            tracks = [remake_mode._smooth_boxes([found[0] if found else None for found in faces])]
        boxes = [box for track in tracks for box in track]
        found = sum(any(track[i] is not None for track in tracks) for i in range(count))
        divisors = [remake_mode.blur_plan(track, setting, height) for track in tracks]
        name = f"preprocess_{uuid.uuid4().hex[:12]}.mp4"
        subfolder = "feihou_preprocess"
        folder = os.path.join(folder_paths.get_temp_directory(), subfolder)
        os.makedirs(folder, exist_ok=True)
        remake_mode.write_preview(os.path.join(folder, name), canvas, count, tracks, divisors, chunk=chunk,
                                  progress=lambda done: bar.update_absolute(count + done, count * 2))
        block = f"分镜 {shot.get('block')} · " if isinstance(shot, dict) and shot.get("block") else ""
        who = (f"{found} 帧认出要模糊的 {len(tracks)} 个人" if vectors else f"{found} 帧检测到人脸")
        summary = (f"{block}{count} 帧（{count / h3.FPS:.1f} 秒）{width}x{height}，{who}，"
                   f"脸部模糊 {remake_mode.describe_plan(boxes, setting, height)}")
        LOGGER.info("Easy H3 动作迁移预处理：预览 %s", summary)
        return {"filename": name, "subfolder": subfolder, "type": "temp", "format": "video/h264-mp4",
                "summary": summary}
