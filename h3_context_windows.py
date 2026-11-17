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

import logging
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


# Older saves wrote the window overlap as 34 / 68 / 102; the overlap of two
# group-aligned windows is always 5 + 17k frames, so those meant 39 / 73 / 107.
LEGACY_OVERLAPS = {"34": "39", "68": "73", "102": "107"}
# The last window of a clip is at least this long (frames, 5 + 17k): about 2 s.
MIN_LAST_WINDOW_FRAMES = 56


def overlap_groups_of(overlap_frames) -> int:
    """17-frame groups ``k`` of an overlap of 5 + 17k frames (22 → 1, 39 → 2, 56 → 3);
    the old 34 / 68 / 102 give 2 / 4 / 6, the same as 39 / 73 / 107."""
    return max(1, math.ceil((int(overlap_frames) - 5) / GROUP_FRAMES))


def plan_windows(total_frames: int, window_frames: int, overlap_frames: int) -> list[Window]:
    """Group-aligned windows covering a 5 + 17N clip, laid out from frame 0.

    Every window is ``window_frames`` long (rounded down to 5 + 17M) and starts
    ``overlap_frames`` (5 + 17k) before the previous one ends, so neighbours
    overlap by exactly that much.  The last window only runs to the clip's end
    (the clip is already padded to 5 + 17N and trimmed afterwards); when that
    would leave it shorter than ``MIN_LAST_WINDOW_FRAMES`` it starts earlier,
    overlapping more.  A clip no longer than one window is one window.
    """
    total_groups = groups_of(total_frames)
    window_groups = max(2, (max(5, int(window_frames)) - 5) // GROUP_FRAMES)
    overlap_groups = overlap_groups_of(overlap_frames)
    if overlap_groups >= window_groups:
        raise ValueError(f"H3 上下文窗口：重叠 {overlap_frames} 帧必须小于窗口 {window_frames} 帧（按 17 帧取整后）。")
    full_audio = audio_tokens(total_frames)
    if total_groups <= window_groups:
        return [Window(0, int(total_frames), 0, video_tokens(total_frames), 0, full_audio)]
    stride = window_groups - overlap_groups
    shortest_last = max(groups_of(MIN_LAST_WINDOW_FRAMES), overlap_groups + 1)
    spans = []
    start = 0
    while start + window_groups < total_groups:
        spans.append((start, window_groups))
        start += stride
    if total_groups - start < shortest_last:
        start = total_groups - shortest_last
        if spans:
            start = max(start, spans[-1][0] + 1)
    spans.append((start, total_groups - start))
    return [_window(GROUP_FRAMES * group, frames_of(groups), int(total_frames)) for group, groups in spans]


def _audio_start(frame_start: int, frames: int, total_frames: int) -> int:
    """First audio token of a window.  A window that ends on the clip's last
    frame takes the clip's last audio tokens: rounding its start down could
    otherwise leave the final token(s) of the clip in no window."""
    full_audio, length_a = audio_tokens(total_frames), audio_tokens(frames)
    if int(frame_start) + int(frames) >= int(total_frames):
        return full_audio - length_a
    return min(round(frame_start * AUDIO_PER_FRAME), full_audio - length_a)


# Lips move a little before and after the sound of a line.
SPEECH_PAD_FRAMES = 4
# A window of a talk block is at least this many 17-frame groups (124 frames ≈ 5 s).
SPEECH_MIN_WINDOW_GROUPS = 7


def _window(frame_start: int, frames: int, total_frames: int) -> Window:
    return Window(frame_start, frames, GROUP_TOKENS * (frame_start // GROUP_FRAMES), video_tokens(frames),
                  _audio_start(frame_start, frames, total_frames), audio_tokens(frames))


def plan_speech_windows(total_frames: int, window_frames: int, overlap_frames: int,
                        speech: list[tuple[int, int]]) -> tuple[list[Window], int]:
    """Group-aligned windows whose overlaps fall in the pauses between spoken lines.

    ``speech`` = (start, end) frames of every dialogue line, relative to the
    clip.  In an overlap two windows are blended, so a sentence there would be
    spoken by both: the windows are chosen so that as few speech frames as
    possible lie in an overlap.  A window may be shorter than
    ``window_frames`` (down to about 5 s) so that its end can reach a pause;
    the first starts at frame 0, the last ends at the clip's end and every
    overlap is at least ``overlap_frames``.  Ties go to fewer windows, then to
    longer ones, then to the even plan of ``plan_windows``.  Returns the
    windows and the speech frames left inside overlaps (0 = every line lies
    inside one window).
    """
    plain = plan_windows(total_frames, window_frames, overlap_frames)
    if len(plain) < 2 or not speech:
        return plain, 0
    total = int(total_frames)
    total_groups = groups_of(total)
    longest = groups_of(plain[0].frames)
    overlap_groups = overlap_groups_of(overlap_frames)
    shortest = min(longest, max(SPEECH_MIN_WINDOW_GROUPS, overlap_groups + 1))
    talking = torch.zeros(total, dtype=torch.bool)
    for start, end in speech:
        start, end = max(0, int(start) - SPEECH_PAD_FRAMES), min(total, int(end) + SPEECH_PAD_FRAMES)
        if end > start:
            talking[start:end] = True
    talking = talking.tolist()
    costs: dict[tuple[int, int], tuple[float, int]] = {}

    def cost(end_group: int, next_start: int) -> tuple[float, int]:
        """(weighted, plain) speech frames in the overlap of a window ending on group
        ``end_group`` (frame 17e + 5) and the next starting on ``next_start``.  Speech in
        the middle of an overlap, where both windows weigh about the same, counts most."""
        key = (end_group, next_start)
        if key not in costs:
            a, b = GROUP_FRAMES * next_start, min(total, GROUP_FRAMES * end_group + 5)
            length, weighted, plain_frames = b - a, 0.0, 0
            for index in range(max(0, length)):
                if talking[a + index]:
                    share = (index + 1) / (length + 1)
                    weighted += 2.0 * min(share, 1.0 - share)
                    plain_frames += 1
            costs[key] = (weighted, plain_frames)
        return costs[key]

    # A window is (start group, end group); best[(s, e)] = (weighted cost, windows, -covered groups,
    # speech frames in overlaps, path).  Every transition moves to a later start, so states are
    # final once their start is reached.
    best = {(0, e): (0.0, 1, -e, 0, [(0, e)]) for e in range(shortest, min(longest, total_groups) + 1)}
    for s in range(total_groups):
        for e in range(s + shortest, min(s + longest, total_groups - 1) + 1):
            here = best.get((s, e))
            if here is None:
                continue
            for r in range(s + 1, e - overlap_groups + 1):
                weighted, frames_crossed = cost(e, r)
                for e2 in range(max(e + 1, r + shortest), min(r + longest, total_groups) + 1):
                    option = (round(here[0] + weighted, 6), here[1] + 1, here[2] - (e2 - r),
                              here[3] + frames_crossed, here[4] + [(r, e2)])
                    if (r, e2) not in best or option[:3] < best[(r, e2)][:3]:
                        best[(r, e2)] = option
    finals = [value for (s, e), value in best.items() if e == total_groups]
    weighted, count, _covered, crossed, path = min(finals, key=lambda item: item[:3])
    even = [(w.frame_start // GROUP_FRAMES, groups_of(w.frames) + w.frame_start // GROUP_FRAMES) for w in plain]
    even_costs = [cost(left[1], right[0]) for left, right in zip(even, even[1:])]
    if round(sum(c[0] for c in even_costs), 6) <= weighted and len(even) <= count:
        chosen = plain  # the even plan is just as good
    else:
        chosen = [_window(GROUP_FRAMES * s, frames_of(e - s), total) for s, e in path]
    return chosen, speech_in_overlaps(chosen, speech)


def speech_in_overlaps(windows: list[Window], speech: list[tuple[int, int]]) -> int:
    """Frames of the spoken lines themselves (no lip padding) that lie where windows overlap."""
    frames = set()
    for before, after in zip(windows, windows[1:]):
        start, end = after.frame_start, before.frame_start + before.frames
        for a, b in speech:
            frames.update(range(max(start, int(a)), min(end, int(b))))
    return len(frames)


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


# Anchored windows hand the video over at the end of the overlap, in this many
# video tokens (about 8 frames).
LATE_HANDOVER_TOKENS = 2


def _late_handover(length: int, overlap: int, rising: bool, fade: int = LATE_HANDOVER_TOKENS) -> torch.Tensor:
    """Like ``_ramp``, but the overlap stays with the earlier window until its last ``fade``
    entries, where it crossfades to the later one.  A rising and a falling handover over the
    same overlap sum to exactly 1."""
    weights = torch.ones(length, dtype=torch.float32)
    overlap = max(0, min(int(overlap), length))
    if not overlap:
        return weights
    fade = max(1, min(int(fade), overlap))
    later = torch.zeros(overlap, dtype=torch.float32)
    later[overlap - fade:] = torch.arange(1, fade + 1, dtype=torch.float32) / (fade + 1)
    if rising:
        weights[:overlap] = later
    else:
        weights[length - overlap:] = 1.0 - later
    return weights


def window_weights(windows: list[Window], stream: str, late: bool = False) -> list[torch.Tensor]:
    """Per-window blend weights along time for ``stream`` "video" or "audio".

    ``late``: the later window of an overlap is anchored on the earlier one's
    prediction there, so the overlap keeps the earlier window's frames and
    hands over only at its end.  Averaging two windows' takes of a dance
    across the whole overlap cancelled their motion out (a frozen stretch).
    """
    def bounds(window):
        if stream == "video":
            return window.video_start, window.video_end
        return window.audio_start, window.audio_end

    edge = _late_handover if late else _ramp
    out = []
    for index, window in enumerate(windows):
        start, end = bounds(window)
        weights = torch.ones(end - start, dtype=torch.float32)
        if index > 0:
            weights = torch.minimum(weights, edge(end - start, bounds(windows[index - 1])[1] - start, True))
        if index + 1 < len(windows):
            weights = torch.minimum(weights, edge(end - start, end - bounds(windows[index + 1])[0], False))
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


# Window anchoring switch (False = windows are sampled jointly without anchors, as before).
WINDOW_ANCHORING = True


# Window anchoring: every window after the first gets a keyframe at its frame 0
# holding the previous window's prediction of their overlap, refreshed at every
# step.  Without it each window settles on its own framing and pose, and the
# overlap cross-fades between two different pictures (a drifting composition,
# averaged-out motion); the first window is anchored by its tail guide or not
# at all, as before.
def add_window_anchor(conds, before: Window, window: Window, video_shape):
    """Add a zero keyframe latent for ``window``'s overlap with ``before`` to every cond entry
    without keyframes of its own; returns the placeholder (None when nothing was added)."""
    overlap = before.video_end - window.video_start
    if overlap <= 0:
        return None
    shape = (1, int(video_shape[1]), int(overlap), int(video_shape[3]), int(video_shape[4]))
    placeholder = torch.zeros(shape, dtype=torch.float32)
    added = False
    for entries in conds.values():
        for entry in entries:
            if entry.get("minimax_keyframes"):
                continue
            entry["minimax_keyframes"] = [{"resolved_frame_index": 0, "latent": placeholder}]
            entry["minimax_frame_count"] = int(window.frames)
            added = True
    return placeholder if added else None


def anchor_latents(conds, placeholder) -> list:
    """The keyframe latents the processed conds actually feed the model for ``placeholder``
    (the same tensor, or the copy processing made), to be refreshed in place every step."""
    found = []
    for entries in conds.values():
        for entry in entries:
            model_conds = entry.get("model_conds") or {}
            payload = getattr(model_conds.get("minimax_payload"), "cond", None)
            if not isinstance(payload, dict):
                continue
            keyframes = payload.get("keyframes") or []
            latents = payload.get("cond_video_latents") or []
            for position, keyframe in enumerate(kf for kf in keyframes if kf.get("latent") is not None):
                if keyframe.get("latent") is placeholder and position < len(latents):
                    if all(latents[position] is not seen for seen in found):
                        found.append(latents[position])
    if not found and placeholder is not None:
        found.append(placeholder)
    return found


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
            ratio = max(window.video_len for window in window_set.windows) / float(window_set.video_total)

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
            self.anchors = []
            windows = self.window_set.windows
            for index, (window, raw) in enumerate(zip(windows, self.window_raw)):
                w_noise, w_shapes = window_pack(noise, window)
                w_latent, _ = window_pack(latent_image, window)
                w_mask, _ = window_pack(denoise_mask, window)
                conds = {key: [dict(item) for item in comfy.sampler_helpers.convert_cond(value)]
                         for key, value in raw.items()}
                placeholder = None
                if index > 0 and WINDOW_ANCHORING:
                    placeholder = add_window_anchor(conds, windows[index - 1], window, video_shape)
                conds = comfy.samplers.process_conds(self.inner_model, w_noise, conds, device, w_latent, w_mask, seed,
                                                     latent_shapes=w_shapes)
                self.runs.append((window, conds, w_shapes))
                self.anchors.append(anchor_latents(conds, placeholder) if placeholder is not None else [])
            # Restore the full shapes: audio scale and inpainting work on the whole clip.
            self.inner_model.latent_shapes = latent_shapes
            anchored = len(self.anchors) > 1 and all(self.anchors[1:])
            self.weights_v = window_weights(self.window_set.windows, "video", late=anchored)
            if len(self.anchors) > 1:
                logging.getLogger("FeiHouEasyH3").info(
                    "Easy H3 上下文窗口：%d 个窗口%s", len(self.anchors),
                    "，后面的窗口锚定在前一个窗口的重叠帧上，画面在重叠结尾交接" if anchored
                    else "，没有加上窗口锚定（窗口自带关键帧），重叠处画面渐变混合")
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
            for index, (window, conds, shapes) in enumerate(self.runs):
                if index > 0 and self.anchors[index]:
                    # The previous window has just predicted this step's clean frames of the shared
                    # overlap: they open this window as its keyframe, like the tail of a frame window.
                    before = self.runs[index - 1][0]
                    lead = parts_v[index - 1][:, :, window.video_start - before.video_start:before.video_len]
                    for anchor in self.anchors[index]:
                        anchor.copy_(lead.to(device=anchor.device, dtype=anchor.dtype))
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
