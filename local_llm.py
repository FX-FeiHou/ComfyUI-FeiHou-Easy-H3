"""Optional local prompt-optimizer backends; no models load during node import.

Transformers/GGUF request lifetime and projector matching adapted from
goohai/Goohai-MiniMax-H3_Integration (GPL-3.0-or-later).
"""
from __future__ import annotations

import base64
from difflib import SequenceMatcher
import gc
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import re
import struct
import threading

_LOCK = threading.Lock()
DEFAULTS = {"model": "", "mmproj": "", "device": "auto", "max_tokens": 4096,
            "context_length": 32768}

# Fixed profiles let the Standard prompt optimizer load a single model weight
# file without requiring a neighboring config.json. The tensor header is used
# to choose between these profiles, so changing the filename does not break
# detection. Tokenizers are separate assets and are resolved from the local
# model directory first, then from the corresponding Hugging Face repository.
FIXED_MODEL_PROFILES = {
    "qwen3-8b": {
        "model_type": "qwen3",
        "tokenizer_repo": "Qwen/Qwen3-8B",
        "config": {
            "architectures": ["Qwen3ForCausalLM"],
            "attention_bias": False,
            "attention_dropout": 0.0,
            "bos_token_id": 151643,
            "eos_token_id": 151645,
            "head_dim": 128,
            "hidden_act": "silu",
            "hidden_size": 4096,
            "initializer_range": 0.02,
            "intermediate_size": 12288,
            "max_position_embeddings": 40960,
            "max_window_layers": 36,
            "model_type": "qwen3",
            "num_attention_heads": 32,
            "num_hidden_layers": 36,
            "num_key_value_heads": 8,
            "rms_norm_eps": 1e-6,
            "rope_scaling": None,
            "rope_theta": 1000000,
            "sliding_window": None,
            "tie_word_embeddings": False,
            "torch_dtype": "bfloat16",
            "use_cache": True,
            "use_sliding_window": False,
            "vocab_size": 151936,
        },
    },
    "deepseek-v4-flash": {
        "model_type": "deepseek_v4",
        "tokenizer_repo": "deepseek-ai/DeepSeek-V4-Flash-0731",
        "config": {
            "architectures": ["DeepseekV4ForCausalLM"],
            "attention_bias": False,
            "attention_dropout": 0.0,
            "bos_token_id": 0,
            "eos_token_id": 1,
            "expert_dtype": "fp4",
            "hc_eps": 1e-6,
            "hc_mult": 4,
            "hc_sinkhorn_iters": 20,
            "head_dim": 512,
            "hidden_act": "silu",
            "hidden_size": 4096,
            "index_head_dim": 128,
            "index_n_heads": 64,
            "index_topk": 512,
            "initializer_range": 0.02,
            "max_position_embeddings": 1048576,
            "model_type": "deepseek_v4",
            "moe_intermediate_size": 2048,
            "n_routed_experts": 256,
            "n_shared_experts": 1,
            "norm_topk_prob": True,
            "num_attention_heads": 64,
            "num_experts_per_tok": 6,
            "num_hidden_layers": 43,
            "num_hash_layers": 3,
            "num_key_value_heads": 1,
            "num_nextn_predict_layers": 1,
            "o_groups": 8,
            "o_lora_rank": 1024,
            "q_lora_rank": 1024,
            "qk_rope_head_dim": 64,
            "quantization_config": {
                "activation_scheme": "dynamic",
                "fmt": "e4m3",
                "quant_method": "fp8",
                "scale_fmt": "ue8m0",
                "weight_block_size": [128, 128],
            },
            "rms_norm_eps": 1e-6,
            "rope_scaling": {
                "beta_fast": 32,
                "beta_slow": 1,
                "factor": 16,
                "original_max_position_embeddings": 65536,
                "type": "yarn",
            },
            "rope_theta": 10000,
            "routed_scaling_factor": 1.5,
            "scoring_func": "sqrtsoftplus",
            "sliding_window": 128,
            "swiglu_limit": 10.0,
            "tie_word_embeddings": False,
            "topk_method": "noaux_tc",
            "torch_dtype": "bfloat16",
            "use_cache": True,
            "vocab_size": 129280,
            "compress_rope_theta": 160000,
            "compress_ratios": [0, 0, 4, 128, 4, 128, 4, 128, 4, 128, 4, 128, 4,
                                128, 4, 128, 4, 128, 4, 128, 4, 128, 4, 128,
                                4, 128, 4, 128, 4, 128, 4, 128, 4, 128, 4,
                                128, 4, 128, 4, 128, 4, 128, 4, 128, 4, 0, 0, 0],
            "dspark_block_size": 5,
            "dspark_noise_token_id": 128799,
            "dspark_target_layer_ids": [40, 41, 42],
            "dspark_markov_rank": 256,
        },
    },
    "deepseek-v4-pro": {
        "model_type": "deepseek_v4",
        "tokenizer_repo": "deepseek-ai/DeepSeek-V4-Pro-0813",
        "config": {
            "architectures": ["DeepseekV4ForCausalLM"],
            "attention_bias": False,
            "attention_dropout": 0.0,
            "bos_token_id": 0,
            "eos_token_id": 1,
            "expert_dtype": "fp4",
            "hc_eps": 1e-6,
            "hc_mult": 4,
            "hc_sinkhorn_iters": 20,
            "head_dim": 512,
            "hidden_act": "silu",
            "hidden_size": 7168,
            "index_head_dim": 128,
            "index_n_heads": 64,
            "index_topk": 1024,
            "initializer_range": 0.02,
            "max_position_embeddings": 1048576,
            "model_type": "deepseek_v4",
            "moe_intermediate_size": 3072,
            "n_routed_experts": 384,
            "n_shared_experts": 1,
            "norm_topk_prob": True,
            "num_attention_heads": 128,
            "num_experts_per_tok": 6,
            "num_hidden_layers": 61,
            "num_hash_layers": 3,
            "num_key_value_heads": 1,
            "num_nextn_predict_layers": 1,
            "o_groups": 16,
            "o_lora_rank": 1024,
            "q_lora_rank": 1536,
            "qk_rope_head_dim": 64,
            "quantization_config": {
                "activation_scheme": "dynamic",
                "fmt": "e4m3",
                "quant_method": "fp8",
                "scale_fmt": "ue8m0",
                "weight_block_size": [128, 128],
            },
            "rms_norm_eps": 1e-6,
            "rope_scaling": {
                "beta_fast": 32,
                "beta_slow": 1,
                "factor": 16,
                "original_max_position_embeddings": 65536,
                "type": "yarn",
            },
            "rope_theta": 10000,
            "routed_scaling_factor": 2.5,
            "scoring_func": "sqrtsoftplus",
            "sliding_window": 128,
            "swiglu_limit": 10.0,
            "tie_word_embeddings": False,
            "topk_method": "noaux_tc",
            "torch_dtype": "bfloat16",
            "use_cache": True,
            "vocab_size": 129280,
            "compress_rope_theta": 160000,
            "compress_ratios": [128, 128, 4, 128, 4, 128, 4, 128, 4, 128, 4,
                                128, 4, 128, 4, 128, 4, 128, 4, 128, 4, 128,
                                4, 128, 4, 128, 4, 128, 4, 128, 4, 128, 4,
                                128, 4, 128, 4, 128, 4, 128, 4, 128, 4, 128,
                                4, 128, 4, 128, 4, 128, 4, 128, 4, 128, 4,
                                128, 4, 128, 4, 128, 4, 128, 4, 128, 4, 0, 0, 0],
            "dspark_block_size": 5,
            "dspark_noise_token_id": 128799,
            "dspark_target_layer_ids": [58, 59, 60],
            "dspark_markov_rank": 512,
        },
    },
}


def _qwen3_profile(profile_id, repo, hidden_size, intermediate_size,
                   num_hidden_layers, num_attention_heads, tie_word_embeddings):
    """Build an embedded Qwen3 config from the official model-card config."""
    return {
        "model_type": "qwen3",
        "tokenizer_repo": repo,
        "config": {
            "architectures": ["Qwen3ForCausalLM"],
            "attention_bias": False,
            "attention_dropout": 0.0,
            "bos_token_id": 151643,
            "eos_token_id": 151645,
            "head_dim": 128,
            "hidden_act": "silu",
            "hidden_size": hidden_size,
            "initializer_range": 0.02,
            "intermediate_size": intermediate_size,
            "max_position_embeddings": 40960,
            "max_window_layers": num_hidden_layers,
            "model_type": "qwen3",
            "num_attention_heads": num_attention_heads,
            "num_hidden_layers": num_hidden_layers,
            "num_key_value_heads": 8,
            "rms_norm_eps": 1e-6,
            "rope_scaling": None,
            "rope_theta": 1000000,
            "sliding_window": None,
            "tie_word_embeddings": tie_word_embeddings,
            "torch_dtype": "bfloat16",
            "use_cache": True,
            "use_sliding_window": False,
            "vocab_size": 151936,
        },
    }


# Qwen3 is released in several Apache-2.0 sizes. Keep their official
# structural values here so a renamed single .safetensors file can still be
# matched without a neighboring config.json. The 8B profile above is retained
# for backward compatibility with existing settings.
FIXED_MODEL_PROFILES.update({
    "qwen3-0.6b": _qwen3_profile("qwen3-0.6b", "Qwen/Qwen3-0.6B", 1024, 3072, 28, 16, True),
    "qwen3-1.7b": _qwen3_profile("qwen3-1.7b", "Qwen/Qwen3-1.7B", 2048, 6144, 28, 16, True),
    "qwen3-4b": _qwen3_profile("qwen3-4b", "Qwen/Qwen3-4B", 2560, 9728, 36, 32, True),
    "qwen3-14b": _qwen3_profile("qwen3-14b", "Qwen/Qwen3-14B", 5120, 17408, 40, 40, False),
    "qwen3-32b": _qwen3_profile("qwen3-32b", "Qwen/Qwen3-32B", 5120, 25600, 64, 64, False),
    "mistral-7b-instruct-v0.3": {
        "model_type": "mistral",
        "tokenizer_repo": "mistralai/Mistral-7B-Instruct-v0.3",
        "config": {
            "architectures": ["MistralForCausalLM"],
            "attention_dropout": 0.0,
            "bos_token_id": 1,
            "eos_token_id": 2,
            "hidden_act": "silu",
            "hidden_size": 4096,
            "initializer_range": 0.02,
            "intermediate_size": 14336,
            "max_position_embeddings": 32768,
            "model_type": "mistral",
            "num_attention_heads": 32,
            "num_hidden_layers": 32,
            "num_key_value_heads": 8,
            "rms_norm_eps": 1e-5,
            "rope_theta": 1000000.0,
            "sliding_window": None,
            "tie_word_embeddings": False,
            "torch_dtype": "bfloat16",
            "use_cache": True,
            "vocab_size": 32768,
        },
    },
})


def normalize_config(value=None, existing=None):
    data = {**DEFAULTS, **(existing or {}), **(value if isinstance(value, dict) else {})}
    def integer(key, low, high):
        try:
            return min(high, max(low, int(data[key])))
        except (ValueError, TypeError):
            return DEFAULTS[key]
    return {"model": str(data.get("model") or ""), "mmproj": str(data.get("mmproj") or ""),
            "device": data.get("device") if data.get("device") in {"auto", "cpu", "cuda"} else "auto",
            "max_tokens": integer("max_tokens", 1, 16384),
            "context_length": integer("context_length", 2048, 131072)}


def model_roots():
    import folder_paths
    roots = [Path(folder_paths.models_dir) / "llm"]
    # Honor extra_model_paths.yaml instead of assuming the custom-node's location.
    registered = folder_paths.folder_names_and_paths.get("llm", ([], set()))[0]
    roots.extend(Path(p) for p in registered)
    parent = Path(folder_paths.models_dir)
    if parent.is_dir():
        roots.extend(p for p in parent.iterdir() if p.is_dir() and p.name.casefold() == "llm")
    return list(dict.fromkeys(p.resolve() for p in roots))


def _is_projector(path):
    return any(word in path.name.lower() for word in ("mmproj", "mm-projector", "projector"))


def _gguf_metadata(path):
    """Read the small leading metadata section, never the model tensors."""
    scalar = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i",
              6: "f", 7: "?", 10: "Q", 11: "q", 12: "d"}
    metadata = {}
    try:
        with path.open("rb") as handle:
            def unpack(fmt):
                return struct.unpack("<" + fmt, handle.read(struct.calcsize(fmt)))[0]
            def string():
                size = unpack("Q")
                if size > 65536:
                    raise ValueError("GGUF metadata string too large")
                return handle.read(size).decode("utf-8", errors="replace")
            def value(kind):
                if kind == 8:
                    return string()
                if kind in scalar:
                    return unpack(scalar[kind])
                if kind == 9:
                    subtype, count = unpack("I"), unpack("Q")
                    if count > 256:
                        raise ValueError("GGUF metadata array too large")
                    return [value(subtype) for _ in range(count)]
                raise ValueError("Unsupported GGUF metadata type")
            if handle.read(4) != b"GGUF" or unpack("I") not in (2, 3):
                return metadata
            unpack("Q")  # Tensor count; tensor data is never read.
            count = unpack("Q")
            for _ in range(min(count, 128)):
                key = string()
                if key.startswith("tokenizer."):
                    break
                metadata[key] = value(unpack("I"))
    except (OSError, ValueError, struct.error):
        pass
    return metadata


def _safetensors_header(path):
    """Read the safetensors header; never load the weight tensors."""
    try:
        with path.open("rb") as handle:
            header_size_bytes = handle.read(8)
            if len(header_size_bytes) != 8:
                return {}
            header_size = struct.unpack("<Q", header_size_bytes)[0]
            # A corrupt file must not make a model scan allocate unbounded memory.
            if header_size > 16 * 1024 * 1024:
                return {}
            header = json.loads(handle.read(header_size).decode("utf-8"))
            return header if isinstance(header, dict) else {}
    except (OSError, ValueError, UnicodeError, struct.error):
        return {}


def _safetensors_metadata(path):
    header = _safetensors_header(path)
    values = header.get("__metadata__")
    return values if isinstance(values, dict) else {}


def _header_model_shape(header):
    entry = header.get("model.embed_tokens.weight") or header.get("transformer.embeddings.word_embeddings.weight")
    shape = entry.get("shape") if isinstance(entry, dict) else None
    return tuple(shape) if isinstance(shape, list) and all(isinstance(v, int) for v in shape) else ()


def _header_tensor_shape(header, name):
    entry = header.get(name)
    shape = entry.get("shape") if isinstance(entry, dict) else None
    return tuple(shape) if isinstance(shape, list) and all(isinstance(v, int) for v in shape) else ()


def _header_layer_count(header):
    indexes = []
    for name in header:
        match = re.search(r"(?:^|\.)layers\.(\d+)\.", name)
        if match:
            indexes.append(int(match.group(1)))
    return max(indexes) + 1 if indexes else 0


def _qwen3_structure_matches(profile, header):
    config = profile["config"]
    shape = _header_model_shape(header)
    if shape != (config["vocab_size"], config["hidden_size"]):
        return False
    if _header_layer_count(header) != config["num_hidden_layers"]:
        return False
    q_shape = _header_tensor_shape(header, "model.layers.0.self_attn.q_proj.weight")
    k_shape = _header_tensor_shape(header, "model.layers.0.self_attn.k_proj.weight")
    v_shape = _header_tensor_shape(header, "model.layers.0.self_attn.v_proj.weight")
    if q_shape != (config["num_attention_heads"] * config["head_dim"], config["hidden_size"]):
        return False
    if k_shape != (config["num_key_value_heads"] * config["head_dim"], config["hidden_size"]):
        return False
    if v_shape != k_shape:
        return False
    return all(name in header for name in (
        "model.layers.0.input_layernorm.weight",
        "model.layers.0.self_attn.o_proj.weight",
        "model.layers.0.mlp.gate_proj.weight",
        "model.layers.0.mlp.down_proj.weight",
        "model.layers.0.mlp.up_proj.weight",
    ))


def _standard_decoder_structure_matches(profile, header):
    """Match ordinary split-QKV decoder weights such as Mistral."""
    config = profile["config"]
    shape = _header_model_shape(header)
    if shape != (config["vocab_size"], config["hidden_size"]):
        return False
    if _header_layer_count(header) != config["num_hidden_layers"]:
        return False
    head_dim = config["hidden_size"] // config["num_attention_heads"]
    q_shape = _header_tensor_shape(header, "model.layers.0.self_attn.q_proj.weight")
    k_shape = _header_tensor_shape(header, "model.layers.0.self_attn.k_proj.weight")
    v_shape = _header_tensor_shape(header, "model.layers.0.self_attn.v_proj.weight")
    if q_shape != (config["num_attention_heads"] * head_dim, config["hidden_size"]):
        return False
    if k_shape != (config["num_key_value_heads"] * head_dim, config["hidden_size"]):
        return False
    if v_shape != k_shape:
        return False
    return all(name in header for name in (
        "model.layers.0.input_layernorm.weight",
        "model.layers.0.self_attn.o_proj.weight",
        "model.layers.0.mlp.gate_proj.weight",
        "model.layers.0.mlp.down_proj.weight",
        "model.layers.0.mlp.up_proj.weight",
    ))


def _deepseek_v4_structure_matches(profile, header):
    config = profile["config"]
    shape = _header_model_shape(header)
    if shape != (config["vocab_size"], config["hidden_size"]):
        return False
    if _header_layer_count(header) != config["num_hidden_layers"]:
        return False
    q_shape = _header_tensor_shape(header, "model.layers.0.self_attn.q_a_proj.weight")
    if q_shape != (config["q_lora_rank"], config["hidden_size"]):
        return False
    return all(name in header for name in (
        "model.layers.0.input_layernorm.weight",
        "model.layers.0.self_attn.kv_a_proj_with_mqa.weight",
        "model.layers.0.self_attn.o_proj.weight",
    ))


def _fixed_profile_for(path, header):
    """Choose a built-in profile from exact tensor structure, never its filename."""
    metadata = header.get("__metadata__") if isinstance(header, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    model_type = str(metadata.get("model_type") or metadata.get("general.architecture") or "").lower()
    if model_type in {"deepseek_v4", "deepseek-v4"}:
        for profile_id in ("deepseek-v4-flash", "deepseek-v4-pro"):
            profile = FIXED_MODEL_PROFILES[profile_id]
            if _deepseek_v4_structure_matches(profile, header):
                return profile
        return None
    for profile_id, profile in FIXED_MODEL_PROFILES.items():
        if profile["model_type"] == "qwen3" and _qwen3_structure_matches(profile, header):
            if model_type and model_type not in {"qwen3", "qwen3forcausallm"}:
                return None
            return profile
        if profile["model_type"] in {"mistral", "llama"} and _standard_decoder_structure_matches(profile, header):
            if model_type and model_type not in {profile["model_type"], f"{profile['model_type']}forcausallm"}:
                return None
            return profile
    return None


def _fixed_profile_runtime_available(profile):
    if profile["model_type"] != "deepseek_v4":
        return True
    try:
        spec = importlib.util.find_spec("transformers")
        locations = list(spec.submodule_search_locations or []) if spec else []
        return any((Path(location) / "models" / "deepseek_v4").is_dir() for location in locations)
    except (ImportError, ModuleNotFoundError, AttributeError, TypeError, OSError):
        return False


def _handler(name):
    if re.search(r"qwen[-_. ]?3[._-]?(?:5|6|8)", name.lower()):
        return "Qwen35ChatHandler"
    if "qwen3-vl" in name.lower() or "qwen3vl" in name.lower():
        return "Qwen3VLChatHandler"
    return ""


def _identity(name):
    text = Path(name).stem.lower()
    text = re.sub(r"(?:mmproj|mm-projector|projector)[-_. ]*", "", text)
    text = re.sub(r"[-_.](?:i?q\d+.*|bf16|fp16|f16|f32)$", "", text)
    return re.sub(r"[^a-z0-9]", "", text)


def matching_projector(model, projectors):
    identity = _identity(model)
    # Filename matching is a suggestion; users can explicitly select a projector.
    scored = [(SequenceMatcher(None, identity, _identity(p["id"])).ratio(), p["id"])
              for p in projectors
              if not any(token in identity and token not in _identity(p["id"])
                         for token in ("i2i", "t2i"))]
    if not scored:
        return ""
    best = max(score for score, _ in scored)
    matches = [name for score, name in scored if score == best and score >= .72]
    return matches[0] if len(matches) == 1 else ""


def scan_models():
    models, projectors = [], []
    seen = set()
    for root_index, root in enumerate(model_roots()):
        if not root.is_dir():
            continue
        candidates = (list(root.rglob("*.gguf"))
                      + [p.parent for p in root.rglob("config.json")]
                      + [p for p in root.rglob("*.safetensors") if p.is_file()])
        for path in sorted(candidates):
            path = path.resolve()
            # Do not expose a symlink outside an explicitly configured model root.
            if not path.is_relative_to(root) or path in seen:
                continue
            # A complete Transformers directory is represented by its directory
            # record below. Do not also list each shard in that directory.
            if path.is_file() and (path.parent / "config.json").is_file():
                continue
            seen.add(path)
            relative = path.relative_to(root).as_posix()
            identifier = relative if root_index == 0 else f"root{root_index}/{relative}"
            record = {"id": identifier, "name": relative, "path": str(path),
                      "format": "gguf" if path.suffix.lower() == ".gguf" else (
                          "safetensors" if path.is_file() else "transformers")}
            if record["format"] == "gguf":
                if _is_projector(path):
                    projectors.append(record)
                    continue
                metadata = _gguf_metadata(path)
                architecture = str(metadata.get("general.architecture") or "").lower()
                record["architecture"] = architecture
                if architecture in {"clip", "flux", "sd1", "sd2", "sdxl", "wan", "qwen_image", "qwen-image"} or metadata.get("general.type") == "mmproj":
                    continue
                tags = metadata.get("general.tags") or []
                record["vision"] = architecture in {"qwen3vl", "qwen2vl"} or "image-editing" in tags
                record["available"] = True
            elif record["format"] == "safetensors":
                header = _safetensors_header(path)
                metadata = header.get("__metadata__") if isinstance(header, dict) else {}
                metadata = metadata if isinstance(metadata, dict) else {}
                profile = _fixed_profile_for(path, header)
                record["architecture"] = str(metadata.get("general.architecture") or (
                    profile["config"].get("model_type") if profile else "")).lower()
                record["vision"] = False
                if profile:
                    record["profile"] = next(key for key, value in FIXED_MODEL_PROFILES.items()
                                             if value is profile)
                    if _fixed_profile_runtime_available(profile):
                        record["available"] = True
                        record["status"] = "fixed_profile"
                    else:
                        record["available"] = False
                        record["status"] = "unsupported_runtime"
                        record["reason"] = "当前 Transformers 未提供 DeepSeek-V4 模型实现；请安装包含 transformers/models/deepseek_v4 的版本"
                else:
                    record["available"] = False
                    record["status"] = "metadata_only"
                    record["reason"] = "无法根据权重结构匹配内置架构；请使用 Qwen3、Mistral 的内置配置，或放入带 config.json 的完整 Transformers 模型目录"
            else:
                try:
                    config = json.loads((path / "config.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                record["vision"] = bool(config.get("vision_config") or any(
                    word in json.dumps(config).lower() for word in ("vision", "visual", "gemma3", "minicpm-v", "internvl")))
                if not any(path.glob("*.safetensors")) and not any(path.glob("pytorch_model*.bin")):
                    continue
                record["available"] = True
            models.append(record)
    for model in models:
        suggestion = matching_projector(model["id"], projectors)
        if model["format"] == "gguf" and suggestion and _handler(model.get("architecture", "") or model["name"]):
            model["vision"] = True
        model["suggested_mmproj"] = suggestion if model["vision"] else ""
    return models, projectors


def catalog():
    models, projectors = scan_models()
    required = ("torch", "transformers", "accelerate", "PIL", "safetensors")
    return {"models": [{k: v for k, v in m.items() if k != "path"} for m in models],
            "projectors": [{k: v for k, v in m.items() if k != "path"} for m in projectors],
            "missing_transformers": [n for n in required if importlib.util.find_spec(n) is None],
            "gguf_available": importlib.util.find_spec("llama_cpp") is not None}


def provider(config):
    models, _ = scan_models()
    local = normalize_config(config.get("local"))
    runnable = [m for m in models if m.get("available", True)]
    return {"id": "local", "name": "本地模型", "description": "Transformers / GGUF",
            "api_format": "local", "api_url": "", "api_key": "", "builtin": True,
            "llm_models": [m["id"] for m in runnable if not m["vision"]],
            "vlm_models": [m["id"] for m in runnable if m["vision"]],
            "llm_model": local["model"], "vlm_model": local["model"], **local}


def workflow_running():
    from server import PromptServer
    queue = getattr(getattr(PromptServer, "instance", None), "prompt_queue", None)
    if queue is None:
        return False
    running, _ = queue.get_current_queue()
    return bool(running)


def _images(media_parts):
    from PIL import Image
    images = []
    for part in media_parts:
        url = part.get("image_url", {}).get("url", "")
        if url.startswith("data:") and "," in url:
            with Image.open(BytesIO(base64.b64decode(url.split(",", 1)[1]))) as image:
                images.append(image.convert("RGB"))
    return images


def _fixed_profile(record):
    profile_id = str(record.get("profile") or "")
    return FIXED_MODEL_PROFILES.get(profile_id)


def _tokenizer(record, profile):
    from transformers import AutoTokenizer

    path = Path(record["path"])
    sources = []
    if record["format"] == "transformers":
        sources.append(str(path))
    else:
        # Some distributions place tokenizer files beside the single weight;
        # others use a shared tokenizers folder under models/llm.
        sources.extend([str(path.parent), str(path.parent / "tokenizer")])
        try:
            for root in model_roots():
                sources.append(str(root / "tokenizers" / record["profile"]))
        except (ImportError, OSError):
            pass
    tried = set()
    for source in sources:
        if source in tried or not Path(source).is_dir():
            continue
        tried.add(source)
        try:
            return AutoTokenizer.from_pretrained(source, local_files_only=True,
                                                 trust_remote_code=True)
        except (OSError, ValueError, ImportError):
            continue
    if profile and profile.get("tokenizer_repo"):
        try:
            # Tokenizers are small compared with model weights and are cached by
            # Transformers. This is the only network fallback for fixed profiles.
            return AutoTokenizer.from_pretrained(profile["tokenizer_repo"],
                                                 local_files_only=False,
                                                 trust_remote_code=True)
        except (OSError, ValueError, ImportError) as exc:
            raise RuntimeError(
                f"裸权重已识别，但缺少 tokenizer；请联网下载 {profile['tokenizer_repo']} "
                "或把 tokenizer 文件放到该权重旁边"
            ) from exc
    raise RuntimeError("本地模型缺少 tokenizer 文件")


def _load_fixed_safetensors(record, device, torch):
    from accelerate import init_empty_weights, load_checkpoint_and_dispatch
    from transformers import AutoConfig, AutoModelForCausalLM

    profile = _fixed_profile(record)
    if profile is None:
        raise RuntimeError("无法匹配内置 safetensors 模型架构")
    config_data = dict(profile["config"])
    config_data.pop("model_type", None)
    model_config = AutoConfig.for_model(profile["model_type"], **config_data)
    dtype = torch.float16 if device == "cuda" else torch.float32
    with init_empty_weights():
        model = AutoModelForCausalLM.from_config(model_config, torch_dtype=dtype)
    device_map = "auto" if device == "cuda" else {"": "cpu"}
    return load_checkpoint_and_dispatch(model, record["path"], device_map=device_map,
                                        dtype=dtype, offload_state_dict=True)


def _transformers_generate(record, config, system, prompt, media_parts):
    import torch
    from transformers import AutoProcessor, AutoTokenizer, AutoModelForCausalLM
    model = processor = inputs = output = None
    try:
        device = config["device"]
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA 不可用，请选择 CPU 或 Auto")
        images = _images(media_parts) if record["vision"] else []
        profile = _fixed_profile(record)
        if record["format"] == "safetensors":
            processor = _tokenizer(record, profile)
            text = processor.apply_chat_template([{"role": "system", "content": system},
                    {"role": "user", "content": prompt}], tokenize=False, add_generation_prompt=True)
            inputs = processor(text, return_tensors="pt")
            model = _load_fixed_safetensors(record, device, torch)
        elif record["vision"]:
            from transformers import AutoModelForImageTextToText
            processor = AutoProcessor.from_pretrained(record["path"], local_files_only=True, trust_remote_code=True)
            content = [{"type": "text", "text": prompt}] + [{"type": "image", "image": image} for image in images]
            messages = [{"role": "system", "content": [{"type": "text", "text": system}]},
                        {"role": "user", "content": content}]
            text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            inputs = processor(text=[text], images=images or None, padding=True, return_tensors="pt")
            model_class = AutoModelForImageTextToText
        else:
            processor = AutoTokenizer.from_pretrained(record["path"], local_files_only=True, trust_remote_code=True)
            text = processor.apply_chat_template([{"role": "system", "content": system},
                    {"role": "user", "content": prompt}], tokenize=False, add_generation_prompt=True)
            inputs = processor(text, return_tensors="pt")
            model_class = AutoModelForCausalLM
        if record["format"] != "safetensors":
            model = model_class.from_pretrained(record["path"], local_files_only=True, trust_remote_code=True,
                torch_dtype=torch.float16 if device == "cuda" else torch.float32,
                device_map="auto" if device == "cuda" else None)
        if device == "cpu":
            model.to("cpu")
        input_length = inputs["input_ids"].shape[1]
        model_limit = getattr(model.config, "max_position_embeddings", None)
        text_config = getattr(model.config, "text_config", None)
        if text_config is not None:
            model_limit = getattr(text_config, "max_position_embeddings", model_limit)
        limit = min(config["context_length"], model_limit) if isinstance(model_limit, int) else config["context_length"]
        if input_length + config["max_tokens"] > limit:
            raise ValueError(f"本地 LLM 上下文不足：输入 {input_length} + 输出上限 {config['max_tokens']} > {limit}；请减少媒体/规则或调整输出上限")
        target = model.get_input_embeddings().weight.device
        inputs = {k: v.to(target) if hasattr(v, "to") else v for k, v in inputs.items()}
        with torch.inference_mode():
            output = model.generate(**inputs, max_new_tokens=config["max_tokens"], do_sample=False)
        generated = output[:, input_length:]
        return processor.batch_decode(generated, skip_special_tokens=True)[0].strip()
    finally:
        model = processor = inputs = output = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def _gguf_generate(record, config, projector, system, prompt, media_parts):
    try:
        from llama_cpp import Llama
    except ImportError as exc:
        raise RuntimeError("GGUF 需要与当前 Python/CUDA 匹配的 llama-cpp-python；未安装不会影响 API 模式") from exc
    handler = model = None
    try:
        if record["vision"]:
            import llama_cpp.llama_chat_format as formats
            handler_name = _handler(record.get("architecture", "") or Path(record["path"]).name)
            handler_class = getattr(formats, handler_name, None)
            if handler_class is None:
                raise RuntimeError("当前 llama-cpp-python 不支持此 Qwen 视觉模型，请安装带对应 Qwen handler 的版本")
            if projector is None:
                raise ValueError("GGUF 视觉模型需要匹配的 mmproj，请在本地模型设置中选择")
            kwargs = {"clip_model_path": projector["path"], "verbose": False}
            if handler_name == "Qwen35ChatHandler":
                kwargs["enable_thinking"] = False
            else:
                kwargs["force_reasoning"] = False
            handler = handler_class(**kwargs)
        use_gpu = config["device"] != "cpu"
        if config["device"] == "auto":
            import torch
            use_gpu = torch.cuda.is_available()
        model = Llama(model_path=record["path"], chat_handler=handler,
                      n_gpu_layers=-1 if use_gpu else 0, n_ctx=config["context_length"], verbose=False)
        content = [{"type": "text", "text": prompt}]
        if record["vision"]:
            content.extend(media_parts)
        result = model.create_chat_completion(messages=[{"role": "system", "content": system},
            {"role": "user", "content": content if record["vision"] else prompt}],
            max_tokens=config["max_tokens"], temperature=.2, top_p=.9)
        return str(result["choices"][0]["message"]["content"] or "").strip()
    finally:
        try:
            if model is not None:
                model.close()
        finally:
            stack = getattr(handler, "_exit_stack", None)
            if stack is not None:
                stack.close()


def generate(config, selected, system, prompt, media_parts, *, manual=False):
    """Serialize complete requests and release the local model before H3 continues."""
    config = normalize_config(config)
    models, projectors = scan_models()
    record = next((m for m in models if m["id"] == selected), None)
    if record is None:
        raise ValueError("所选本地模型已不存在，请刷新本地模型列表")
    if not record.get("available", True):
        raise ValueError(record.get("reason") or "所选本地模型缺少可加载所需的完整文件")
    projector_id = config["mmproj"] or record.get("suggested_mmproj", "")
    projector = next((m for m in projectors if m["id"] == projector_id), None)
    if not _LOCK.acquire(blocking=False):
        raise RuntimeError("已有本地提示词优化正在执行，请等待完成")
    try:
        if manual and workflow_running():
            raise RuntimeError("工作流正在执行，请完成后再使用本地提示词优化")
        from comfy import model_management
        model_management.unload_all_models()
        gc.collect()
        model_management.soft_empty_cache()
        try:
            if record["format"] == "gguf":
                result = _gguf_generate(record, config, projector, system, prompt, media_parts)
            else:
                result = _transformers_generate(record, config, system, prompt, media_parts)
            if not result.strip():
                raise RuntimeError("本地模型返回了空提示词")
            return result
        finally:
            gc.collect()
            model_management.soft_empty_cache()
            # Transformers can leave a model registered in ComfyUI's model
            # management even after its Python reference is released. Match
            # the Goohai request lifecycle so H3 loading starts from a clean
            # cache when local optimization is used from the node.
            model_management.unload_all_models()
    finally:
        _LOCK.release()
