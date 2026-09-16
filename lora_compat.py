"""Preflight standard LoRA dimensions without materializing model weights."""
import logging

import comfy.lora
import comfy.lora_convert


def compatible_lora(model, tensors, name):
    # Convert a shallow copy: never modify the raw LoRA cache shared by branches.
    tensors = comfy.lora_convert.convert_lora(dict(tensors))
    mapping = comfy.lora.model_lora_keys_unet(model.model, {})
    parsed = comfy.lora.load_lora(tensors, mapping)
    state = model.model.state_dict()
    rejected = {}
    accepted = 0
    unchecked = 0
    for target, adapter in parsed.items():
        key = target if isinstance(target, str) else target[0]
        if key not in state:
            rejected[target] = "target weight not found"
            continue
        shape = tuple(state[key].shape)
        if getattr(adapter, "name", None) == "lora" and len(shape) == 2:
            up, down, _alpha, mid, _dora, reshape = adapter.weights
            up_shape, down_shape = tuple(up.shape), tuple(down.shape)
            # H3 transformer LoRAs are linear. Reject resizing/LoCon adapters
            # here instead of letting their bypass forward change layer shape.
            valid = (len(up_shape) == len(down_shape) == 2
                     and mid is None and (reshape is None or tuple(reshape) == shape)
                     and up_shape[1] == down_shape[0]
                     and (up_shape[0], down_shape[1]) == shape)
            if not valid:
                rejected[target] = f"model={shape}, LoRA up={up_shape}, down={down_shape}"
                continue
        else:
            # Preserve core support for other adapter types; do not claim that
            # this linear-LoRA check proves their compatibility.
            unchecked += 1
        accepted += 1
    for target, reason in rejected.items():
        logging.warning("Easy H3 LoRA %s: skipped incompatible layer %s: %s", name, target, reason)
    if not accepted:
        raise ValueError(f"LoRA {name}: 没有可加载的匹配层 / no compatible layers "
                         f"(skipped={len(rejected)}). 请检查模型与 LoRA 是否匹配。")
    if unchecked:
        logging.warning("Easy H3 LoRA %s: %d non-linear/other patches delegated to ComfyUI; dimensions not prevalidated",
                        name, unchecked)
    if rejected:
        # Adapter.loaded_keys may be a shared accumulator in ComfyUI. Filter
        # using exact source prefixes instead, never that shared key set.
        prefixes = tuple(prefix for source, target in mapping.items() if target in rejected
                         for prefix in (source + ".", source + "_lora."))
        tensors = {key: value for key, value in tensors.items()
                   if not key.startswith(prefixes)}
        logging.warning("Easy H3 LoRA %s: PARTIAL LOAD / 部分加载: matched=%d skipped=%d; effect may differ from the complete LoRA",
                        name, accepted, len(rejected))
    else:
        logging.info("Easy H3 LoRA %s: matched=%d skipped=0", name, accepted)
    return tensors
