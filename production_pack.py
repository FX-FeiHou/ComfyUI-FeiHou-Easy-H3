"""Local production-pack import. HTML is rendered only in an isolated browser frame.

No package Python/JS is executed by the server. Only a validated shot manifest
and referenced media cross the loader socket; model/sampler settings stay local.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import uuid
import zipfile

SHOT_TYPE = "FEIHOU_H3_PRODUCTION_SHOT"
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
AUDIO_EXT = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".avi"}
MAX_FILES = 5000
MAX_BYTES = 4 * 1024**3
MAX_HTML = 16 * 1024**2


def cache_root():
    import folder_paths
    path = Path(folder_paths.get_user_directory()) / "feihou_h3_production_packs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def contained(root, relative):
    """Reject absolute paths, links outside the pack, NTFS ADS and traversal."""
    name = str(relative).replace("\\", "/")
    parts = PurePosixPath(name)
    if not name or parts.is_absolute() or ":" in name or ".." in parts.parts:
        raise ValueError(f"Unsafe package path: {relative}")
    for part in parts.parts:
        if part.endswith((".", " ")) or re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", part.split(".")[0], re.I):
            raise ValueError(f"Unsafe Windows package filename: {relative}")
    root = Path(root).resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Package path escapes its folder: {relative}")
    return path


def atomic_json(path, data):
    temp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def unpack_zip(archive, destination):
    """Extract data only, with bounded size and no symlinks or path escapes."""
    destination = Path(destination)
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if len(entries) > MAX_FILES or sum(i.file_size for i in entries) > MAX_BYTES:
            raise ValueError("ZIP exceeds the 5000-file / 4 GiB uncompressed limit")
        selected, seen = [], set()
        for item in entries:
            path = contained(destination, item.filename)
            key = str(path).casefold()
            if key in seen or stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError("ZIP contains duplicate paths or symbolic links")
            seen.add(key)
            if item.flag_bits & 1:
                raise ValueError("Encrypted ZIP files are not supported")
            if not item.is_dir() and path.suffix.lower() in IMAGE_EXT | AUDIO_EXT | VIDEO_EXT | {".html", ".htm"}:
                selected.append((item, path))
        for item, path in selected:
            path.parent.mkdir(parents=True, exist_ok=True)
            with source.open(item) as reader, path.open("xb") as writer:
                shutil.copyfileobj(reader, writer, 1024 * 1024)


def inventory(root):
    files = []
    for base, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not d.startswith(".") and not (Path(base) / d).is_symlink()]
        for name in names:
            path = Path(base) / name
            if path.suffix.lower() not in IMAGE_EXT | AUDIO_EXT | VIDEO_EXT | {".html", ".htm"}:
                continue
            rel = path.relative_to(root).as_posix()
            contained(root, rel)
            files.append(rel)
            if len(files) > MAX_FILES:
                raise ValueError("Package has too many media/HTML files (limit 5000)")
    return sorted(files)


def inspect_pack(source, shotlist_file=""):
    source = str(source).strip().strip('"')
    if not source:
        raise ValueError("Enter a production-pack folder or ZIP path first")
    root = Path(source).expanduser().resolve()
    if root.is_file() and root.suffix.lower() == ".zip":
        dest = Path(tempfile.mkdtemp(prefix="zip_", dir=cache_root()))
        try:
            unpack_zip(root, dest)
        except Exception:
            # Only this newly-created, concrete extraction directory is removed.
            shutil.rmtree(dest)
            raise
        root = dest
    if not root.is_dir():
        raise ValueError("Package folder / ZIP does not exist on the ComfyUI machine")
    files = inventory(root)
    candidates = [f for f in files if Path(f).suffix.lower() in {".html", ".htm"} and "shotlist" in Path(f).stem.lower()]
    if shotlist_file:
        selected = contained(root, shotlist_file).relative_to(root).as_posix()
        if selected not in files or Path(selected).suffix.lower() not in {".html", ".htm"}:
            raise ValueError("Shotlist HTML is not in this package")
    elif len(candidates) == 1:
        selected = candidates[0]
    else:
        raise ValueError(f"Expected one Shotlist HTML; found {len(candidates)}. Keep one Shotlist in the selected package folder: {candidates}")
    page = contained(root, selected)
    if page.stat().st_size > MAX_HTML:
        raise ValueError("Shotlist HTML exceeds 16 MiB")
    html = page.read_text(encoding="utf-8-sig")
    package_id = uuid.uuid4().hex
    data = {"source": source, "root": str(root), "html_file": selected,
            "html_sha256": hashlib.sha256(html.encode()).hexdigest(), "files": files}
    atomic_json(cache_root() / f"{package_id}.json", data)
    return {"package_id": package_id, "html": html, "html_file": selected,
            "audio_files": [f for f in files if Path(f).suffix.lower() in AUDIO_EXT]}


def read_pack(package_id):
    if not re.fullmatch(r"[a-f0-9]{32}", str(package_id)):
        raise ValueError("Load / refresh the production pack first")
    path = cache_root() / f"{package_id}.json"
    if not path.is_file():
        raise ValueError("Production-pack cache is missing; load / refresh it on this machine")
    return json.loads(path.read_text(encoding="utf-8"))


def time_seconds(value):
    value = str(value).strip().replace("：", ":")
    if not re.fullmatch(r"\d+(?:[.:]\d+){0,2}", value):
        raise ValueError(f"Invalid shot time: {value}")
    parts = value.split(":")
    if len(parts) == 3:
        seconds = int(parts[0]) * 60 + int(parts[1]) + int(parts[2]) / 1000
    elif len(parts) == 2:
        seconds = int(parts[0]) * 60 + float(parts[1])
    else:
        seconds = float(parts[0])
    return math.floor(seconds * 10 + 0.50000001) / 10


def time_text(seconds):
    ticks = round(seconds * 10)
    return f"{ticks // 600:02}:{ticks // 10 % 60:02}:{ticks % 10 * 100:03}"


def validate_shots(data, shots, audio_file=""):
    if not isinstance(shots, list) or not 1 <= len(shots) <= 1000:
        raise ValueError("No supported shots found, or more than 1000 shots")
    files = data["files"]
    audio_files = [f for f in files if Path(f).suffix.lower() in AUDIO_EXT]
    assets = {}
    for filename in files:
        if Path(filename).suffix.lower() in IMAGE_EXT:
            assets.setdefault(Path(filename).stem.casefold(), []).append(filename)
    result = []
    ids = set()
    for index, raw in enumerate(shots, 1):
        shot_id = str(raw.get("id", ""))
        if not shot_id or shot_id in ids:
            raise ValueError(f"Shot {index}: missing / duplicate ID")
        ids.add(shot_id)
        digital_human = raw.get("package_mode") == "digital_human"
        selected_audio = str((raw.get("voice_reference") or "") if digital_human else raw.get("audio_file") or audio_file or "").strip()
        audio_role = "voice reference" if digital_human else "soundtrack"
        if selected_audio:
            matches = [f for f in audio_files if f.casefold() == selected_audio.replace("\\", "/").casefold()]
            if not matches:
                matches = [f for f in audio_files if Path(f).name.casefold() == Path(selected_audio.replace("\\", "/")).name.casefold()]
            if len(matches) != 1:
                raise ValueError(f"{shot_id}: {audio_role} is missing or ambiguous: {selected_audio}")
            selected_audio = matches[0]
        elif not digital_human and len(audio_files) == 1:
            selected_audio = audio_files[0]
        elif not digital_human and len(audio_files) > 1:
            raise ValueError(f"{shot_id}: multiple audio files; declare master_audio or audio_file in the Shotlist")
        if selected_audio and not contained(data["root"], selected_audio).is_file():
            raise ValueError(f"{shot_id}: missing {audio_role}: {selected_audio}")
        times = re.split(r"\s*[-–—~～至]\s*", str(raw.get("range", "")))
        if digital_human:
            if raw.get("range") != "00:00:000–00:00:000":
                raise ValueError(f"{shot_id}: digital-human range must be 00:00:000–00:00:000")
            if raw.get("seconds") is None:
                raise ValueError(f"{shot_id}: digital-human shots require seconds")
            start, end = 0, time_seconds(raw["seconds"])
        elif len(times) != 2 and raw.get("seconds") is not None:
            start, end = 0, time_seconds(raw["seconds"])
        elif len(times) != 2:
            raise ValueError(f"{shot_id}: missing or invalid range")
        else:
            start, end = map(time_seconds, times)
        duration = round(end - start, 1)
        if not 0.2 <= duration <= 30:
            raise ValueError(f"{shot_id}: duration {duration}s is outside Easy H3's 0.2–30s range")
        declared = raw.get("seconds")
        if declared is not None and (not math.isfinite(float(declared)) or abs(float(declared) - duration) > 0.11):
            raise ValueError(f"{shot_id}: shot duration disagrees with its range")
        refs = raw.get("refs", [])
        if not isinstance(refs, list) or not 1 <= len(refs) <= 9:
            raise ValueError(f"{shot_id}: expected 1–9 ordered image references, got {len(refs)}")
        images = []
        for ref in refs:
            matches = assets.get(str(ref).strip().casefold(), [])
            if len(matches) != 1:
                raise ValueError(f"{shot_id}: asset {ref} is missing or ambiguous: {matches}")
            if not contained(data["root"], matches[0]).is_file():
                raise ValueError(f"{shot_id}: missing image: {matches[0]}")
            images.append(matches[0])
        video_refs = raw.get("video_refs", [])
        if not isinstance(video_refs, list) or len(video_refs) > 3:
            raise ValueError(f"{shot_id}: expected at most 3 ordered video references")
        videos = []
        for ref in video_refs:
            matches = [f for f in files if Path(f).suffix.lower() in VIDEO_EXT
                       and (f.casefold() == str(ref).casefold() or Path(f).stem.casefold() == str(ref).casefold())]
            if len(matches) != 1 or not contained(data["root"], matches[0]).is_file():
                raise ValueError(f"{shot_id}: video {ref} is missing or ambiguous: {matches}")
            videos.append(matches[0])
        prompts = {lang: str(raw.get(f"prompt_{lang}", "")).strip() for lang in ("zh", "en")}
        if not any(prompts.values()) or any(len(p) > 100000 for p in prompts.values()):
            raise ValueError(f"{shot_id}: missing or oversized prompt")
        for prompt in prompts.values():
            used = [int(n) for n in re.findall(r"<(?:Picture|Image)\s+(\d+)>", prompt, re.I)]
            if any(n < 1 or n > len(images) for n in used):
                raise ValueError(f"{shot_id}: prompt references an unavailable Picture slot")
            if any(int(n) < 1 or int(n) > len(videos) for n in re.findall(r"<Video\s+(\d+)>", prompt, re.I)):
                raise ValueError(f"{shot_id}: prompt references an unavailable Video slot")
            if re.search(r"<Audio\s+(?!1>)[0-9]+>", prompt, re.I):
                raise ValueError(f"{shot_id}: only the selected soundtrack (Audio 1) is supported")
            if not selected_audio and re.search(r"<Audio\s+\d+>", prompt, re.I):
                raise ValueError(f"{shot_id}: prompt references Audio but no reference audio exists")
        params = {"mode": "reference", "seconds": duration, "audio_duration_auto": False if digital_human else bool(selected_audio),
                  "reference_mention_mode": "index", "prompt_optimizer_enabled": False}
        aspect = str(raw.get("aspect_ratio", "")).strip()
        if aspect:
            if aspect not in {"16:9", "9:16", "1:1", "2:3", "3:2", "4:3", "3:4", "21:9"}:
                raise ValueError(f"{shot_id}: unsupported aspect ratio {aspect}")
            params["aspect_ratio"] = aspect
        if raw.get("fps") is not None:
            fps = float(raw["fps"])
            if not math.isfinite(fps) or not 1 <= fps <= 120:
                raise ValueError(f"{shot_id}: invalid FPS")
            params["fps"] = fps
        if raw.get("resolution"):
            resolution = str(raw["resolution"]).upper()
            if resolution not in {f"{p}P" for p in [360, 416, 480, 540, 640, 720, 768, 832, 928, 1024, 1080]}:
                raise ValueError(f"{shot_id}: unsupported resolution")
            params["resolution"] = resolution
        result.append({"id": shot_id, "title": str(raw.get("title", shot_id)), "params": params,
                       "prompts": prompts, "refs": list(refs), "images": images, "videos": videos, "audio": selected_audio,
                       "range": "00:00:000–00:00:000" if digital_human else f"{time_text(start)}–{time_text(end)}"})
    return result


def commit_shots(package_id, shots, audio_file="", diagnose=False, prompt_language=""):
    data = read_pack(package_id)
    report = []
    errors = []
    if diagnose:
        if not isinstance(shots, list) or not 1 <= len(shots) <= 1000:
            raise ValueError("No supported shots found, or more than 1000 shots")
        ids = set()
        for index, shot in enumerate(shots, 1):
            try:
                validated = validate_shots(data, [shot], audio_file)[0]
                if prompt_language and not validated["prompts"].get(prompt_language):
                    raise ValueError(f"{validated['id']}: missing {prompt_language} prompt")
                if validated["id"] in ids:
                    raise ValueError(f"Duplicate shot ID: {validated['id']}")
                ids.add(validated["id"])
                report.append(f"OK {index}: {validated['id']} | {validated['params']['seconds']}s | images={len(validated['images'])}, videos={len(validated['videos'])}, audio={int(bool(validated['audio']))}")
                report.extend(str(w) for w in shot.get("import_warnings", []))
            except (ValueError, TypeError, OSError) as exc:
                errors.append(f"Shot {index}: {exc}")
        if errors:
            return {"package_id": "", "total": len(shots), "errors": errors, "report": report}
    data["shots"] = validate_shots(data, shots, audio_file)
    # A prepared manifest is an immutable snapshot, including already queued work.
    ready_id = uuid.uuid4().hex
    atomic_json(cache_root() / f"{ready_id}.json", data)
    return {"package_id": ready_id, "total": len(data["shots"]), "errors": [], "report": report,
            "html_file": data["html_file"], "audio_file": data["shots"][0]["audio"]}


def materialize(root, relative, input_root):
    source = contained(root, relative)
    info = source.stat()
    identity = f"{source}|{info.st_size}|{info.st_mtime_ns}"
    subfolder = f"feihou_h3_pack_media/{hashlib.sha256(identity.encode()).hexdigest()[:24]}"
    target = Path(input_root) / subfolder / source.name
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            shutil.copyfile(source, temp)
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
    return {"filename": target.name, "subfolder": subfolder, "storage": "input"}


def load_shot(source, package_id, shot_index, prompt_language, shotlist_file="", audio_file=""):
    import folder_paths
    data = read_pack(package_id)
    if str(source).strip().strip('"') != data["source"]:
        raise ValueError("Source changed: load / refresh the production pack before queuing")
    if shotlist_file and contained(data["root"], shotlist_file) != contained(data["root"], data["html_file"]):
        raise ValueError("Shotlist selection changed: load / refresh the production pack")
    page = contained(data["root"], data["html_file"])
    if hashlib.sha256(page.read_text(encoding="utf-8-sig").encode()).hexdigest() != data["html_sha256"]:
        raise ValueError("Shotlist HTML changed: load / refresh the production pack before queuing")
    shots = data.get("shots", [])
    index = int(shot_index)
    if index < 1 or index > len(shots):
        raise ValueError(f"Shot {index} is outside 1–{len(shots)}; batch count must not exceed the remaining shots")
    shot = shots[index - 1]
    if audio_file and contained(data["root"], audio_file) != contained(data["root"], shot["audio"]):
        raise ValueError("Soundtrack selection changed: load / refresh the production pack")
    prompt = shot["prompts"].get(prompt_language)
    if not prompt:
        raise ValueError(f"{shot['id']}: the selected prompt language is absent")
    media = []
    for ordinal, filename in enumerate(shot["images"], 1):
        media.append({**materialize(data["root"], filename, folder_paths.get_input_directory()),
                      "media_type": "image", "ordinal": ordinal})
    for ordinal, filename in enumerate(shot.get("videos", []), 1):
        media.append({**materialize(data["root"], filename, folder_paths.get_input_directory()),
                      "media_type": "video", "ordinal": ordinal})
    if shot["audio"]:
        media.append({**materialize(data["root"], shot["audio"], folder_paths.get_input_directory()),
                      "media_type": "audio", "ordinal": 1, "audio_trim": shot["range"]})
    return {"schema": 1, "id": shot["id"], "title": shot["title"], "index": index,
            "total": len(shots), "prompt": prompt, "params": shot["params"], "media": media,
            "refs": shot["refs"], "range": shot["range"]}


class FeiHouEasyH3ProductionPackLoader:
    CATEGORY = "FeiHou Easy H3"
    FUNCTION = "load"
    RETURN_TYPES = (SHOT_TYPE,)
    RETURN_NAMES = ("production_shot",)
    DESCRIPTION = "Read one prepared Shotlist shot. Set shot_index to increment after generation, then queue the remaining shot count."

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "source": ("STRING", {"default": ""}),
            "shotlist_file": ("STRING", {"default": "", "tooltip": "Optional relative HTML path when multiple Shotlists exist"}),
            "audio_file": ("STRING", {"default": "", "tooltip": "Optional MV soundtrack path. Digital-human packs use voice_reference from the Shotlist."}),
            "shot_index": ("INT", {"default": 1, "min": 1, "max": 1000000, "control_after_generate": True}),
            "prompt_language": (["zh", "en"], {"default": "zh"}),
            "package_id": ("STRING", {"default": ""}),
        }}

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def load(self, source, shot_index, prompt_language, package_id, **kwargs):
        # Legacy path slots remain serializable but no longer override detection.
        shot = load_shot(source, package_id, shot_index, prompt_language)
        return {"ui": {"production_shot": [shot]}, "result": (shot,)}


def register_routes(routes, is_local):
    from aiohttp import web
    import asyncio

    def guard(request):
        # A custom header plus same-origin check prevents cross-site form/fetch
        # requests from turning this local path picker into a file-read endpoint.
        from urllib.parse import urlsplit
        origin = request.headers.get("Origin")
        return (is_local(request) and request.headers.get("X-FeiHou-Pack") == "1"
                and (not origin or urlsplit(origin).netloc == request.host))

    @routes.post("/feihou_easy_h3/production_pack/{action}")
    async def production_pack_route(request):
        if not guard(request):
            return web.json_response({"error": "Production packs are available only to the local ComfyUI browser"}, status=403)
        try:
            action = request.match_info["action"]
            if action == "upload":
                path = cache_root() / f"upload_{uuid.uuid4().hex}.zip"
                try:
                    size = 0
                    with path.open("xb") as output:
                        async for chunk in request.content.iter_chunked(1024 * 1024):
                            size += len(chunk)
                            if size > MAX_BYTES:
                                raise ValueError("Uploaded ZIP exceeds 4 GiB")
                            output.write(chunk)
                    result = await asyncio.to_thread(inspect_pack, str(path))
                    result["source"] = str(path)
                except Exception:
                    path.unlink(missing_ok=True)
                    raise
            else:
                if request.content_length and request.content_length > MAX_HTML:
                    raise ValueError("Request exceeds 16 MiB")
                payload = json.loads((await request.read()).decode("utf-8"))
                if action == "inspect":
                    result = await asyncio.to_thread(inspect_pack, payload["source"], payload.get("shotlist_file", ""))
                elif action == "prepare":
                    result = await asyncio.to_thread(commit_shots, payload["package_id"], payload["shots"], payload.get("audio_file", ""), bool(payload.get("diagnose")), payload.get("prompt_language", ""))
                elif action == "preview":
                    result = await asyncio.to_thread(load_shot, payload["source"], payload["package_id"], payload["shot_index"], payload["prompt_language"], payload.get("shotlist_file", ""), payload.get("audio_file", ""))
                else:
                    raise ValueError("Unknown package operation")
            return web.json_response(result)
        except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile) as exc:
            return web.json_response({"error": str(exc)}, status=400)
