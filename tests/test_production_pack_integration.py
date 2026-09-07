"""Run the actual generate() preamble with model-free stubs, not a copy of it."""
import ast
import logging
from pathlib import Path
import subprocess
import types
import unittest

SOURCE = Path(__file__).parents[1] / "nodes.py"


def main_class(text):
    return next(n for n in ast.parse(text).body if isinstance(n, ast.ClassDef) and n.name == "FeiHouEasyH3")


class IntegrationTests(unittest.TestCase):
    def test_pack_overrides_before_loading_and_manual_path_is_unchanged(self):
        cls = main_class(SOURCE.read_text(encoding="utf-8"))
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "generate")
        stop = next(i for i, n in enumerate(method.body) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "items" for t in n.targets))
        method.decorator_list = []
        method.body = method.body[:stop + 1] + ast.parse("return locals()").body
        scope = {"MiniMaxH3Bundle": types.SimpleNamespace, "MAX_MEDIA": 15,
                 "MODE_REFERENCE": "reference", "KEYFRAME_FIRST": "first", "KEYFRAME_LAST": "last",
                 "REFERENCE_MENTION_INDEX": "index", "_as_bool": bool, "logging": logging,
                 "_canvas_dimensions": lambda *args: args[-2:]}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), str(SOURCE), "exec"), scope)
        args = dict(cls=types.SimpleNamespace(_collect_media=lambda kwargs: kwargs.copy()),
                    h3_bundle=types.SimpleNamespace(), mode="image", prompt="manual", resolution="720P",
                    aspect_ratio="9:16", width=720, height=1280, audio_duration_auto=False, seconds=8,
                    advanced=False, fps=25, keyframe_role="first", ref_image_size="match",
                    reference_mention_mode="filename", prompt_optimizer_enabled=True,
                    media_1="old.png", media_12="unused.wav", media_type_12="audio")
        manual = scope["generate"](**args)
        self.assertEqual(manual["prompt"], "manual")
        self.assertEqual(manual["items"]["media_12"], "unused.wav")
        data = {"schema": 1, "id": "H3-004", "index": 4, "total": 36, "range": "00:20:900–00:25:700",
                "prompt": "<Picture 1> <Audio 1>", "params": {"seconds": 4.8, "aspect_ratio": "16:9"},
                "media": [{"subfolder": "pack", "filename": "P2.png", "media_type": "image"},
                          {"subfolder": "pack", "filename": "song.mp3", "media_type": "audio", "audio_trim": "00:20:900–00:25:700"}]}
        actual = scope["generate"](**args, production_shot=data)
        self.assertEqual(actual["prompt"], data["prompt"])
        self.assertEqual(actual["mode"], "reference")
        self.assertEqual(actual["seconds"], 4.8)
        self.assertTrue(actual["audio_duration_auto"])
        self.assertFalse(actual["prompt_optimizer_enabled"])
        self.assertEqual(actual["fps"], 25)
        self.assertEqual(actual["resolution"], "720P")
        self.assertEqual(actual["aspect_ratio"], "16:9")
        self.assertEqual(actual["items"]["media_1"], "pack/P2.png")
        self.assertEqual(actual["items"]["media_trim_2"], data["range"])
        self.assertNotIn("media_12", actual["items"])

    def test_existing_widget_order_and_output_contract_unchanged(self):
        before = subprocess.check_output(["git", "show", "HEAD:nodes.py"], cwd=SOURCE.parent).decode("utf-8")
        after = SOURCE.read_text(encoding="utf-8")
        def contract(text):
            cls = main_class(text)
            func = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "INPUT_TYPES")
            returned = next(n.value for n in func.body if isinstance(n, ast.Return))
            required = next(v for k, v in zip(returned.keys, returned.values) if isinstance(k, ast.Constant) and k.value == "required")
            names = [k.value for k in required.keys]
            output = [ast.dump(n) for n in cls.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in {"RETURN_TYPES", "RETURN_NAMES"} for t in n.targets)]
            return names, output
        self.assertEqual(contract(before), contract(after))


if __name__ == "__main__":
    unittest.main()
