"""Joint context-window sampling for long H3 motion-transfer blocks.

A long block (e.g. a 15-45 s dance driven by reference video 1) is denoised
in ONE sampler run.  At every step the full latent is cut into overlapping
windows; each window is predicted with its own conditioning (reference video
and its soundtrack cut to that window, reference pictures and prompt as
always), and the predictions are blended over the overlaps with linear ramps
and normalised by the summed weights.  No window ever conditions on another
window's decoded output, so quality does not compound from segment to segment
the way the sequential tail-guide continuation does.

H3 packs text, references and the target into one token sequence (see
``comfy.model_base.MiniMaxH3.extra_conds``), so a window is not a slice of the
full conditioning: every window gets a fully rebuilt conditioning and its own
``process_conds`` at the window's latent shape.  Hence a guider wrapper rather
than ComfyUI's generic context-window handler.

Timeline geometry (``comfy_extras.nodes_minimax_h3``): a clip of 5 + 17N
frames has 2 + 5N video latent frames — every 17 frames are one VAE clip of
5 tokens (spans 1,4,4,4,4 frames), the last clip drops 3 tokens.  Windows
therefore start on a 17-frame group (token 5g) and are 5 + 17M frames long, so
each window has exactly the token layout of a standalone generation.  Audio
runs at 40 latent frames/s (5/3 per video frame).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import torch

GROUP_FRAMES = 17
GROUP_TOKENS = 5
AUDIO_PER_FRAME = 40.0 / 24.0
# Conditioning metadata key that carries the per-window conditionings.
WINDOWS_KEY = "feihou_h3_windows"


def groups_of(frames: int) -> int:
    """17-frame groups of a 5 + 17N clip (N)."""
    frames = int(frames)
    if frames < 5 or (frames - 5) % GROUP_FRAMES:
        raise ValueError(f"H3 上下文窗口：{frames} 帧不是 5+17N。")
    return (frames - 5) // GROUP_FRAMES


def frames_of(groups: int) -> int:
    return 5 + GROUP_FRAMES * int(groups)


def video_tokens(frames: int) -> int:
    return 2 + GROUP_TOKENS * groups_of(frames)


def audio_tokens(frames: int) -> int:
    """Same rounding as ``nodes_minimax_h3.temporal_shape``."""
    return round(int(frames) / 24.0 * 40.0)


@dataclass(frozen=True)
class Window:
    """One context window; frames are relative to the sampled clip's start."""

    frame_start: int
    frames: int
    video_start: int
    video_len: int
    audio_start: int
    audio_len: int

    @property
    def video_end(self) -> int:
        return self.video_start + self.video_len

    @property
    def audio_end(self) -> int:
        return self.audio_start + self.audio_len


def plan_windows(total_frames: int, window_frames: int, overlap_frames: int) -> list[Window]:
    """Evenly spaced, group-aligned windows covering a 5 + 17N clip.

    ``window_frames`` / ``overlap_frames`` are rounded down to whole 17-frame
    groups (window at least 2 groups, overlap at least 1 and less than the
    window).  The first window starts at frame 0 and the last ends at the
    clip's last frame; starts in between are spread evenly, so every overlap is
    at least ``overlap_frames``.  A clip no longer than one window is one
    window.
    """
    total_groups = groups_of(total_frames)
    window_groups = max(2, (max(5, int(window_frames)) - 5) // GROUP_FRAMES)
    overlap_groups = max(1, int(overlap_frames) // GROUP_FRAMES)
    if overlap_groups >= window_groups:
        raise ValueError(f"H3 上下文窗口：重叠 {overlap_frames} 帧必须小于窗口 {window_frames} 帧（按 17 帧取整后）。")
    full_audio = audio_tokens(total_frames)
    if total_groups <= window_groups:
        return [Window(0, int(total_frames), 0, video_tokens(total_frames), 0, full_audio)]
    stride = window_groups - overlap_groups
    count = 1 + math.ceil((total_groups - window_groups) / stride)
    span = total_groups - window_groups
    starts = [round(index * span / (count - 1)) for index in range(count)]
    frames = frames_of(window_groups)
    length_v, length_a = video_tokens(frames), audio_tokens(frames)
    windows = []
    for group in starts:
        frame_start = GROUP_FRAMES * group
        audio_start = min(round(frame_start * AUDIO_PER_FRAME), full_audio - length_a)
        windows.append(Window(frame_start, frames, GROUP_TOKENS * group, length_v, audio_start, length_a))
    return windows


def _ramp(length: int, overlap: int, rising: bool) -> torch.Tensor:
    """Weights over ``length`` with a linear ramp across the first/last ``overlap`` entries.

    Ramp values are (i + 1) / (overlap + 1): never 0, and a rising and a
    falling ramp over the same overlap sum to exactly 1.
    """
    weights = torch.ones(length, dtype=torch.float32)
    overlap = max(0, min(int(overlap), length))
    if overlap:
        ramp = torch.arange(1, overlap + 1, dtype=torch.float32) / (overlap + 1)
        if rising:
            weights[:overlap] = ramp
        else:
            weights[length - overlap:] = ramp.flip(0)
    return weights


def window_weights(windows: list[Window], stream: str) -> list[torch.Tensor]:
    """Per-window blend weights along time for ``stream`` "video" or "audio"."""
    def bounds(window):
        if stream == "video":
            return window.video_start, window.video_end
        return window.audio_start, window.audio_end

    out = []
    for index, window in enumerate(windows):
        start, end = bounds(window)
        weights = torch.ones(end - start, dtype=torch.float32)
        if index > 0:
            weights = torch.minimum(weights, _ramp(end - start, bounds(windows[index - 1])[1] - start, True))
        if index + 1 < len(windows):
            weights = torch.minimum(weights, _ramp(end - start, end - bounds(windows[index + 1])[0], False))
        out.append(weights)
    return out


def blend(parts: list[torch.Tensor], windows: list[Window], weights: list[torch.Tensor], total: int,
          stream: str, dim: int) -> torch.Tensor:
    """Weighted average of per-window tensors placed on the full timeline along ``dim``."""
    first = parts[0]
    shape = list(first.shape)
    shape[dim] = int(total)
    out = torch.zeros(shape, dtype=torch.float32, device=first.device)
    counter = torch.zeros(int(total), dtype=torch.float32, device=first.device)
    view = [1] * len(shape)
    for part, window, weight in zip(parts, windows, weights):
        start = window.video_start if stream == "video" else window.audio_start
        weight = weight.to(first.device)
        view[dim] = int(weight.numel())
        out.narrow(dim, start, part.shape[dim]).add_(part.float() * weight.view(view))
        counter[start:start + part.shape[dim]] += weight
    if bool((counter <= 0).any()):
        raise ValueError("H3 上下文窗口：有时间位置没有被任何窗口覆盖。")
    view[dim] = int(total)
    return (out / counter.view(view)).to(first.dtype)


@dataclass
class WindowSet:
    """Everything the windowed guider needs; rides in the conditioning metadata."""

    windows: list
    conditionings: list
    total_frames: int

    @property
    def video_total(self) -> int:
        return video_tokens(self.total_frames)

    @property
    def audio_total(self) -> int:
        return audio_tokens(self.total_frames)


def windowed_conditioning(window_set: WindowSet):
    """The first window's conditioning, tagged with the whole window set."""
    import node_helpers
    return node_helpers.conditioning_set_values(window_set.conditionings[0], {WINDOWS_KEY: window_set})


def window_set_of(conditioning) -> WindowSet | None:
    for entry in conditioning or ():
        if isinstance(entry, (list, tuple)) and len(entry) > 1 and isinstance(entry[1], dict):
            found = entry[1].get(WINDOWS_KEY)
            if isinstance(found, WindowSet):
                return found
    return None


def decode_video_chunked(decode, video: torch.Tensor, groups_per_chunk: int = 21):
    """Decode a long H3 video latent in 17-frame-group-aligned pieces.

    The H3 VAE decodes every 5-token clip (+2 overlap tokens) independently,
    so a piece starting on token 5g decodes its first 17m frames exactly like
    the full latent would; only the last piece keeps its 5 trailing frames.
    ``decode(latent) -> [F, H, W, C]``.  Yields frame tensors in order.
    """
    total = int(video.shape[2])
    groups = (total - 2) // GROUP_TOKENS
    if (total - 2) % GROUP_TOKENS or groups < 1:
        yield decode(video)
        return
    step = max(1, int(groups_per_chunk))
    for group in range(0, groups, step):
        count = min(step, groups - group)
        piece = video[:, :, GROUP_TOKENS * group:GROUP_TOKENS * (group + count) + 2]
        frames = decode(piece)
        last = group + count >= groups
        yield frames if last else frames[:GROUP_FRAMES * count]


def _strip_canvas(conditioning, keys):
    return [[embedding, {k: v for k, v in metadata.items() if k not in keys}] for embedding, metadata in conditioning]


def make_guider(model, conditioning, cfg: float = 1.0, negative=None, canvas_keys=(), with_canvas=None):
    """A guider that samples ``conditioning``'s window set jointly."""
    import comfy.model_patcher
    import comfy.patcher_extension
    import comfy.sampler_helpers
    import comfy.samplers
    import comfy.utils

    window_set = window_set_of(conditioning)
    use_negative = negative is not None and float(cfg) > 1.0
    plain_negative = _strip_canvas(negative, set(canvas_keys) | {WINDOWS_KEY}) if use_negative else None

    class H3ContextWindowGuider(comfy.samplers.CFGGuider):
        """CFGGuider whose every model call is a blend of per-window calls."""

        def __init__(self, model_patcher):
            super().__init__(model_patcher)
            self.model_options = comfy.model_patcher.create_model_options_clone(model_patcher.model_options)
            self.window_set = window_set
            self.window_raw = []
            for cond in window_set.conditionings:
                entry = {"positive": cond}
                if plain_negative is not None:
                    entry["negative"] = with_canvas(plain_negative, cond) if with_canvas else plain_negative
                self.window_raw.append(entry)
            self.set_cfg(float(cfg) if use_negative else 1.0)
            # The outer conds (model loading, hooks) are the first window's.
            self.inner_set_conds(self.window_raw[0])
            ratio = window_set.windows[0].video_len / float(window_set.video_total)

            def window_memory(executor, model, noise_shape, conds, *args, **kwargs):
                # Memory is estimated for one window (the video dominates), not the whole clip.
                shape = list(noise_shape)
                shape[-1] = max(1, int(math.ceil(int(shape[-1]) * ratio)))
                return executor(model, tuple(shape), conds, *args, **kwargs)

            comfy.patcher_extension.add_wrapper_with_key(
                comfy.patcher_extension.WrappersMP.PREPARE_SAMPLING, "feihou_h3_context_windows", window_memory,
                self.model_options, is_model_options=True)

        def inner_sample(self, noise, latent_image, device, sampler, sigmas, denoise_mask, callback, disable_pbar,
                         seed, latent_shapes=None):
            if latent_shapes is None or len(latent_shapes) < 2:
                raise ValueError("H3 上下文窗口需要 H3 音视频 latent。")
            self.full_shapes = latent_shapes
            video_shape, audio_shape = latent_shapes[0], latent_shapes[1]
            if int(video_shape[2]) != self.window_set.video_total or int(audio_shape[-1]) != self.window_set.audio_total:
                raise ValueError(
                    f"H3 上下文窗口：latent 长度 {tuple(video_shape)} / {tuple(audio_shape)} 与窗口计划"
                    f"（视频 {self.window_set.video_total}、音频 {self.window_set.audio_total}）不一致。")
            self.inner_model.latent_shapes = latent_shapes
            if latent_image is not None and torch.count_nonzero(latent_image) > 0:
                latent_image = self.inner_model.process_latent_in(latent_image)

            def window_pack(packed, window):
                if packed is None:
                    return None, None
                video, audio = comfy.utils.unpack_latents(packed, latent_shapes)
                return comfy.utils.pack_latents([video[:, :, window.video_start:window.video_end],
                                                 audio[..., window.audio_start:window.audio_end]])

            self.runs = []
            for window, raw in zip(self.window_set.windows, self.window_raw):
                w_noise, w_shapes = window_pack(noise, window)
                w_latent, _ = window_pack(latent_image, window)
                w_mask, _ = window_pack(denoise_mask, window)
                conds = {key: [dict(item) for item in comfy.sampler_helpers.convert_cond(value)]
                         for key, value in raw.items()}
                conds = comfy.samplers.process_conds(self.inner_model, w_noise, conds, device, w_latent, w_mask, seed,
                                                     latent_shapes=w_shapes)
                self.runs.append((window, conds, w_shapes))
            # Restore the full shapes: audio scale and inpainting work on the whole clip.
            self.inner_model.latent_shapes = latent_shapes
            self.weights_v = window_weights(self.window_set.windows, "video")
            self.weights_a = window_weights(self.window_set.windows, "audio")
            self.conds = self.runs[0][1]

            extra_model_options = comfy.model_patcher.create_model_options_clone(self.model_options)
            extra_model_options.setdefault("transformer_options", {})["sample_sigmas"] = sigmas
            extra_args = {"model_options": extra_model_options, "seed": seed}
            executor = comfy.patcher_extension.WrapperExecutor.new_class_executor(
                sampler.sample, sampler,
                comfy.patcher_extension.get_all_wrappers(comfy.patcher_extension.WrappersMP.SAMPLER_SAMPLE,
                                                         extra_args["model_options"], is_model_options=True))
            samples = executor.execute(self, sigmas, extra_args, callback, noise, latent_image, denoise_mask,
                                       disable_pbar)
            return self.inner_model.process_latent_out(samples.to(torch.float32))

        def predict_noise(self, x, timestep, model_options={}, seed=None):
            video, audio = comfy.utils.unpack_latents(x, self.full_shapes)
            parts_v, parts_a = [], []
            for window, conds, shapes in self.runs:
                self.conds = conds
                packed, _ = comfy.utils.pack_latents([video[:, :, window.video_start:window.video_end],
                                                      audio[..., window.audio_start:window.audio_end]])
                out = comfy.samplers.sampling_function(self.inner_model, packed, timestep, conds.get("negative"),
                                                       conds.get("positive"), self.cfg,
                                                       model_options=model_options, seed=seed)
                out_v, out_a = comfy.utils.unpack_latents(out, shapes)
                parts_v.append(out_v)
                parts_a.append(out_a)
            windows = self.window_set.windows
            video = blend(parts_v, windows, self.weights_v, self.window_set.video_total, "video", 2)
            audio = blend(parts_a, windows, self.weights_a, self.window_set.audio_total, "audio", audio.ndim - 1)
            return comfy.utils.pack_latents([video, audio])[0]

    return H3ContextWindowGuider(model)
