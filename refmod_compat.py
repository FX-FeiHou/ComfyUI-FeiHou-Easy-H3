"""Consume upstream H3_REF_MODS without importing the optional node pack.

The upstream loader expands standalone files and v5 bundles into ordered
(H3RefMod, strength) pairs. ref_block owns its strength processing; the same
payload is presented to the text/vision encoder and passed to the sampler.
"""
from __future__ import annotations

import math
import re
import sys

import torch

LABELS = {"image": "Picture", "video": "Video", "audio": "Audio"}
REFERENCE_TAG_RE = re.compile(r"<(Picture|Video|Audio)\s+(\d+)>")


def normalize_refmods(mods):
    if mods is None:
        return ()
    if not isinstance(mods, (list, tuple)):
        raise ValueError("refmods must connect to Load H3 RefMods.mods (H3_REF_MODS).")
    active = []
    for index, entry in enumerate(mods, 1):
        if not isinstance(entry, (tuple, list)) or len(entry) != 2:
            raise ValueError(f"RefMod {index}: expected a (reference, strength) pair.")
        mod, strength = entry
        try:
            strength = float(strength)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"RefMod {index}: invalid strength.") from exc
        if not math.isfinite(strength) or not 0 <= strength <= 1:
            raise ValueError(f"RefMod {index}: strength must be between 0 and 1.")
        if strength == 0:
            continue
        if getattr(mod, "kind", None) not in LABELS or not callable(getattr(mod, "ref_block", None)):
            raise ValueError(f"RefMod {index}: incompatible reference; use the upstream RefMod loader.")
        active.append((mod, strength))
    return tuple(active)


def present_refmods(mods, vae, *, text_only=False, reference_fps=24.0):
    """Decode visuals only for Qwen; never re-encode the sampler's latents."""
    items, blocks, names = [], [], []
    for index, (mod, strength) in enumerate(mods, 1):
        block = mod.ref_block(strength)
        if not isinstance(block, dict) or block.get("kind") != mod.kind:
            raise ValueError(f"RefMod {index}: invalid native reference block.")
        block = dict(block)
        kind = block["kind"]
        latent = block.get("audio_latent" if kind == "audio" else "latent")
        prefix = (1, 32, 2) if kind == "audio" else (1, 24)
        ndim = 4 if kind == "audio" else 5
        if not isinstance(latent, torch.Tensor) or latent.ndim != ndim \
                or tuple(latent.shape[:len(prefix)]) != prefix or min(latent.shape) <= 0:
            raise ValueError(f"RefMod {index}: invalid {kind} latent shape.")
        if kind == "audio":
            if block.get("ref_audio_t") != latent.shape[-1]:
                raise ValueError(f"RefMod {index}: audio frame count does not match its latent.")
        elif latent.shape[-2] % 2 or latent.shape[-1] % 2 \
                or block.get("latent_h") != latent.shape[-2] \
                or block.get("latent_w") != latent.shape[-1] \
                or (kind == "image" and latent.shape[2] != 1) \
                or (kind == "video" and block.get("latent_t") != latent.shape[2]):
            raise ValueError(f"RefMod {index}: visual dimensions do not match its latent.")
        item = {"type": kind}
        if kind != "audio":
            if vae is None:
                raise ValueError("Connect an H3 video VAE in the Easy H3 Loader to present visual RefMods.")
            pixels = vae.decode(latent)
            if pixels.ndim == 5 and pixels.shape[0] == 1:
                pixels = pixels[0]
            if pixels.ndim != 4 or pixels.shape[-1] != 3 or pixels.shape[0] < 1:
                raise ValueError("H3 VAE must decode RefMods to [frames, height, width, 3].")
            if kind == "image":
                item["data"] = pixels[:1].detach().cpu().clone()
            else:
                # Match upstream Text Encode: 24 fps reconstruction, 2 fps
                # presentation. Stacked photos do not preserve source timing.
                times = [i / 2 for i in range(math.ceil(pixels.shape[0] * 2 / reference_fps))]
                indices = [min(round(t * reference_fps), pixels.shape[0] - 1) for t in times]
                item["data"] = pixels[indices].detach().cpu().clone()
                item["timestamps"] = times
        items.append(item)
        names.append(str(getattr(mod, "name", "") or f"RefMod {index}"))
        if not text_only:
            block["refmod"] = True
            blocks.append(block)
    return items, blocks, names


def reference_catalog(loader_class):
    """Read the installed upstream loader's metadata without loading tensors."""
    if loader_class is None:
        return []
    upstream = sys.modules[loader_class.__module__]
    entries = []
    for name in upstream._list_mod_names():
        try:
            meta = upstream.read_refmod_meta(upstream._find_mod_path(name))
            members = upstream.bundle_members(meta) if meta.get("kind") == "bundle" else [meta]
            entries.append({"name": name, "members": [
                {"index": index, "name": str(member.get("name") or name), "kind": member["kind"]}
                for index, member in enumerate(members) if member.get("kind") in LABELS
            ]})
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return entries


def reference_map(items, names):
    """RefMods lead each modality; ordinary media follow them."""
    counters = dict.fromkeys(LABELS, 0)
    rows = []
    for item, name in zip(items, names):
        kind = item["type"]
        counters[kind] += 1
        rows.append(f"<{LABELS[kind]} {counters[kind]}> = RefMod: {name}")
    return "\n".join(rows)


def validate_reference_tags(prompt, items):
    counts = {label: sum(item["type"] == kind for item in items) for kind, label in LABELS.items()}
    for match in REFERENCE_TAG_RE.finditer(prompt):
        if not 1 <= int(match[2]) <= counts[match[1]]:
            raise ValueError(f"Easy H3: {match[0]} has no active reference; check reference_map / 请检查参考编号对应表。")
