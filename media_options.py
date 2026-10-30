"""Per-video 「媒体设置」 of the Easy H3 Media gallery.

Each reference-video card can carry VHS_LoadVideo-style options: frame rate
(``force_rate``, 0 = the file's own rate), short edge, frame load cap and
skipped leading frames.  They travel to Setup in the hidden
``media_opts_N`` field as JSON and are applied while the file is decoded:
frames outside the window are never converted and kept frames are scaled down
right away, so a long 1080p source does not have to fit in memory first.

``probe_video`` backs the popup's source information (and the 0 = original
frame rate linkage) through ``/feihou_easy_h3/media/probe``.
"""

from __future__ import annotations

import json
import logging
import math
import os
from collections.abc import Mapping
from typing import Any

import numpy as np
import torch

# Short-edge presets (only ever downscaled; aspect ratio kept).
VIDEO_SIZE_ORIGINAL = "原始"
VIDEO_SIZE_MATCH_IMAGE = "匹配参考图"
VIDEO_SHORT_EDGES = {f"{edge}P": edge for edge in (360, 480, 540, 640, 720)}
VIDEO_SIZES = [VIDEO_SIZE_ORIGINAL, VIDEO_SIZE_MATCH_IMAGE, *VIDEO_SHORT_EDGES]
DEFAULT_FPS = 24.0
CANVAS_MULTIPLE = 32


def parse_video_options(value: Any) -> dict | None:
    """Normalized options from the ``media_opts_N`` field, or None when absent."""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            value = json.loads(text)
        except ValueError:
            return None
    if not isinstance(value, dict):
        return None

    def number(key, default, cast):
        try:
            result = cast(value.get(key, default))
        except (TypeError, ValueError):
            return default
        return result if math.isfinite(result) and result >= 0 else default

    size = str(value.get("size") or VIDEO_SIZE_ORIGINAL)
    return {
        "fps": min(120.0, number("fps", DEFAULT_FPS, float)),
        "size": size if size in VIDEO_SIZES else VIDEO_SIZE_ORIGINAL,
        "skip": int(number("skip", 0, int)),
        "cap": int(number("cap", 0, int)),
    }


def fit_short_edge(width: int, height: int, short_edge: int) -> tuple[int, int]:
    scale = min(1.0, float(short_edge) / max(1, min(width, height)))
    step = CANVAS_MULTIPLE
    return max(step, round(width * scale / step) * step), max(step, round(height * scale / step) * step)


def fit_area(width: int, height: int, area: float) -> tuple[int, int]:
    scale = min(1.0, math.sqrt(max(1.0, area) / max(1, width * height)))
    step = CANVAS_MULTIPLE
    return max(step, int(width * scale) // step * step), max(step, int(height * scale) // step * step)


def target_size(width: int, height: int, size: str, image_area: float | None) -> tuple[int, int] | None:
    """Decoded frame size for a preset, or None to keep the source size."""
    if size in VIDEO_SHORT_EDGES:
        target = fit_short_edge(width, height, VIDEO_SHORT_EDGES[size])
    elif size == VIDEO_SIZE_MATCH_IMAGE and image_area:
        target = fit_area(width, height, image_area)
    else:
        return None
    return None if target == (width, height) else target


def _native_fps(stream) -> float:
    for rate in (stream.average_rate, stream.guessed_rate, stream.base_rate):
        try:
            value = float(rate)
        except (TypeError, ZeroDivisionError):
            continue
        if math.isfinite(value) and value > 0:
            return value
    return DEFAULT_FPS


def probe_video(path: str) -> dict:
    import av

    with av.open(path) as container:
        stream = container.streams.video[0]
        fps = _native_fps(stream)
        if stream.duration is not None and stream.time_base:
            duration = float(stream.duration * stream.time_base)
        elif container.duration:
            duration = container.duration / 1_000_000
        else:
            duration = 0.0
        frames = int(stream.frames or 0) or round(duration * fps)
        return {
            "fps": round(fps, 3),
            "frames": frames,
            "duration": round(duration, 3),
            "width": int(stream.codec_context.width),
            "height": int(stream.codec_context.height),
            "has_audio": bool(container.streams.audio),
        }


def _frame_array(frame, size: tuple[int, int] | None) -> np.ndarray:
    if size is None:
        return frame.to_ndarray(format="rgb24")
    try:
        return frame.reformat(width=size[0], height=size[1], format="rgb24", interpolation="AREA").to_ndarray()
    except (TypeError, ValueError):
        return frame.reformat(width=size[0], height=size[1], format="rgb24").to_ndarray()


def _sample_frames(path: str, rate: float | None, skip: int, cap: int, size_of) -> tuple[list[np.ndarray], float]:
    """Frames ``skip .. skip + cap`` of the video resampled to ``rate`` (None = native).

    An output frame at time k / rate shows the last source frame shown at or
    before that time, like VHS's force_rate.
    """
    import av

    with av.open(path) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        native = _native_fps(stream)
        rate = rate or native
        size = size_of(int(stream.codec_context.width), int(stream.codec_context.height))
        origin = float(stream.start_time * stream.time_base) if stream.start_time is not None else 0.0
        start_time = skip / rate
        if start_time > 1.0:
            # Land on the keyframe before the window; earlier frames only feed `previous`.
            container.seek(int((origin + start_time - 1.0) / stream.time_base), stream=stream, backward=True)
        end = skip + cap if cap > 0 else None
        out: list[np.ndarray] = []
        index = skip
        previous = previous_array = None
        previous_time = 0.0
        epsilon = 0.25 / max(rate, native)

        def emit_until(limit_time):
            nonlocal index, previous_array
            while (end is None or index < end) and index / rate < limit_time - epsilon:
                if previous_array is None:
                    previous_array = _frame_array(previous, size)
                out.append(previous_array)
                index += 1

        for frame in container.decode(stream):
            if frame.pts is None:
                continue
            time = float(frame.pts * stream.time_base) - origin
            if previous is not None:
                emit_until(time)
            if end is not None and index >= end:
                break
            previous, previous_array, previous_time = frame, None, time
        if previous is not None and (end is None or index < end):
            emit_until(previous_time + 1.0 / native)
        return out, rate


def _slice_audio(path: str, start: float, end: float):
    from comfy_extras import nodes_audio as comfy_audio_nodes

    try:
        waveform, sample_rate = comfy_audio_nodes.load(path)
    except Exception as exc:  # no audio stream, or an undecodable one
        logging.info("Easy H3 媒体设置: 读取视频音轨失败（%s），按无声处理", exc)
        return None
    first, last = round(start * sample_rate), round(end * sample_rate)
    waveform = waveform[..., first:last]
    if waveform.shape[-1] == 0:
        return None
    return {"waveform": waveform.unsqueeze(0), "sample_rate": int(sample_rate)}


def timeline(path: str, options: dict | None, trim: tuple[float, float] | None) -> tuple[float, float, dict]:
    """Where reference video 1's timeline starts in the file, and how long it runs (seconds).

    The 媒体设置 frame window (or else the card's time trim) defines it; shot
    md ranges are measured from its start.
    """
    info = probe_video(path)
    duration = float(info["duration"])
    if options is not None:
        rate = options["fps"] or float(info["fps"])
        origin = options["skip"] / rate
        length = options["cap"] / rate if options["cap"] else duration - origin
    else:
        start, end = trim or (0.0, 0.0)
        origin = min(start, duration)
        length = (min(end, duration) if end > 0 else duration) - origin
    return origin, max(0.0, min(length, duration - origin)), info


def hold_audio(audio: Any, seconds: float) -> Any:
    """``audio`` with ``seconds`` of silence in front (the shot md's @hold)."""
    if not isinstance(audio, Mapping) or not isinstance(audio.get("waveform"), torch.Tensor) or seconds <= 0:
        return audio
    waveform = audio["waveform"]
    rate = int(audio.get("sample_rate") or 32000)
    silence = waveform.new_zeros((*waveform.shape[:-1], round(seconds * rate)))
    return dict(audio, waveform=torch.cat([silence, waveform], dim=-1))


def hold_indexes(start_frame: int, frames: int, hold: int, total: int) -> torch.Tensor:
    """Source frames shown at timeline frames [start_frame, start_frame + frames) with ``hold`` frames held.

    The timeline opens with the first frame held for ``hold`` frames, then the
    whole video; ``total`` is the video's own length in frames.
    """
    timeline_frames = torch.arange(int(start_frame), int(start_frame) + int(frames))
    return (timeline_frames - int(hold)).clamp(min=0, max=max(0, int(total) - 1))


def load_video_window(path: str, options: dict | None, trim: tuple[float, float] | None,
                      start_frame: int, frames: int, image_area: float | None = None, fps: float = 24.0,
                      hold: int = 0) -> dict:
    """Only frames [start_frame, start_frame + frames) of the timeline, at ``fps``.

    For one motion-transfer shot block: the rest of the film is never decoded.
    ``frame_offset`` / ``total_frames`` keep frame numbers absolute for the
    readers; the soundtrack covers the whole timeline (it is small) so audio
    stays indexed from the timeline start.

    ``hold`` (the shot md's @hold) puts the video's first frame, held for that
    many frames and silent, in front of the timeline.
    """
    origin, length, info = timeline(path, options, trim)
    total = max(1, round(length * fps))
    hold = max(0, int(hold))
    start_frame = max(0, min(int(start_frame), hold + total - 1))
    count = max(1, min(int(frames), hold + total - start_frame))
    indexes = hold_indexes(start_frame, count, hold, total)
    first = int(indexes[0])
    size = options["size"] if options is not None else VIDEO_SIZE_ORIGINAL
    arrays, _rate = _sample_frames(path, fps, round(origin * fps) + first, int(indexes[-1]) - first + 1,
                                   lambda width, height: target_size(width, height, size, image_area))
    if not arrays:
        raise ValueError(f"参考视频「{os.path.basename(path)}」在第 {first} 帧之后没有画面（视频长度 {total} 帧）")
    height, width = arrays[0].shape[:2]
    decoded = torch.empty((len(arrays), height, width, 3), dtype=torch.float32)
    for position, array in enumerate(arrays):
        decoded[position] = torch.from_numpy(array).to(torch.float32).div_(255.0)
    images = decoded[(indexes - first).clamp(max=len(arrays) - 1)] if hold else decoded
    audio = _slice_audio(path, origin, origin + length) if info["has_audio"] else None
    audio = hold_audio(audio, hold / fps)
    logging.info("Easy H3 媒体: %s 只读分镜这一段：第 %d-%d 帧（共 %d 帧%s）%dx%d",
                 os.path.basename(path), start_frame, start_frame + len(images) - 1, hold + total,
                 f"，开头定格 {hold} 帧" if hold else "", width, height)
    return {"images": images, "audio": audio, "fps": float(fps), "frame_offset": start_frame,
            "total_frames": hold + total}


def load_video(path: str, options: dict, image_area: float | None = None) -> dict:
    """Decode a reference video with its 媒体设置 into ``{"images", "audio", "fps"}``."""
    has_audio = probe_video(path)["has_audio"]
    arrays, rate = _sample_frames(
        path, options["fps"] or None, options["skip"], options["cap"],
        lambda width, height: target_size(width, height, options["size"], image_area))
    if not arrays:
        raise ValueError(f"参考视频「{os.path.basename(path)}」按媒体设置没有读到任何帧"
                         f"（跳过 {options['skip']} 帧，可能已超过视频长度）")
    height, width = arrays[0].shape[:2]
    images = torch.empty((len(arrays), height, width, 3), dtype=torch.float32)
    for position, array in enumerate(arrays):
        images[position] = torch.from_numpy(array).to(torch.float32).div_(255.0)
    start = options["skip"] / rate
    audio = _slice_audio(path, start, start + len(arrays) / rate) if has_audio else None
    logging.info("Easy H3 媒体设置: %s → %d 帧 %dx%d @ %.3gfps（跳过 %d 帧）",
                 os.path.basename(path), len(arrays), width, height, rate, options["skip"])
    return {"images": images, "audio": audio, "fps": float(rate)}
