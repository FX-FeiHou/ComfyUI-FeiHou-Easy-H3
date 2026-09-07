"""Validate a real package and browser-extracted shots without loading models."""
import json
from pathlib import Path
import sys
import tempfile
import types
from test_production_pack import pack

with tempfile.TemporaryDirectory(prefix="easy_h3_pack_test_") as temp:
    root = Path(temp)
    sys.modules["folder_paths"] = types.SimpleNamespace(
        get_user_directory=lambda: str(root / "user"),
        get_input_directory=lambda: str(root / "input"))
    info = pack.inspect_pack(sys.argv[1])
    shots = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    ready = pack.commit_shots(info["package_id"], shots)
    assert ready["total"] == 36
    manifest = pack.read_pack(ready["package_id"])
    for index in (1, 4, 36):
        shot = pack.load_shot(sys.argv[1], ready["package_id"], index, "zh")
        assert len(shot["media"]) == len(shot["refs"]) + 1
        assert shot["params"]["audio_duration_auto"] is True
        print(json.dumps({key: shot[key] for key in ("id", "range", "refs", "params")}, ensure_ascii=False))
    assets = set(manifest["files"])
    referenced = {name for s in manifest["shots"] for name in s["images"]}
    print(f"PASS: {len(shots)} shots; {len(referenced)} distinct referenced images; {len(assets)} inventory files; no model loading")
