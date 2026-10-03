"""Split edition of the Easy H3 main node.

* FeiHou Easy H3 Media: the 15-slot media gallery (9 images, 3 videos,
  3 audio clips) as its own node.  The gallery lives in the browser; the files
  are uploaded to ComfyUI's input folder exactly like on the main node.
* FeiHou Easy H3 Setup: every other input/output of the main node, plus an
  ``h3_media`` socket for the Media node.

The frontend sends the Media node's files to the Setup node as the same hidden
media_* transport fields the main node uses, so Setup executes the unchanged
main-node code path.  The original main node is not modified.
"""

from __future__ import annotations

from .nodes import FeiHouEasyH3

MEDIA_TYPE = "FEIHOU_H3_MEDIA"


class FeiHouEasyH3Media:
    CATEGORY = "FeiHou Easy H3"
    FUNCTION = "media"
    RETURN_TYPES = (MEDIA_TYPE,)
    RETURN_NAMES = ("h3_media",)
    DESCRIPTION = "Easy H3 媒体载入：9 图 / 3 视频 / 3 音频。接到 FeiHou Easy H3 Setup 的 h3_media。"

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}}

    @staticmethod
    def media():
        # The media themselves travel to the Setup node with the prompt.
        return ({"source": "feihou_easy_h3_media"},)


class FeiHouEasyH3Setup(FeiHouEasyH3):
    DESCRIPTION = "Easy H3 生成设置：与 ComfyUI-FeiHou-Easy-H3 主节点相同的输入输出，媒体由 FeiHou Easy H3 Media 节点提供。"

    @classmethod
    def INPUT_TYPES(cls):
        types = FeiHouEasyH3.INPUT_TYPES()
        required = {"h3_media": (MEDIA_TYPE, {"tooltip": "接 FeiHou Easy H3 Media 节点。"})}
        required.update(types["required"])
        types["required"] = required
        return types

    @classmethod
    def generate(cls, h3_media=None, **kwargs):
        return super().generate(**kwargs)
