"""Production-pack import. HTML is rendered only in an isolated browser frame.

No package Python/JS is executed by the server. Only a validated shot manifest
and referenced media cross the loader socket; model/sampler settings stay local.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import posixpath
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
from urllib.parse import urlsplit

SHOT_TYPE = "FEIHOU_H3_PRODUCTION_SHOT"
PLAN_TYPE = "FEIHOU_H3_PRODUCTION_PLAN"
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
AUDIO_EXT = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".avi"}
MAX_FILES = 5000
MAX_BYTES = 4 * 1024**3
MAX_HTML = 16 * 1024**2

# The browser-facing Setup node deliberately does not duplicate the sampling
# controls already present in an Easy H3 workflow. A queue/API client can
# still pass those values as a small JSON document on the production-pack
# loader; the prepared plan carries the validated document to Setup.
_CONTINUOUS_RUNTIME_KEYS = {
    "two_pass", "first_total_steps", "first_steps", "first_sampler_name", "first_scheduler",
    "second_steps", "second_sampler_name", "latent_upscaler", "upscale_by",
    "steps", "sampler_name", "scheduler", "denoise", "cfg", "negative_prompt",
    "semantic_bridge_on", "semantic_bridge", "bridge_strength", "default_trigger_words",
    "seed", "seed_mode", "generation_order", "start_shot",
}
_CONTINUOUS_RUNTIME_ALIASES = {
    "total_steps": "first_total_steps",
    "first_pass_steps": "first_steps",
    "second_pass_steps": "second_steps",
    "upscaler": "latent_upscaler",
    "scale": "upscale_by",
}


def normalize_continuous_runtime_config(value):
    """Parse and whitelist the optional continuation runtime JSON."""
    if value is None or value == "":
        return {}
    if isinstance(value, str):
        if len(value.encode("utf-8")) > 64 * 1024:
            raise ValueError("continuous_runtime_config exceeds 64 KiB")
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"continuous_runtime_config 不是有效 JSON：{exc.msg}") from exc
    if not isinstance(value, dict):
        raise ValueError("continuous_runtime_config 必须是 JSON 对象")
    result = {}
    for key, item in value.items():
        name = _CONTINUOUS_RUNTIME_ALIASES.get(str(key), str(key))
        if name in _CONTINUOUS_RUNTIME_KEYS:
            result[name] = item
    return result


def _pack_request_allowed(request, is_local):
    """CSRF protection, not authentication; cloud deployments need a login gateway.

    Browser-controlled Fetch Metadata handles reverse proxies that rewrite Host.
    Never use a forwarded client IP to grant unrestricted local filesystem access.
    """
    if request.headers.get("X-FeiHou-Pack") != "1":
        return False
    site = request.headers.get("Sec-Fetch-Site", "")
    if site and site != "same-origin":
        return False
    origin = request.headers.get("Origin", "")
    if not origin:
        # Some cloud gateways strip Origin but preserve browser Fetch Metadata.
        # The custom header was checked above; explicit cross-site values still fail.
        return site == "same-origin" or (is_local(request) and not site)
    try:
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return False
        return site == "same-origin" or parsed.netloc.casefold() == request.host.casefold()
    except ValueError:
        return False


def _pack_request_diagnostic(request, is_local):
    """Report only guard facts; never expose cookies, tokens, URLs or headers wholesale."""
    marker = request.headers.get("X-FeiHou-Pack")
    origin = request.headers.get("Origin", "")
    site = request.headers.get("Sec-Fetch-Site", "")
    valid_origin = False
    host_match = False
    try:
        parsed = urlsplit(origin)
        valid_origin = bool(parsed.scheme in {"http", "https"} and parsed.hostname
                            and not parsed.username and not parsed.password)
        host_match = valid_origin and parsed.netloc.casefold() == request.host.casefold()
    except ValueError:
        pass
    peer_local = bool(is_local(request))
    if marker != "1":
        reason = "pack-header-missing" if marker is None else "pack-header-invalid"
    elif site and site != "same-origin":
        reason = "fetch-site-rejected"
    elif not origin:
        reason = "allowed" if site == "same-origin" or (peer_local and not site) else "origin-missing"
    elif not valid_origin:
        reason = "origin-invalid"
    elif site != "same-origin" and not host_match:
        reason = "origin-host-mismatch"
    else:
        reason = "allowed"
    return {
        "build": "pack-access-20260911c", "reason": reason,
        "pack_header": "1" if marker == "1" else ("missing" if marker is None else "invalid"),
        "fetch_site": site if site in {"same-origin", "same-site", "cross-site", "none"} else ("missing" if not site else "invalid"),
        "origin_present": bool(origin), "origin_valid": valid_origin,
        "origin_host_match": host_match, "peer_loopback": peer_local,
    }


def _pack_request_is_local(request, is_local):
    """A loopback proxy must not make an external browser a local path reader."""
    if not is_local(request):
        return False
    if not request.headers.get("Origin") and request.headers.get("Sec-Fetch-Site"):
        # Without the browser origin, a loopback gateway does not prove locality.
        return False
    try:
        host = urlsplit(request.headers.get("Origin") or f"http://{request.host}").hostname
        return host == "localhost" or ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def _remote_pack_roots():
    import folder_paths
    roots = [Path(folder_paths.get_input_directory()).resolve()]
    config = Path(__file__).with_name("production_pack_access.json")
    if config.exists():
        if config.stat().st_size > 65536:
            raise ValueError("production_pack_access.json exceeds 64 KiB")
        data = json.loads(config.read_text(encoding="utf-8-sig"))
        extra = data.get("remote_roots", []) if isinstance(data, dict) else None
        if not isinstance(extra, list) or any(not isinstance(p, str) for p in extra):
            raise ValueError("production_pack_access.json: remote_roots must be an array of absolute folder paths")
        for name in extra:
            path = Path(name).expanduser()
            if not path.is_absolute() or path.resolve() == Path(path.anchor):
                raise ValueError("remote_roots must contain specific absolute folders, not filesystem roots")
            roots.append(path.resolve())
    return roots


def _resolve_pack_source(source):
    """Relative package names always belong to ComfyUI/input, never process CWD."""
    import folder_paths
    name = str(source).strip().strip('"')
    if not name:
        raise ValueError("Enter a production-pack folder or ZIP path first")
    candidate = Path(name.replace("\\", "/")).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return contained(folder_paths.get_input_directory(), name)


def _authorize_pack_source(source, local):
    if local:
        return
    name = str(source).strip().strip('"')
    if name.replace("\\", "/").startswith("//"):
        raise ValueError("Remote package paths cannot use UNC/network shares; upload a ZIP instead")
    path = _resolve_pack_source(name)
    # Only server-created upload names, never arbitrary paths in the user/cache directory.
    if (path.parent == cache_root().resolve()
            and re.fullmatch(r"upload_[a-f0-9]{32}\.zip", path.name) and path.is_file()):
        return
    if any(path.is_relative_to(root) for root in _remote_pack_roots()):
        return
    raise ValueError(
        "Remote package path is outside allowed folders. Upload a ZIP, put the package under ComfyUI/input, "
        "or add its server folder to remote_roots in production_pack_access.json. "
        "远程路径须位于云端 ComfyUI/input 内；其他目录请在服务器 production_pack_access.json 的 remote_roots 中添加。"
    )


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


_PROJECT_DATA = re.compile(r'<script\b[^>]*\bid\s*=\s*["\']dh-project-data["\'][^>]*>(.*?)</script\s*>', re.I | re.S)


def asset_file_map(html, html_file, files):
    """Map asset IDs (P1, P2 ...) to package images declared in the page's inline project data.

    Only the JSON data block is parsed; no page script runs. User images keep their own
    names (for example ../人物.jpg) and still resolve as P1. Files named after the ID win.
    """
    match = _PROJECT_DATA.search(html)
    if not match:
        return {}
    try:
        project = json.loads(match.group(1))
    except ValueError:
        return {}
    by_name = {f.casefold(): f for f in files if Path(f).suffix.lower() in IMAGE_EXT}
    base = PurePosixPath(html_file).parent.as_posix()
    mapping = {}
    assets = project.get("assets") if isinstance(project, dict) else None
    for asset in assets if isinstance(assets, list) else []:
        if not isinstance(asset, dict):
            continue
        ident, name = str(asset.get("id", "")).strip(), str(asset.get("file", "")).strip().replace("\\", "/")
        if not ident or not name or name.startswith("data:") or re.match(r"^[a-z][a-z0-9+.-]*:", name, re.I):
            continue
        rel = posixpath.normpath(posixpath.join(base, name)) if base not in ("", ".") else posixpath.normpath(name)
        if rel == ".." or rel.startswith("../") or rel.startswith("/"):
            continue
        found = by_name.get(rel.casefold())
        if found:
            mapping[ident.casefold()] = found
    return mapping


PAGE_META = re.compile(
    r"""<meta\b[^>]*\bname\s*=\s*["']feihou-pack-page["'][^>]*>""", re.IGNORECASE)
PAGE_CONTENT = re.compile(r"""\bcontent\s*=\s*["']\s*([A-Za-z_-]+)\s*["']""", re.IGNORECASE)
SHOTLIST_NAMES = ("分镜列表", "shotlist")


def _page_role(path: Path) -> str:
    """The page's declared role (<meta name="feihou-pack-page" content="...">), "" when absent."""
    try:
        with path.open("rb") as stream:
            head = stream.read(65536).decode("utf-8", errors="ignore")
    except OSError:
        return ""
    tag = PAGE_META.search(head)
    content = PAGE_CONTENT.search(tag.group(0)) if tag else None
    return content.group(1).strip().lower() if content else ""


def _shotlist_candidates(root: Path, files) -> list:
    """Shotlist page(s): the declared role first, otherwise the file name (number prefixes are ignored)."""
    pages = [f for f in files if Path(f).suffix.lower() in {".html", ".htm"}]
    roles = {f: _page_role(contained(root, f)) for f in pages}
    declared = [f for f in pages if roles[f] == "shotlist"]
    if declared:
        return declared
    # Pages that declare another role (assets / prompts) are never the shotlist.
    return [f for f in pages if not roles[f]
            and any(name in Path(f).stem.lower() for name in SHOTLIST_NAMES)]


def inspect_pack(source, shotlist_file=""):
    root = _resolve_pack_source(source)
    source = str(root)
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
    candidates = _shotlist_candidates(root, files)
    if shotlist_file:
        selected = contained(root, shotlist_file).relative_to(root).as_posix()
        if selected not in files or Path(selected).suffix.lower() not in {".html", ".htm"}:
            raise ValueError("Shotlist HTML is not in this package")
    elif len(candidates) == 1:
        selected = candidates[0]
    else:
        raise ValueError(
            f"Expected one Shotlist HTML; found {len(candidates)}: {candidates}. "
            f"制作包里需要且只能有一个分镜列表页面（页面开头 <meta name=\"feihou-pack-page\" content=\"shotlist\">，"
            f"或文件名含「分镜列表」/ shotlist）。")
    page = contained(root, selected)
    if page.stat().st_size > MAX_HTML:
        raise ValueError("Shotlist HTML exceeds 16 MiB")
    html = page.read_text(encoding="utf-8-sig")
    package_id = uuid.uuid4().hex
    data = {"source": source, "root": str(root), "html_file": selected,
            "html_sha256": hashlib.sha256(html.encode()).hexdigest(), "files": files,
            "asset_files": asset_file_map(html, selected, files)}
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


def _resolve_audio_references(data, raw, fallback, default_range):
    """Ordered per-shot references; no artificial audio-count ceiling."""
    declared = raw.get("audio_references")
    if declared is None:
        declared = [{"file": fallback, "range": default_range}] if fallback else []
    if not isinstance(declared, list):
        raise ValueError(f"{raw['id']}: audio_references must be an ordered list")
    files = [f for f in data["files"] if Path(f).suffix.lower() in AUDIO_EXT]
    result = []
    for ordinal, entry in enumerate(declared, 1):
        if isinstance(entry, str):
            entry = {"file": entry}
        if not isinstance(entry, dict) or not isinstance(entry.get("file"), str) or not entry["file"].strip():
            raise ValueError(f"{raw['id']}: Audio {ordinal} requires a file")
        name = entry["file"].strip().replace("\\", "/")
        matches = [f for f in files if f.casefold() == name.casefold()]
        if not matches:
            matches = [f for f in files if Path(f).name.casefold() == Path(name).name.casefold()]
        if len(matches) != 1 or not contained(data["root"], matches[0]).is_file():
            raise ValueError(f"{raw['id']}: Audio {ordinal} is missing or ambiguous: {name}")
        trim = entry.get("range", "00:00:000–00:00:000")
        parts = re.split(r"\s*[-–—~～至]\s*", str(trim))
        if len(parts) != 2:
            raise ValueError(f"{raw['id']}: Audio {ordinal} requires a start–end range")
        start, end = map(time_seconds, parts)
        if start < 0 or end < 0 or (end != 0 and end <= start):
            raise ValueError(f"{raw['id']}: Audio {ordinal} range must increase (end=0 means to end)")
        result.append({"file": matches[0], "range": f"{time_text(start)}–{time_text(end)}"})
    return result


_CONTINUE_WORDS = {"continue", "continuation", "continuous", "seamless", "接续", "续接", "连续", "无缝"}
_CUT_WORDS = {"cut", "hard_cut", "hardcut", "hard cut", "硬切", "切", "切镜", "剪切"}
_FRAME_CHOICES = {"0+0", "0+5", "5+0", "5+5", "22+0", "22+5", "22+22", "39+0", "39+5", "39+22", "39+39"}


def _shot_transition(value):
    text = str(value or "").strip().casefold()
    if not text:
        return None
    if text in _CONTINUE_WORDS:
        return "continue"
    if text in _CUT_WORDS:
        return "cut"
    return None


def _shot_frames(value):
    """'22+5', [22, 5], {'first': 22, 'second': 5} or a bare 0/5/22/39 (= 0+0 / 5+5 / 22+22 / 39+39)."""
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        value = f"{value.get('first', value.get('1st', ''))}+{value.get('second', value.get('2nd', ''))}"
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        value = f"{value[0]}+{value[1]}"
    text = str(value).replace(" ", "").replace("＋", "+")
    if text in ("0", "5", "22", "39"):  # 0 = no continuation frames (works like a hard cut)
        text = f"{text}+{text}"
    return text if text in _FRAME_CHOICES else None


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
        if raw.get("audio_references") is not None:
            selected_audio = ""  # Explicit per-shot list, including [], overrides legacy selection.
        elif selected_audio:
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
        default_range = "00:00:000–00:00:000" if digital_human else f"{time_text(start)}–{time_text(end)}"
        audios = _resolve_audio_references(data, raw, selected_audio, default_range)
        selected_audio = audios[0]["file"] if audios else ""
        refs = raw.get("refs", [])
        if not isinstance(refs, list) or len(refs) > 9:
            raise ValueError(f"{shot_id}: expected 0–9 ordered image references, got {len(refs) if isinstance(refs, list) else refs!r}")
        declared_files = data.get("asset_files", {})
        images = []
        for ref in refs:
            key = str(ref).strip().casefold()
            matches = assets.get(key, [])
            if not matches and key in declared_files:
                matches = [declared_files[key]]
            if not matches:
                raise ValueError(f"{shot_id}: asset {ref} has no image. Name its image {ref}.png (or .jpg/.webp) "
                                 f"inside the package, or set its file in the asset page. 资产 {ref} 缺少图片")
            if len(matches) != 1:
                raise ValueError(f"{shot_id}: asset {ref} is ambiguous; keep only one image named {ref}: {matches}")
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
        if audios and not images and not videos:
            raise ValueError(f"{shot_id}: reference audio needs at least one reference image or video "
                             "(MiniMax H3 reference mode). Add an image, or remove this shot's audio for text-to-video. "
                             "无图镜头不能带参考音频：请加参考图，或去掉本镜音频走文生视频")
        prompts = {lang: str(raw.get(f"prompt_{lang}", "")).strip() for lang in ("zh", "en")}
        if not any(prompts.values()) or any(len(p) > 100000 for p in prompts.values()):
            raise ValueError(f"{shot_id}: missing or oversized prompt")
        for prompt in prompts.values():
            used = [int(n) for n in re.findall(r"<(?:Picture|Image)\s+(\d+)>", prompt, re.I)]
            if any(n < 1 or n > len(images) for n in used):
                raise ValueError(f"{shot_id}: prompt references an unavailable Picture slot")
            if any(int(n) < 1 or int(n) > len(videos) for n in re.findall(r"<Video\s+(\d+)>", prompt, re.I)):
                raise ValueError(f"{shot_id}: prompt references an unavailable Video slot")
            if any(int(n) < 1 or int(n) > len(audios) for n in re.findall(r"<Audio\s+(\d+)>", prompt, re.I)):
                raise ValueError(f"{shot_id}: prompt references an unavailable Audio slot (loaded {len(audios)})")
            if not selected_audio and re.search(r"<Audio\s+\d+>", prompt, re.I):
                raise ValueError(f"{shot_id}: prompt references Audio but no reference audio exists")
        # No visual media: the Easy H3 node runs image mode without media, i.e. text-to-video.
        params = {"mode": "reference" if images or videos else "image", "seconds": duration, "audio_duration_auto": False if digital_human or raw.get("audio_references") is not None else bool(selected_audio),
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
        transition = _shot_transition(raw.get("transition"))
        continuation_frames = _shot_frames(raw.get("continuation_frames"))
        if raw.get("transition") not in (None, "") and transition is None:
            raise ValueError(f"{shot_id}: transition must be 接续/continue or 硬切/cut, got {raw.get('transition')!r}")
        if raw.get("continuation_frames") not in (None, "") and continuation_frames is None:
            raise ValueError(f"{shot_id}: continuation_frames must be 0 or one of 0+5/5+0/5+5/22+0/22+5/22+22/39+0/39+5/39+22/39+39, "
                             f"got {raw.get('continuation_frames')!r}")
        # Ignore all package resolution declarations; generation uses manual settings.
        result.append({"transition": transition, "continuation_frames": continuation_frames,
                       "id": shot_id, "title": str(raw.get("title", shot_id)), "params": params,
                       "prompts": prompts, "refs": list(refs), "images": images, "videos": videos, "audio": selected_audio, "audios": audios,
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
                report.append(f"OK {index}: {validated['id']} | {validated['params']['seconds']}s | images={len(validated['images'])}, videos={len(validated['videos'])}, audio={len(validated['audios'])}"
                              f" | 衔接={ {'continue': '接续', 'cut': '硬切'}.get(validated.get('transition'), '未指定')}"
                              f"{' ' + validated['continuation_frames'] if validated.get('continuation_frames') else ''}")
                report.extend(f"  Audio {n}: {a['file']} | {a['range']}" for n, a in enumerate(validated['audios'], 1))
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
    if _resolve_pack_source(source) != _resolve_pack_source(data["source"]):
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
    # Old prepared manifests may still contain resolution overrides.
    shot = {**shot, "params": {k: v for k, v in shot["params"].items()
                               if k not in {"resolution", "width", "height"}}}
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
    audios = shot.get("audios", [{"file": shot["audio"], "range": shot["range"]}] if shot["audio"] else [])
    for ordinal, audio in enumerate(audios, 1):
        media.append({**materialize(data["root"], audio["file"], folder_paths.get_input_directory()),
                      "media_type": "audio", "ordinal": ordinal, "audio_trim": audio["range"]})
    return {"schema": 1, "id": shot["id"], "title": shot["title"], "index": index,
            "total": len(shots), "prompt": prompt, "params": shot["params"], "media": media,
            "refs": shot["refs"], "range": shot["range"],
            "transition": shot.get("transition"), "continuation_frames": shot.get("continuation_frames"),
            # Stable across refreshes: identifies the pack for the saved continuation (.latent) files.
            "pack_key": hashlib.sha256(f"{_resolve_pack_source(data['source'])}|{data['html_file']}".encode()).hexdigest()[:16]}


def load_plan(source, package_id, prompt_language, shotlist_file="", audio_file="", runtime_config=None):
    """Materialize every prepared shot for the in-node continuation runner.

    The ordinary ``load_shot`` contract remains unchanged.  A plan is an
    immutable snapshot of those same shot dictionaries, so the continuation
    node can rebuild each H3 conditioning block without asking the browser to
    queue a separate graph execution for every shot.
    """
    data = read_pack(package_id)
    if _resolve_pack_source(source) != _resolve_pack_source(data["source"]):
        raise ValueError("Source changed: load / refresh the production pack before queuing")
    if shotlist_file and contained(data["root"], shotlist_file) != contained(data["root"], data["html_file"]):
        raise ValueError("Shotlist selection changed: load / refresh the production pack")
    page = contained(data["root"], data["html_file"])
    if hashlib.sha256(page.read_text(encoding="utf-8-sig").encode()).hexdigest() != data["html_sha256"]:
        raise ValueError("Shotlist HTML changed: load / refresh the production pack before queuing")
    shots = [load_shot(source, package_id, index, prompt_language, shotlist_file, audio_file)
             for index in range(1, len(data.get("shots", [])) + 1)]
    return {"schema": 1, "source": str(source or ""), "package_id": str(package_id or ""),
            "prompt_language": str(prompt_language or "zh"), "shots": shots,
            "context_frames": 22, "runtime_config": normalize_continuous_runtime_config(runtime_config)}


CONTEXT_CHOICES = ["自动", "0+5", "5+0", "5+5", "22+0", "22+5", "22+22", "39+0", "39+5", "39+22", "39+39"]
CONTEXT_DEFAULT = "自动"
TRANSITION_CHOICES = ["自动", "接续", "硬切"]


class FeiHouEasyH3ProductionPackLoader:
    CATEGORY = "FeiHou Easy H3"
    FUNCTION = "load"
    RETURN_TYPES = (SHOT_TYPE,)
    RETURN_NAMES = ("production_shot",)
    DESCRIPTION = (
        "Read one prepared Shotlist shot. Set shot_index to increment after generation, then queue the remaining shot count. "
        "视频接续开启时，分镜 2 起自动接上一个分镜的结尾（配合「FeiHou Easy H3 接续合并」输出完整视频）。"
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "source": ("STRING", {"default": ""}),
            "shotlist_file": ("STRING", {"default": "", "tooltip": "Optional relative HTML path when multiple Shotlists exist"}),
            "audio_file": ("STRING", {"default": "", "tooltip": "Optional MV soundtrack path. Digital-human packs use voice_reference from the Shotlist."}),
            "shot_index": ("INT", {"default": 1, "min": 1, "max": 1000000, "control_after_generate": True}),
            "prompt_language": (["zh", "en"], {"default": "zh"}),
            "package_id": ("STRING", {"default": ""}),
            # Legacy hidden slot, kept so saved widget positions do not shift.
            "continuous_runtime_config": ("STRING", {"default": "", "multiline": True, "hidden": True}),
            "continuation": (TRANSITION_CHOICES, {"default": "自动", "tooltip":
                "分镜衔接方式。接续：每个分镜都接上一个分镜结尾；硬切：每个分镜独立生成、直接切换；"
                "自动：按制作包分镜里的 transition（接续 / 硬切）决定，没写的按接续。每段结尾都会保存，硬切也可作为后续接续来源。"}),
            "context_frames": (CONTEXT_CHOICES, {"default": CONTEXT_DEFAULT, "tooltip":
                "接续帧数（一采+二采）：上一个分镜结尾多少帧引导本分镜。前一个数给一采（构图、动作），"
                "后一个数给二采（细节），0 表示该阶段不用画面引导。自动：按制作包分镜里的 continuation_frames，没写的按 22+22。"}),
            "use_pack": ("BOOLEAN", {"default": True, "label_on": "开", "label_off": "关", "tooltip":
                "批量运行制作包。开：按制作包内容批量运行；关：不读制作包，只作为接续加载器——Setup 用自己的提示词和素材，"
                "接着下面「接续视频」选的视频生成，复用接续帧数。没选视频时：分镜衔接为「接续」则中止，「自动」/「硬切」则独立生成并保存结尾 latent，供下一段接续。"}),
            "continue_video": ("STRING", {"default": "自动", "tooltip":
                "接续视频：点下方「选择接续视频」按钮弹出窗口选上一段的视频（也可直接填视频完整路径），当前分镜就接着它往后生成；"
                "视频旁边有同名 .latent 时用 .latent，没有就直接读视频结尾。只对本次排队的第一个分镜生效，之后自动回到「自动」。"
                "自动：接内存里或已保存的上一分镜。分镜衔接选「硬切」时不接续。"}),
        }}

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def load(self, source, shot_index, prompt_language, package_id, continuation="自动", context_frames=CONTEXT_DEFAULT,
             continue_video="自动", use_pack=True, **kwargs):
        if use_pack is False or str(use_pack).strip().lower() in ("false", "0", "off"):
            from .production_continuation import manual_continuation
            return {"result": (manual_continuation(continuation, context_frames, continue_video),)}
        # Legacy path slots remain serializable but no longer override detection.
        shot = load_shot(source, package_id, shot_index, prompt_language)
        from .production_continuation import continue_shot
        continued = continue_shot(shot, continuation, context_frames, continue_video)
        return {"ui": {"production_shot": [shot]}, "result": (continued,)}


CONTINUE_VIDEO_EXTENSIONS = (".mp4", ".webm", ".mov", ".mkv", ".m4v", ".latent")
CONTINUE_UPLOAD_FOLDER = "feihou_h3_continue"


def _continue_find(name: str, size: int) -> dict:
    """A picked video that already lives in ComfyUI's output/input (same name and size): use it in place."""
    import folder_paths
    name = os.path.basename(name)
    if not name.lower().endswith(CONTINUE_VIDEO_EXTENSIONS) or size < 0:
        return {}
    for label, root in (("output", folder_paths.get_output_directory()), ("input", folder_paths.get_input_directory())):
        for folder, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            if name in files:
                path = os.path.join(folder, name)
                try:
                    if os.path.getsize(path) == size:
                        return {"value": label + "/" + os.path.relpath(path, root).replace(os.sep, "/")}
                except OSError:
                    pass
    return {}


async def _continue_upload(request) -> dict:
    """Store a picked video (or its .latent) under input/feihou_h3_continue/<batch>/."""
    import folder_paths
    from urllib.parse import unquote
    name = os.path.basename(unquote(request.headers.get("X-FeiHou-Name", "")).replace("\\", "/"))
    batch = request.headers.get("X-FeiHou-Batch", "")
    if not name or not name.lower().endswith(CONTINUE_VIDEO_EXTENSIONS) or name.startswith("."):
        raise ValueError("只能上传视频（mp4 / webm / mov / mkv）或 .latent 文件")
    if not re.fullmatch(r"[a-f0-9]{12}", batch):
        raise ValueError("Invalid upload batch")
    folder = Path(folder_paths.get_input_directory()) / CONTINUE_UPLOAD_FOLDER / batch
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    temp = folder / f".{uuid.uuid4().hex}.part"
    try:
        size = 0
        with temp.open("xb") as output:
            async for chunk in request.content.iter_chunked(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("文件超过 4 GiB")
                output.write(chunk)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return {"value": f"input/{CONTINUE_UPLOAD_FOLDER}/{batch}/{name}"}


def register_routes(routes, is_local):
    from aiohttp import web
    import asyncio

    @routes.post("/feihou_easy_h3/production_pack/{action}")
    async def production_pack_route(request):
        if not _pack_request_allowed(request, is_local):
            diagnostic = _pack_request_diagnostic(request, is_local)
            detail = json.dumps(diagnostic, ensure_ascii=False)
            return web.json_response({
                "error": "制作包来源检查未通过。后端实际收到的检查信息（不含凭据）： " + detail,
                "diagnostic": diagnostic,
            }, status=403)
        try:
            local = _pack_request_is_local(request, is_local)
            action = request.match_info["action"]
            if action == "continue_upload":
                result = await _continue_upload(request)
            elif action == "upload":
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
                if not isinstance(payload, dict):
                    raise ValueError("Expected a production-pack JSON object")
                if action == "inspect":
                    _authorize_pack_source(payload["source"], local)
                    result = await asyncio.to_thread(inspect_pack, payload["source"], payload.get("shotlist_file", ""))
                elif action == "prepare":
                    _authorize_pack_source(read_pack(payload["package_id"])["source"], local)
                    result = await asyncio.to_thread(commit_shots, payload["package_id"], payload["shots"], payload.get("audio_file", ""), bool(payload.get("diagnose")), payload.get("prompt_language", ""))
                elif action == "continue_find":
                    result = await asyncio.to_thread(_continue_find, str(payload.get("name") or ""), int(payload.get("size") or -1))
                elif action == "preview":
                    _authorize_pack_source(read_pack(payload["package_id"])["source"], local)
                    result = await asyncio.to_thread(load_shot, payload["source"], payload["package_id"], payload["shot_index"], payload["prompt_language"], payload.get("shotlist_file", ""), payload.get("audio_file", ""))
                else:
                    raise ValueError("Unknown package operation")
            return web.json_response(result)
        except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile) as exc:
            return web.json_response({"error": str(exc)}, status=400)
