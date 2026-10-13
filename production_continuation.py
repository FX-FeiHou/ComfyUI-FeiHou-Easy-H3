"""FeiHou Easy H3 continuation: shot-by-shot production-pack generation.

The normal 1st/2nd-pass chain stays exactly as it is.  Two small nodes wrap it
so that queueing the production pack shot after shot continues each shot from
the previous one:

  Production Pack Loader (continuation on) -> Setup -> 1st/2nd pass (unchanged)
      -> decode / duration crop -> [Continue Out] -> Video Combine (shot) / Video Combine (full)

* The loader (``continue_shot``) lengthens the shot by the overlap
  (22/39 frames) and attaches the previous shot's last frames and soundtrack.
  Setup anchors them at frame 0 with H3's native guide (the keyframe format of
  the official "Add Guide for MiniMax H3" node); the guide is re-encoded at the
  canvas of each pass, so both the 1st (LOW) and 2nd (HIGH) pass see it.
* Continue Out removes the regenerated overlap frames and remembers this shot's
  tail for the next queue run.  Its ``tail_latent`` goes to Video Combine V2
  (FeiHou Toolbox), which saves it as ``<video>.latent`` next to the shot video,
  records the shot in a per-pack manifest and joins the shot videos after the
  last shot.  A later run (also after a restart) continues from that file when
  the previous shot is not in memory.

The loader's shot index increments after each queue, so queueing the shot
count runs the whole pack.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import replace
from typing import Any

import torch

import folder_paths
import nodes
from comfy.ldm.minimax.model import FRAME_RESCALE
from comfy_extras import nodes_audio as comfy_audio_nodes
from comfy_extras import nodes_minimax_h3 as h3

from .nodes import H3HighresRebuilder, _frame_length


LOGGER = logging.getLogger(__name__)
VAE_DOWNSAMPLE = 16
SHOT_TYPE = "FEIHOU_H3_PRODUCTION_SHOT"
CONTINUATION_KEY = "feihou_continuation"
# Tail of the last finished shot, kept between queue runs (ComfyUI process memory).
_LAST: dict = {}
# "1st+2nd" continuation frames: the 1st pass (composition/motion) sees the
# whole overlap, the 2nd (detail) pass only its last N frames (0 = none).
CONTEXT_CHOICES = ["自动", "0+5", "5+0", "5+5", "22+0", "22+5", "22+22", "39+0", "39+5", "39+22", "39+39"]
CONTEXT_DEFAULT = "自动"
CONTEXT_FALLBACK = "22+22"  # "自动" when the package does not specify frames
# How each shot joins the previous one.  "自动" follows the package's per-shot
# transition (shots without one are continued).
TRANSITION_CHOICES = ["自动", "接续", "硬切"]
TRANSITION_DEFAULT = "自动"
MAX_TAIL_FRAMES = 39
MIN_OVERLAP_FRAMES = 22  # lead-in trimmed before the kept frames, even for 5-frame guides
# Audio context: at least one second, ending at the seam.
AUDIO_MIN_FRAMES = 24
CONTINUE_FROM = ["latent", "image"]


def _align_up(frames: int) -> int:
    """Smallest valid H3 frame count (5 + 17N) that is >= frames."""
    frames = max(5, int(frames))
    return 5 + 17 * math.ceil((frames - 5) / 17)


def _audio_slice(audio: Any, start_frame: float, frame_count: int, fps: float) -> Any:
    """Cut [start_frame, start_frame + frame_count) of a decoded soundtrack (zero padded)."""
    if not isinstance(audio, dict) or not isinstance(audio.get("waveform"), torch.Tensor):
        return None
    sample_rate = int(audio.get("sample_rate", 44100))
    start = max(0, round(float(start_frame) / float(fps) * sample_rate))
    length = round(float(frame_count) / float(fps) * sample_rate)
    waveform = audio["waveform"][..., start:start + length]
    if waveform.shape[-1] < length:
        padded = waveform.new_zeros((*waveform.shape[:-1], length))
        padded[..., :waveform.shape[-1]] = waveform
        waveform = padded
    return {"waveform": waveform.detach().cpu().contiguous(), "sample_rate": sample_rate}


def _guided_context(context: Any, info: dict):
    """Anchor the previous shot's tail at the start of this shot, per pass.

    1st pass (the Setup canvas): the whole overlap as pixels -> composition and motion.
    2nd pass (any larger canvas): only the last 5 frames, taken straight from the
    previous 2nd-pass latent in "latent" mode (no VAE round trip, no face refine),
    otherwise re-encoded from the pixels.  Audio: >= 1 s ending at the seam.
    """
    rebuilder = getattr(context, "highres_rebuilder", None)
    if rebuilder is None or not callable(getattr(rebuilder, "build", None)):
        raise ValueError("续接需要 Setup 的 h3_context 重建信息，请重新连接 FeiHou Easy H3 Setup。")
    tail = info["tail"]
    overlap = int(info["overlap"])
    if tail.get("images") is None:
        # Continued from a saved .latent (latent mode): decode its video tail once.
        decoded = nodes.VAEDecode().decode(context.video_vae, {"samples": tail["latent_video"]})[0]
        tail["images"] = decoded[-int(tail.get("frames") or decoded.shape[0]):].detach().cpu().float()
    images = tail["images"][-overlap:]
    lead = min(int(info.get("first", overlap)), overlap)    # 1st-pass guide frames (0 = none)
    short = min(int(info.get("second", overlap)), overlap)  # 2nd-pass guide frames (0 = none)
    fps_tail = float(tail.get("fps") or 24.0)
    audio_frames = min(max(AUDIO_MIN_FRAMES, overlap), int(tail.get("audio_frames") or overlap))
    video = context.latent["samples"].tensors[0]
    base = (int(video.shape[-1]) * VAE_DOWNSAMPLE, int(video.shape[-2]) * VAE_DOWNSAMPLE)
    audio_t = int(context.latent["samples"].tensors[1].shape[-1])

    audio_guide = None
    if tail.get("mode") == "latent" and tail.get("latent_audio") is not None:
        tokens = tail["latent_audio"][..., -max(1, round(audio_frames * FRAME_RESCALE)):]
        end = round(overlap * FRAME_RESCALE + float(tail.get("overhang", 0.0)))
        audio_guide = {"resolved_frame_index": (end - tokens.shape[-1]) / FRAME_RESCALE,
                       "audio_latent": tokens[..., :max(1, audio_t)].clone()}
    elif tail.get("audio") is not None:
        stored = int(tail.get("audio_frames") or audio_frames)
        clip = _audio_slice(tail["audio"], stored - audio_frames, audio_frames, fps_tail)
        audio_latent, _ = h3._encode_ref_audio(context.audio_vae, clip)
        audio_guide = {"resolved_frame_index": overlap - audio_frames,
                       "audio_latent": audio_latent[..., :max(1, audio_t)].clone()}

    cache: dict[tuple[int, int], dict] = {}

    def video_guide(width: int, height: int) -> dict:
        key = (int(width), int(height))
        if key not in cache:
            if key == base:
                # 1st pass: the last `lead` frames, ending at the seam ("0+" = no guide).
                cache[key] = ({"resolved_frame_index": overlap - lead,
                               "latent": context.video_vae.encode(h3._resize(images[-lead:], key[0], key[1], "center"))}
                              if lead > 0 else None)
            elif short <= 0:
                cache[key] = None  # "+0": the 2nd pass gets no video guide
            else:
                latent = tail.get("latent_video") if tail.get("mode") == "latent" else None
                steps = (short - 5) // 17 * 5 + 2  # H3 latent steps for 5/22/39 frames
                if latent is not None and latent.shape[2] >= steps:
                    latent = latent[:, :, -steps:]
                elif latent is not None:
                    latent = None
                if latent is None or tuple(latent.shape[-2:]) != (key[1] // VAE_DOWNSAMPLE, key[0] // VAE_DOWNSAMPLE):
                    if latent is not None:
                        LOGGER.warning("Easy H3 continuation: previous latent %s does not match the %dx%d canvas; "
                                       "using the decoded frames", tuple(latent.shape[-2:]), key[0], key[1])
                    latent = context.video_vae.encode(h3._resize(images[-short:], key[0], key[1], "center"))
                cache[key] = {"resolved_frame_index": overlap - short, "latent": latent}
        return cache[key]

    def add(conditioning, width: int, height: int):
        guides = [g for g in (video_guide(width, height), audio_guide) if g]
        output = []
        for embedding, metadata in conditioning:
            metadata = dict(metadata or {})
            # Keyframes inside the overlap (e.g. an I2V first frame) would fight
            # the continuation guide; keyframes after it (a last frame) stay.
            kept = [dict(item) for item in metadata.get("minimax_keyframes", ())
                    if int(item.get("resolved_frame_index", 0)) >= overlap]
            metadata["minimax_keyframes"] = [*guides, *kept]
            output.append([embedding, metadata])
        return output

    def build(width, height, trigger_prefix=""):
        conditioning, template = rebuilder.build(width, height, trigger_prefix)
        return add(conditioning, width, height), template

    return replace(
        context,
        conditioning=add(context.conditioning, *base),
        highres_rebuilder=H3HighresRebuilder(build=build, canvas_dependent=True),
    )


TAIL_SCHEMA = "feihou_h3_tail/1"
SAVE_KEY = "feihou_save"  # read by Video Combine V2 (FeiHou Toolbox)


def _manifest_path(pack_key) -> str | None:
    if not pack_key:
        return None
    return os.path.join(folder_paths.get_output_directory(), "feihou_h3_continuation", f"{pack_key}.json")


def _latent_frames(steps: int) -> int:
    return (int(steps) - 2) // 5 * 17 + 5


def _load_saved(pack_key, index: int, total: int) -> dict:
    """Tail of shot ``index`` from the .latent file Video Combine V2 saved (empty if absent)."""
    manifest = _manifest_path(pack_key)
    try:
        with open(manifest, encoding="utf-8") as stream:
            entry = (json.load(stream).get("shots") or {}).get(str(index)) or {}
    except (TypeError, OSError, ValueError):
        return {}
    path = entry.get("latent")
    if not path or not os.path.isfile(path):
        return {}
    record = _read_tail_file(path)
    if record:
        record.update(index=index, pack_key=pack_key)
        record["total"] = int(record.get("total") or total)
        LOGGER.info("Easy H3 continuation: shot %d continues from %s", index + 1, path)
    return record


def _read_tail_file(path: str) -> dict:
    """Tail record from a .latent saved by Video Combine V2 (empty if it is not one)."""
    import safetensors
    import safetensors.torch
    with safetensors.safe_open(path, framework="pt", device="cpu") as handle:
        meta = handle.metadata() or {}
    if meta.get("feihou_schema") != TAIL_SCHEMA:
        return {}
    tensors = safetensors.torch.load_file(path, device="cpu")
    number = lambda key, default=0.0: float(meta.get(key) or default)
    record = {"index": int(number("index", 0)), "total": int(number("total", 0)), "pack_key": meta.get("pack_key"),
              "series": meta.get("series") or time.strftime("%Y%m%d_%H%M%S"),
              "fps": number("fps", 24.0), "audio_frames": int(number("audio_frames", AUDIO_MIN_FRAMES)),
              "mode": meta.get("mode") or "image", "images": None, "audio": None, "source": path}
    if "tail_images" in tensors:
        record["images"] = tensors["tail_images"].float() / 255.0
        record["frames"] = int(record["images"].shape[0])
    if "tail_audio" in tensors:
        record["audio"] = {"waveform": tensors["tail_audio"], "sample_rate": int(number("sample_rate", 44100))}
    if record["mode"] == "latent" and "latent_tensor" in tensors and "latent_audio" in tensors:
        record.update(latent_video=tensors["latent_tensor"], latent_audio=tensors["latent_audio"],
                      overhang=number("overhang"))
        record.setdefault("frames", _latent_frames(tensors["latent_tensor"].shape[2]))
    elif record["images"] is None:
        return {}
    else:
        record["mode"] = "image"
    return record


VIDEO_EXTENSIONS = (".mp4", ".webm", ".mov", ".mkv", ".m4v")
CONTINUE_VIDEO_AUTO = "自动"


def resolve_continue_video(choice) -> str | None:
    """Absolute path of an "output/..." / "input/..." choice or a full path (None for 自动)."""
    text = str(choice or "").strip().strip('"')
    if not text or text in (CONTINUE_VIDEO_AUTO, "auto"):
        return None
    if os.path.isabs(text):
        if not os.path.isfile(text):
            raise ValueError(f"接续视频找不到：{text}。请重新选择，或改回「自动」。")
        return text
    label, _, relative = text.partition("/")
    roots = {"output": folder_paths.get_output_directory(), "input": folder_paths.get_input_directory()}
    root = roots.get(label)
    path = os.path.realpath(os.path.join(root, relative)) if root else ""
    try:
        inside = bool(root) and os.path.commonpath([path, os.path.realpath(root)]) == os.path.realpath(root)
    except ValueError:  # different drives
        inside = False
    if not inside or not os.path.isfile(path):
        raise ValueError(f"接续视频找不到：{text}。请重新选择，或改回「自动」。")
    return path


def _read_video_tail(path: str) -> dict:
    """Tail record from a finished video: its last 39 frames and soundtrack (image mode)."""
    import collections

    import av
    import numpy as np

    frames = collections.deque(maxlen=MAX_TAIL_FRAMES)
    count = 0
    with av.open(path) as container:
        if not container.streams.video:
            raise ValueError(f"接续视频没有画面：{path}")
        stream = container.streams.video[0]
        fps = float(stream.average_rate) if stream.average_rate else 24.0
        for frame in container.decode(stream):
            frames.append(frame.to_ndarray(format="rgb24"))
            count += 1
    if not frames:
        raise ValueError(f"接续视频读不到画面：{path}")
    images = torch.from_numpy(np.stack(frames)).float() / 255.0
    tail = int(images.shape[0])
    audio_frames = min(max(AUDIO_MIN_FRAMES, tail), count)
    audio = None
    with av.open(path) as container:
        if container.streams.audio:
            stream = container.streams.audio[0]
            resampler = av.AudioResampler(format="fltp", layout="stereo", rate=stream.rate or 44100)
            chunks = []
            for frame in container.decode(stream):
                chunks.extend(out.to_ndarray() for out in resampler.resample(frame))
            chunks.extend(out.to_ndarray() for out in resampler.resample(None))
            if chunks:
                waveform = torch.from_numpy(np.concatenate(chunks, axis=-1).astype(np.float32)).unsqueeze(0)
                full = {"waveform": waveform, "sample_rate": int(stream.rate or 44100)}
                audio = _audio_slice(full, count - audio_frames, audio_frames, fps)
    return {"index": 0, "total": 0, "pack_key": None, "series": time.strftime("%Y%m%d_%H%M%S"),
            "fps": fps, "frames": tail, "images": images, "audio": audio,
            "audio_frames": audio_frames, "mode": "image", "source": path}


def _sibling_video(path: str) -> str | None:
    """The video saved next to a .latent (same name), if any."""
    stem = os.path.splitext(path)[0]
    return next((stem + ext for ext in VIDEO_EXTENSIONS if os.path.isfile(stem + ext)), None)


def _explicit_tail(path: str) -> dict:
    """Previous-shot tail from a chosen video: its .latent next to it when saved, else the video itself."""
    sibling = os.path.splitext(path)[0] + ".latent"
    record = _read_tail_file(sibling) if os.path.isfile(sibling) else {}
    if not record and path.lower().endswith(".latent"):
        raise ValueError(f"所选 .latent 不是 FeiHou Easy H3 接续保存的文件：{path}")
    if not record:
        record = _read_video_tail(path)
    LOGGER.info("Easy H3 continuation: continuing from the chosen video %s (%s)", path,
                "saved .latent" if record.get("source") == sibling else "decoded frames")
    return record


MANUAL_SCHEMA = "feihou_continuation_only"


def manual_continuation(continuation=TRANSITION_DEFAULT, context_frames=CONTEXT_DEFAULT,
                        continue_video=CONTINUE_VIDEO_AUTO):
    """The pack loader with batch runs switched off: a continuation-only "shot".

    Setup keeps its own prompt, media and settings and only receives the chosen
    video's tail.  Without a chosen video (or with 硬切) the run is a plain hard
    cut: None, so nothing is lengthened, trimmed or saved as .latent.
    """
    hard_cut = continuation is False or continuation == "硬切"
    chosen = resolve_continue_video(continue_video)
    if hard_cut:
        LOGGER.info("Easy H3 接续：%s", "前序视频接续不生效，执行硬切" if chosen else "执行硬切")
        return None
    if not chosen:
        if continuation is True or continuation == "接续":
            raise ValueError("未选择前序视频，接续任务中止。请在「接续视频」选择要接续的视频，或把分镜衔接改为「自动」/「硬切」。")
        LOGGER.info("Easy H3 接续：未选择前序视频，执行硬切")
        return None
    last = _explicit_tail(chosen)
    frames = context_frames
    if str(frames).strip() in ("自动", "auto"):
        frames = CONTEXT_FALLBACK
    first, second = parse_context(frames)
    longest = max(first, second)
    overlap = min(max(longest, MIN_OVERLAP_FRAMES) if longest else 0, int(last["frames"]))
    info = {"series": last.get("series") or time.strftime("%Y%m%d_%H%M%S"), "index": 1, "total": 1,
            "overlap": overlap, "first": min(first, overlap), "second": min(second, overlap), "tail": last,
            "pack_key": None, "context_frames": MAX_TAIL_FRAMES, "manual": True,
            "transition": "continue", "save": True, "previous": None, "title": ""}
    LOGGER.info("Easy H3 接续：接续前序视频 %s（%d+%d）", chosen, info["first"], info["second"])
    return {"schema": MANUAL_SCHEMA, CONTINUATION_KEY: info}


def prepare_setup(kwargs: dict):
    """Called by Setup: returns (kwargs, guide) for a continued production shot."""
    shot = kwargs.get("production_shot")
    info = shot.get(CONTINUATION_KEY) if isinstance(shot, dict) else None
    if isinstance(shot, dict) and shot.get("schema") == MANUAL_SCHEMA:
        # Manual run: Setup's own prompt/media/settings, lengthened by the overlap.
        kwargs = dict(kwargs)
        kwargs.pop("production_shot", None)
        if isinstance(info, dict) and info.get("overlap"):
            fps = float(kwargs.get("fps") or 24.0)
            base = _frame_length(float(kwargs.get("seconds") or 5.0), fps)
            kwargs["seconds"] = _align_up(base + int(info["overlap"])) / fps
            kwargs["audio_duration_auto"] = "off"
        return kwargs, info if isinstance(info, dict) else None
    if not isinstance(info, dict):
        return kwargs, None
    if not info.get("overlap"):
        return kwargs, info
    kwargs = dict(kwargs)
    # The lengthened shot must not be re-timed to its reference audio/video.
    kwargs["audio_duration_auto"] = "off"
    return kwargs, info


def apply_guide(context: Any, info: dict | None):
    if not info:
        return context
    # Lets "Continue Out" decode the 2nd-pass latent itself when no images are wired.
    info["vae"] = (context.video_vae, context.audio_vae)
    info.setdefault("fps", float(getattr(context, "fps", 0) or 0) or None)
    if not info.get("overlap"):
        return context
    return _guided_context(context, info)


def parse_context(value) -> tuple[int, int]:
    """"22+5" -> (22, 5).  A bare legacy "22"/"39" keeps a 5-frame 2nd pass."""
    text = str(value or CONTEXT_FALLBACK).strip()
    if text in ("自动", "auto"):
        text = CONTEXT_FALLBACK
    first, _, second = text.partition("+")
    first = int(first) if first.strip() in ("0", "5", "22", "39") else 22
    second = int(second) if second.strip() in ("0", "5", "22", "39") else 5
    return first, second


def _transition(mode, production_shot) -> str:
    """'continue' or 'cut' for this shot (how it joins the previous shot)."""
    if mode is True or mode == "接续":
        return "continue"
    if mode is False or mode == "硬切":
        return "cut"
    return production_shot.get("transition") or "continue"  # 自动


def continue_shot(production_shot, continuation=TRANSITION_DEFAULT, context_frames=CONTEXT_DEFAULT,
                  continue_video=CONTINUE_VIDEO_AUTO):
    """Lengthen a production shot and attach the previous shot's tail (used by the pack loader).

    The previous tail comes from memory (the last queue run) or, failing that,
    from the .latent file Video Combine V2 saved for the previous shot.  A hard
    cut ("硬切") gets no overlap and no guide; with the loader set to 硬切 no
    .latent files are saved either.  A video chosen in the loader ("接续视频")
    overrides all of that for this shot: the shot continues from that video.
    """
    if not isinstance(production_shot, dict) or production_shot.get("schema") != 1:
        return production_shot
    index, total = int(production_shot.get("index", 1)), int(production_shot.get("total", 1))
    pack_key = production_shot.get("pack_key")
    params = dict(production_shot.get("params") or {})
    transition = _transition(continuation, production_shot) if index > 1 else "first"
    hard_cut = continuation is False or continuation == "硬切"
    chosen = resolve_continue_video(continue_video)
    if chosen and hard_cut:
        LOGGER.warning("Easy H3 continuation: 分镜衔接 is 硬切, the chosen video %s is ignored", chosen)
        chosen = None
    last = dict(_LAST)
    follows = (index > 1 and last.get("index") == index - 1 and last.get("total") == total
               and last.get("pack_key") == pack_key)
    if chosen:
        last, follows, transition = _explicit_tail(chosen), True, "continue"
    elif index > 1 and not follows and transition == "continue":
        last = _load_saved(pack_key, index - 1, total)
        follows = bool(last) and last.get("total") == total
        if not follows:
            LOGGER.warning("Easy H3 continuation: shot %d is neither in memory nor saved as .latent, "
                           "shot %d starts without continuation", index - 1, index)
    series = last["series"] if follows else time.strftime("%Y%m%d_%H%M%S")
    frames = context_frames
    if str(frames).strip() in ("自动", "auto"):
        frames = production_shot.get("continuation_frames") or CONTEXT_FALLBACK
    first, second = parse_context(frames)
    info = {"series": series, "index": index, "total": total, "overlap": 0, "pack_key": pack_key,
            "context_frames": MAX_TAIL_FRAMES, "transition": transition,
            "save": not hard_cut,
            "previous": {"index": index - 1, "video": _sibling_video(chosen) if chosen.lower().endswith(".latent") else chosen}
            if chosen and index > 1 else None,
            "title": production_shot.get("title") or production_shot.get("id") or ""}
    if follows and transition == "continue":
        # The shot is lengthened by the longer of the two guides, but by at least one
        # full H3 block (5 + 17 = 22 frames): with a 5-frame guide the start of a clip
        # shifts colour, so that start is generated as a lead-in and trimmed away.
        # Each guide ends at the seam, i.e. right before the frames that are kept.
        longest = max(first, second)
        overlap = min(max(longest, MIN_OVERLAP_FRAMES) if longest else 0, int(last["frames"]))
        fps = float(params.get("fps") or last.get("fps") or 24.0)
        base_frames = _frame_length(float(params.get("seconds") or 5.0), fps)
        params["seconds"] = _align_up(base_frames + overlap) / fps
        info.update(overlap=overlap, first=min(first, overlap), second=min(second, overlap), tail=last)
    LOGGER.info("Easy H3 continuation: shot %d/%d %s (%s)", index, total, transition,
                f"{info.get('first', 0)}+{info.get('second', 0)}" if info["overlap"] else "no overlap")
    return {**production_shot, "params": params, CONTINUATION_KEY: info}


def _is_h3_latent(latent: Any) -> bool:
    tensors = getattr(latent.get("samples"), "tensors", None) if isinstance(latent, dict) else None
    return bool(tensors) and len(tensors) == 2 and tensors[0].ndim == 5


def _latent_tail(latent: Any, audio_frames: int) -> dict:
    """Last 39 frames of video and >= 1 s of audio straight from the 2nd-pass latent."""
    samples = latent.get("samples") if isinstance(latent, dict) else None
    tensors = getattr(samples, "tensors", None)
    if not tensors or len(tensors) != 2 or tensors[0].ndim != 5 or tensors[0].shape[2] < 2 or tensors[1].ndim != 4:
        LOGGER.warning("Easy H3 continuation: latent input is not an H3 audio/video latent; using images instead")
        return {}
    video, audio = tensors
    frames = (int(video.shape[2]) - 2) // 5 * 17 + 5
    overhang = float(latent.get("_h3_audio_overhang", audio.shape[-1] - frames * FRAME_RESCALE))
    tokens = min(int(audio.shape[-1]), round(audio_frames * FRAME_RESCALE))
    return {
        "mode": "latent",
        # on the H3 grid the last 2 / 7 / 12 latent steps are exactly the last 5 / 22 / 39 frames
        "latent_video": video[:, :, -min(int(video.shape[2]), 12):].detach().to(device="cpu", copy=True).contiguous(),
        "latent_audio": audio[..., -tokens:].detach().to(device="cpu", copy=True).contiguous(),
        "overhang": overhang,
    }


class FeiHouEasyH3ContinueOut:
    """Drop the overlap, keep this shot's tail and hand it to Video Combine V2 for saving."""

    CATEGORY = "FeiHou Easy H3"
    FUNCTION = "join"
    RETURN_TYPES = ("IMAGE", "AUDIO", "LATENT", "FLOAT")
    RETURN_NAMES = ("images", "audio", "tail_latent", "fps")
    DESCRIPTION = (
        "视频接续（合并）：接在最终画面/音频之后，latent 接二采采样器输出。images/audio 每段都输出（已去掉开头重叠帧）；"
        "tail_latent 接 Video Combine V2 的 latent：保存与视频同名的 .latent（本段结尾，视频与音频在同一个文件），"
        "最后一段跑完后由 V2 把各段视频拼成完整视频。重跑或重启后，下一段从上一段的 .latent 接续。"
        "加载器选「硬切」时不保存 .latent。不是制作包分镜时 images/audio 原样输出，tail_latent 为空（不保存 .latent）。"
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "continue_from": (CONTINUE_FROM, {"default": "latent", "tooltip":
                    "latent：下一段二采直接用二采 latent 结尾（不经解码、不含修脸，推荐），只需连 latent；"
                    "image：用 images 画面重新编码（会带上修脸效果），只需连 images。"}),
            },
            "optional": {
                "latent": ("LATENT", {"tooltip": "接二采采样器的 output（LATENT）。latent 模式必接；images 没连时由它解码出画面。"}),
                "images": ("IMAGE", {"tooltip": "最终画面（例如修脸后）。image 模式必接；latent 模式可不接。"}),
                "audio": ("AUDIO", {"tooltip": "要保存的声音（生成音频或原声）。连了就用它；不连时从 latent 解码生成的声音。"}),
                "production_shot": (SHOT_TYPE, {"forceInput": True, "tooltip": "接制作包读取节点的 production_shot（与 Setup 同一个）。"}),
            }
        }

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    @staticmethod
    def _tail_latent(record: dict, info: dict) -> dict:
        """LATENT for Video Combine V2: the tail tensors plus what the next run needs to continue."""
        tensors, latent = {}, {}
        if record.get("mode") == "latent":
            latent["samples"] = record["latent_video"]
            tensors["latent_audio"] = record["latent_audio"]
        else:
            tensors["tail_images"] = (record["images"].clamp(0, 1) * 255).round().to(torch.uint8)
        audio = record.get("audio")
        if isinstance(audio, dict) and isinstance(audio.get("waveform"), torch.Tensor):
            tensors["tail_audio"] = audio["waveform"].float()
        metadata = {
            "feihou_schema": TAIL_SCHEMA, "mode": record.get("mode", "image"), "fps": record["fps"],
            "frames": int(record["frames"]), "audio_frames": int(record["audio_frames"]),
            "sample_rate": int((audio or {}).get("sample_rate", 44100)), "overhang": float(record.get("overhang", 0.0)),
            "series": info["series"], "index": int(info["index"]), "total": int(info["total"]),
            "pack_key": info.get("pack_key") or "", "transition": info.get("transition") or "",
        }
        latent[SAVE_KEY] = {
            "save_latent": bool(info.get("save", True)), "manifest": _manifest_path(info.get("pack_key")),
            "index": int(info["index"]), "total": int(info["total"]),
            "info": {"pack_key": info.get("pack_key") or ""},
            "previous": info.get("previous"),
            "tensors": tensors, "metadata": metadata,
        }
        return latent

    @staticmethod
    def join(continue_from="latent", latent=None, images=None, audio=None, production_shot=None):
        info = production_shot.get(CONTINUATION_KEY) if isinstance(production_shot, dict) else None
        if not isinstance(info, dict) and images is not None:
            pass  # not a continued production shot: plain pass-through
        elif continue_from == "latent" and not _is_h3_latent(latent):
            raise ValueError("接续合并选的是 latent：请连接二采采样器的 output 到 latent 接口（或切换到 image）。")
        if continue_from != "latent" and images is None:
            raise ValueError("接续合并选的是 image：请连接 images 接口（或切换到 latent）。")
        vae = (info or {}).get("vae")
        if images is None and vae and _is_h3_latent(latent):
            images = nodes.VAEDecode().decode(vae[0], latent)[0]
        # External audio wins; otherwise decode the generated soundtrack from the latent.
        if audio is None and vae and _is_h3_latent(latent):
            decoded = comfy_audio_nodes.VAEDecodeAudio.execute(vae[1], latent)
            audio = (decoded.result if hasattr(decoded, "result") else decoded)[0]
        if audio is None:
            raise ValueError("audio 没有连接，也无法从 latent 解码（需要连接制作包读取节点和 latent）；请连接 audio。")
        if images is None:
            raise ValueError("images 没有连接，也无法从 latent 解码（需要连接制作包读取节点和 latent）；请连接 images。")
        # Frame rate: what Setup actually used for this shot, else the shot's own, else 24.
        fps = float((info or {}).get("fps") or float(((production_shot or {}).get("params") or {}).get("fps") or 24.0))
        if not isinstance(info, dict):
            # Not a production-pack continuation shot: nothing for Video Combine V2 to save.
            return images, audio, None, fps
        images = images.detach().cpu()
        frames = int(images.shape[0])
        tail = min(int(info["context_frames"]), frames)
        audio_frames = min(max(AUDIO_MIN_FRAMES, tail), frames)
        record = {"series": info["series"], "index": info["index"], "total": info["total"], "fps": fps,
                  "pack_key": info.get("pack_key"), "frames": tail,
                  "images": images[-tail:].float().clone(), "audio_frames": audio_frames,
                  "audio": _audio_slice(audio, frames - audio_frames, audio_frames, fps), "mode": "image"}
        if continue_from == "latent":
            record.update(_latent_tail(latent, audio_frames))
        _LAST.clear()
        _LAST.update(record)

        overlap = min(int(info.get("overlap") or 0), frames - 1)
        kept = images[overlap:]
        segment_audio = _audio_slice(audio, overlap, int(kept.shape[0]), fps)
        if segment_audio is None:
            segment_audio = audio
        LOGGER.info("Easy H3 continuation: shot %d/%d done (%d frames, overlap %d)",
                    info["index"], info["total"], int(kept.shape[0]), overlap)
        return kept, segment_audio, FeiHouEasyH3ContinueOut._tail_latent(record, info), fps
