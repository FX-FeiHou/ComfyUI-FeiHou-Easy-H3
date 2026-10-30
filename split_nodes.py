"""Split edition of the Easy H3 main node.

* FeiHou Easy H3 Media: the 15-slot media gallery (9 images, 3 videos,
  3 audio clips) as its own node.  The gallery lives in the browser; the files
  are uploaded to ComfyUI's input folder exactly like on the main node.
* FeiHou Easy H3 Setup: every other input/output of the main node, plus an
  ``h3_media`` socket for the Media node.

* FeiHou Easy H3 Media Bridge: ordinary IMAGE / VIDEO / AUDIO links (for
  example VHS videos or depth preprocessors) instead of the gallery.  It plugs
  into the same ``h3_media`` socket; Setup takes either the gallery or the
  bridge.

The frontend sends the Media node's files to the Setup node as the same hidden
media_* transport fields the main node uses, so Setup executes the unchanged
main-node code path.  Bridge media are written into those fields at execution
time, on fixed slots (images 1-9, videos 10-12, audio 13-15) that the
frontend uses for @ references.  The original main node is not modified.
"""

from __future__ import annotations

from collections.abc import Mapping

import torch

from .nodes import MAX_AUDIOS, MAX_IMAGES, MAX_MEDIA, MAX_VIDEOS, FeiHouEasyH3
from .production_continuation import apply_guide, prepare_setup

MEDIA_TYPE = "FEIHOU_H3_MEDIA"
BRIDGE_SOURCE = "feihou_easy_h3_media_bridge"
# IMAGE frame sequences on the bridge carry no frame rate: H3 runs at 24 fps.
IMAGE_SEQUENCE_FPS = 24.0


MEDIA_SOURCE = "feihou_easy_h3_media"


def _plain_audio(audio):
    """Copy an AUDIO payload (VHS returns a lazy mapping) into a plain dict."""
    if not isinstance(audio, Mapping) or "waveform" not in audio:
        return None
    return {"waveform": audio["waveform"], "sample_rate": int(audio.get("sample_rate") or 44100)}


def _linked_input_types(images: int, videos: int, audios: int) -> dict:
    """Optional IMAGE / VIDEO / AUDIO sockets: image_N, video_N (+ video_N_audio), audio_N."""
    optional = {}
    for index in range(1, images + 1):
        optional[f"image_{index}"] = ("IMAGE", {"tooltip": f"参考图片 {index}，分镜 md 里就是 <Picture {index}>。"})
    for index in range(1, videos + 1):
        optional[f"video_{index}"] = ("IMAGE,VIDEO", {"tooltip":
            f"参考视频 {index}：VIDEO，或 IMAGE 帧序列（按 24fps）。"})
        optional[f"video_{index}_audio"] = ("AUDIO", {"tooltip":
            f"参考视频 {index} 的同步音轨（可选）；接了就替换视频自带的声音。"})
    for index in range(1, audios + 1):
        optional[f"audio_{index}"] = ("AUDIO", {"tooltip": f"独立参考音频 {index}。"})
    return optional


def _linked_items(kwargs: dict, images: int, videos: int, audios: int) -> list:
    """(slot, media_type, value) for every connected socket; slots 1-9 images, 10-12 videos, 13-15 audio."""
    items = []
    for index in range(1, images + 1):
        image = kwargs.get(f"image_{index}")
        if isinstance(image, torch.Tensor) and image.ndim == 4 and image.shape[0] > 0:
            items.append((index, "image", image))
    for index in range(1, videos + 1):
        video = kwargs.get(f"video_{index}")
        soundtrack = _plain_audio(kwargs.get(f"video_{index}_audio"))
        if video is None:
            continue
        if isinstance(video, torch.Tensor):
            if video.ndim != 4 or video.shape[0] < 1:
                raise ValueError(f"video_{index} 不是有效的 IMAGE 帧序列")
            video = {"images": video, "audio": soundtrack, "fps": IMAGE_SEQUENCE_FPS}
        elif hasattr(video, "get_components") and soundtrack is not None:
            components = video.get_components()
            video = {"images": components.images, "audio": soundtrack,
                     "fps": float(components.frame_rate or IMAGE_SEQUENCE_FPS)}
        items.append((images + index, "video", video))
    for index in range(1, audios + 1):
        audio = _plain_audio(kwargs.get(f"audio_{index}"))
        if audio is not None:
            items.append((images + videos + index, "audio", audio))
    return items


def _linked_keys(images: int, videos: int, audios: int) -> list:
    return list(_linked_input_types(images, videos, audios))


class FeiHouEasyH3Media:
    CATEGORY = "FeiHou Easy H3"
    FUNCTION = "media"
    RETURN_TYPES = (MEDIA_TYPE,)
    RETURN_NAMES = ("h3_media",)
    DESCRIPTION = (
        "Easy H3 媒体载入：9 图 / 3 视频 / 3 音频。接到 FeiHou Easy H3 Setup 的 h3_media。"
        "每类卡片左边可以外接连线（加载图片 / 视频 / 音频等），按接入顺序编号 1、2、3…；"
        "外接的优先，覆盖同编号卡片里上传的文件。"
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}, "optional": _linked_input_types(MAX_IMAGES, MAX_VIDEOS, MAX_AUDIOS)}

    @staticmethod
    def media(**kwargs):
        # Gallery files travel to Setup with the prompt; linked media override their cards there.
        return ({"source": MEDIA_SOURCE, "items": _linked_items(kwargs, MAX_IMAGES, MAX_VIDEOS, MAX_AUDIOS)},)


class FeiHouEasyH3MediaBridge:
    CATEGORY = "FeiHou Easy H3"
    FUNCTION = "pack"
    RETURN_TYPES = (MEDIA_TYPE,)
    RETURN_NAMES = ("h3_media",)
    DESCRIPTION = (
        "Easy H3 媒体桥接：用普通连线接入 9 图 / 3 视频 / 3 音频，接到 FeiHou Easy H3 Setup 的 h3_media（替代 Media 节点）。"
        "video 可接 VIDEO 或 IMAGE 帧序列（如 VHS 加载视频，帧序列按 24fps）；video_N_audio 是该视频的同步音轨。"
        "编号按卡位固定：image_3 就是 <Picture 3>，前面空着也不顺延。帧率、尺寸在 VHS 里设，或改用 Media 节点的「媒体设置」。"
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {},
            "optional": _linked_input_types(MAX_IMAGES, MAX_VIDEOS, MAX_AUDIOS),
        }

    @staticmethod
    def pack(**kwargs):
        # Old workflows still send the removed video_fps / video_size widgets.
        kwargs.pop("video_fps", None)
        kwargs.pop("video_size", None)
        return ({"source": BRIDGE_SOURCE, "items": _linked_items(kwargs, MAX_IMAGES, MAX_VIDEOS, MAX_AUDIOS)},)


def _bridge_media(kwargs: dict, h3_media) -> dict:
    """Write Media Bridge items into the hidden media_* fields on their fixed slots.

    The Media node's linked inputs only replace the cards they are numbered
    for; the rest of its gallery stays.
    """
    if isinstance(h3_media, Mapping) and h3_media.get("source") == MEDIA_SOURCE and h3_media.get("items"):
        kwargs = dict(kwargs)
        for slot, media_type, value in h3_media["items"]:
            kwargs[f"media_{slot}"] = value
            kwargs[f"media_type_{slot}"] = media_type
            kwargs[f"media_trim_{slot}"] = ""
            kwargs.pop(f"media_opts_{slot}", None)
        return kwargs
    if not isinstance(h3_media, Mapping) or h3_media.get("source") != BRIDGE_SOURCE:
        return kwargs
    kwargs = dict(kwargs)
    for index in range(1, MAX_MEDIA + 1):
        for prefix in ("media_", "media_type_", "media_trim_", "media_opts_"):
            kwargs.pop(f"{prefix}{index}", None)
    for slot, media_type, value in h3_media.get("items") or ():
        kwargs[f"media_{slot}"] = value
        kwargs[f"media_type_{slot}"] = media_type
        kwargs[f"media_trim_{slot}"] = ""
    return kwargs


class FeiHouEasyH3Setup(FeiHouEasyH3):
    DESCRIPTION = "Easy H3 生成设置：与主节点相同，媒体由 FeiHou Easy H3 Media 提供。"

    @classmethod
    def INPUT_TYPES(cls):
        types = FeiHouEasyH3.INPUT_TYPES()
        required = {"h3_media": (MEDIA_TYPE, {"tooltip": "接 FeiHou Easy H3 Media 节点，或 FeiHou Easy H3 Media Bridge（外接图片/视频/音频）。"})}
        required.update(types["required"])
        types["required"] = required
        return types

    @classmethod
    def generate(cls, h3_media=None, **kwargs):
        # A production shot coming through "Continue In" carries the previous
        # shot's tail; it is anchored at frame 0 as a native H3 guide.
        kwargs = _bridge_media(kwargs, h3_media)
        kwargs, continuation = prepare_setup(kwargs)
        model, second_model, context = super().generate(**kwargs)
        return model, second_model, apply_guide(context, continuation)
