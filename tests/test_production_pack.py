"""CPU-only tests; does not import ComfyUI, PyTorch or model files."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
import zipfile

spec = importlib.util.spec_from_file_location("production_pack", Path(__file__).parents[1] / "production_pack.py")
pack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pack)


class PackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.package = self.root / "source"
        self.package.mkdir()
        for filename in ["P1b.png", "P2.jpg", "P99.png", "song.mp3"]:
            (self.package / filename).write_bytes(filename.encode())
        (self.package / "Test_Shotlist.html").write_text("<html>test</html>")
        self.previous = sys.modules.get("folder_paths")
        sys.modules["folder_paths"] = types.SimpleNamespace(
            get_user_directory=lambda: str(self.root / "user"),
            get_input_directory=lambda: str(self.root / "input"))
        self.shot = {"id": "H3-001", "range": "00:20:940–00:25:700", "seconds": 4.8,
                     "refs": ["P1B", "P2"], "aspect_ratio": "16:9",
                     "prompt_zh": "<Picture 1> <Picture 2> <Audio 1>",
                     "prompt_en": "<Picture 1> and <Picture 2> with <Audio 1>"}

    def tearDown(self):
        if self.previous is None:
            sys.modules.pop("folder_paths", None)
        else:
            sys.modules["folder_paths"] = self.previous
        self.temp.cleanup()

    def prepare(self, shots=None):
        info = pack.inspect_pack(str(self.package))
        return pack.commit_shots(info["package_id"], shots or [self.shot])["package_id"]

    def test_exact_mapping_and_only_referenced_files(self):
        package_id = self.prepare()
        first = pack.load_shot(str(self.package), package_id, 1, "zh")
        self.assertEqual(first["range"], "00:20:900–00:25:700")
        self.assertEqual(first["params"]["seconds"], 4.8)
        self.assertNotIn("fps", first["params"])
        self.assertNotIn("resolution", first["params"])
        self.assertEqual([m["filename"] for m in first["media"]], ["P1b.png", "P2.jpg", "song.mp3"])
        self.assertEqual(len(list((self.root / "input").rglob("*.*"))), 3)

    def test_queue_indices_are_independent_and_bounded(self):
        shots = [{**self.shot, "id": f"H3-{i:03}", "refs": ["P2", "P1b"] if i % 2 == 0 else ["P1b", "P2"]} for i in range(1, 37)]
        package_id = self.prepare(shots)
        outputs = [pack.load_shot(str(self.package), package_id, i, "en") for i in range(1, 37)]
        self.assertEqual([s["index"] for s in outputs], list(range(1, 37)))
        self.assertEqual(outputs[1]["media"][0]["filename"], "P2.jpg")
        self.assertEqual(pack.load_shot(str(self.package), package_id, 1, "en"), outputs[0])
        with self.assertRaisesRegex(ValueError, "outside"):
            pack.load_shot(str(self.package), package_id, 37, "en")

    def test_missing_ambiguous_limits_and_conflicting_times(self):
        info = pack.inspect_pack(str(self.package))
        for bad in [{"refs": ["P404"]}, {"refs": ["P1b"] * 10}, {"seconds": 5},
                    {"range": "00:25–00:20"}, {"prompt_en": "<Picture 3>"}, {"fps": float("nan")}]:
            with self.assertRaises(ValueError):
                pack.commit_shots(info["package_id"], [{**self.shot, **bad}])
        (self.package / "P1b.jpg").write_bytes(b"duplicate")
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.prepare()

    def test_changed_source_html_and_language_require_refresh(self):
        package_id = self.prepare()
        with self.assertRaisesRegex(ValueError, "Source changed"):
            pack.load_shot("other", package_id, 1, "zh")
        with self.assertRaisesRegex(ValueError, "language"):
            pack.load_shot(str(self.package), package_id, 1, "fr")
        (self.package / "Test_Shotlist.html").write_text("changed")
        with self.assertRaisesRegex(ValueError, "HTML changed"):
            pack.load_shot(str(self.package), package_id, 1, "zh")

    def test_path_escape_and_zip_data_only(self):
        for name in ["../escape.png", "/escape.png", "C:/escape.png", "P1.png:stream", "NUL.png", "dir./P1.png"]:
            with self.assertRaises(ValueError):
                pack.contained(self.package, name)
        archive = self.root / "pack.zip"
        with zipfile.ZipFile(archive, "w") as output:
            for path in self.package.iterdir():
                output.write(path, f"制作包/{path.name}")
            output.writestr("制作包/danger.py", "raise Exception('must not run')")
        info = pack.inspect_pack(str(archive))
        package_id = pack.commit_shots(info["package_id"], [self.shot])["package_id"]
        self.assertEqual(pack.load_shot(str(archive), package_id, 1, "en")["total"], 1)
        self.assertFalse(list(pack.cache_root().rglob("*.py")))
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("../escape.png", b"bad")
        with self.assertRaises(ValueError):
            pack.inspect_pack(str(archive))

    def test_prepared_manifest_is_immutable(self):
        info = pack.inspect_pack(str(self.package))
        first = pack.commit_shots(info["package_id"], [self.shot])
        second = pack.commit_shots(info["package_id"], [{**self.shot, "id": "H3-002"}])
        self.assertNotEqual(first["package_id"], second["package_id"])
        self.assertEqual(pack.read_pack(first["package_id"])["shots"][0]["id"], "H3-001")

    def test_time_rounding(self):
        for source, expected in [("00:20:940", 20.9), ("00:20:950", 21), ("80", 80), ("00:61:000", 61)]:
            self.assertEqual(pack.time_seconds(source), expected)


if __name__ == "__main__":
    unittest.main()
