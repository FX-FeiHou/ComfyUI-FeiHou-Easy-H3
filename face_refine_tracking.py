# Adapted from Carasibana/ComfyUI-H3-FaceRefine (MIT).
# Tracking and stitching only; no upstream routes, UI or node registration.
# See LICENSE-FaceRefine.txt for attribution.
"""Face tracking / cropping / stitching nodes for MiniMax H3 face refinement."""

from __future__ import annotations

import os

import numpy as np
import torch

import comfy.nested_tensor
import folder_paths

# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------

_DETECTOR_CACHE: dict[str, object] = {}


def _detector_list() -> list[str]:
    """Face detectors, from Impact subpack's ultralytics_bbox registration if present."""
    names: list[str] = []
    for key in ("ultralytics_bbox", "ultralytics"):
        try:
            names.extend(folder_paths.get_filename_list(key))
        except Exception:
            pass
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out or ["face_yolov8m.pt"]


def _load_detector(name: str):
    if name in _DETECTOR_CACHE:
        return _DETECTOR_CACHE[name]
    path = None
    for key in ("ultralytics_bbox", "ultralytics"):
        try:
            path = folder_paths.get_full_path(key, name)
        except Exception:
            path = None
        if path:
            break
    if path is None:  # fall back to the standard models tree
        base = getattr(folder_paths, "models_dir", "models")
        for sub in ("ultralytics/bbox", "ultralytics", "ultralytics/segm"):
            cand = os.path.join(base, *sub.split("/"), name)
            if os.path.exists(cand):
                path = cand
                break
    if path is None:
        raise FileNotFoundError(
            f"Face detector '{name}' not found in ultralytics_bbox / ultralytics model folders."
        )
    from ultralytics import YOLO

    model = YOLO(path)
    _DETECTOR_CACHE[name] = model
    return model


_REC_CACHE: dict = {}


def _face_recogniser(pack: str = "buffalo_l"):
    """InsightFace recognition model, for identity matching. Cached."""
    if pack in _REC_CACHE:
        return _REC_CACHE[pack]
    import insightface

    # ComfyUI's models/insightface. InsightFace wants the directory CONTAINING a
    # "models" folder, and downloads the pack there on first use if it is absent.
    root = os.path.join(getattr(folder_paths, "models_dir", "models"), "insightface")
    app = insightface.app.FaceAnalysis(
        name=pack, root=root, allowed_modules=["detection", "recognition"],
        providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(640, 640))
    _REC_CACHE[pack] = app
    return app


def _embed_faces(app, bgr: np.ndarray) -> list:
    """[(bbox, normed_embedding), ...] for every face insightface finds."""
    out = []
    for f in app.get(bgr):
        e = getattr(f, "normed_embedding", None)
        if e is None:
            continue
        out.append((f.bbox.tolist(), np.asarray(e, dtype=np.float32)))
    return out


def _best_match(cands: list, ref_emb: np.ndarray):
    """Index of the candidate closest to the reference by cosine similarity, and the score."""
    if not cands or ref_emb is None:
        return None, -1.0
    sims = [float(np.dot(e, ref_emb)) for _, e in cands]
    i = int(np.argmax(sims))
    return i, sims[i]


# ----------------------------------------------------------------------------
# identity embedding backends
# ----------------------------------------------------------------------------
#
# buffalo_l is ArcFace on photographed faces, and it is reached through InsightFace's
# OWN detector (SCRFD, trained on WIDER FACE). On illustration that detector fires on
# nothing, so `embed` returns no candidates at all and identity matching silently
# degrades to continuity - the failure is invisible rather than wrong. Swapping only the
# recogniser would not help while the candidates still have to come from SCRFD.
#
# So the out-of-domain backends embed THE BOXES THE FACE DETECTOR ALREADY FOUND. The
# ultralytics model the user picked is the thing that knows where an anime face is, and
# it is already a node input.

_IDENT_MODES = ["insightface", "clip_vision", "ccip"]

# Context around the face box for the out-of-domain backends. A face detector's box is
# eyes-to-chin, but hair is most of what distinguishes one illustrated character from
# another, and both CCIP and CLIP expect a character image rather than a cropped face.
_IDENT_CROP_FACTOR = 2.0
_IDENT_CROP_PX = 384


def _ident_crop(frame: torch.Tensor, box, out: int = _IDENT_CROP_PX) -> torch.Tensor:
    """Square, context-padded crop around one face box. frame [1,H,W,C] -> [1,out,out,3]."""
    _, H, W, _ = frame.shape
    x0, y0, x1, y1 = float(box[0]), float(box[1]), float(box[2]), float(box[3])
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    side = min(max(x1 - x0, y1 - y0) * _IDENT_CROP_FACTOR, float(min(W, H)))
    side = max(side, 8.0)
    x = min(max(cx - side / 2.0, 0.0), max(0.0, W - side))
    y = min(max(cy - side / 2.0, 0.0), max(0.0, H - side))
    return _affine_crop(frame, (x, y, side, side), out, out)


def _unit_mean(feats) -> np.ndarray:
    a = np.mean(np.stack(feats), axis=0)
    n = np.linalg.norm(a)
    return a / n if n > 0 else a


class _InsightFaceEmbedder:
    """ArcFace via InsightFace, on InsightFace's own aligned detections.

    Left exactly as it was: ArcFace wants the 5-point landmark alignment that app.get()
    performs, and feeding it a raw box crop measurably degrades it.
    """

    name = "insightface"
    default_threshold = 0.28
    note = ""
    # SCRFD comes with the model, so the tracker's own detector is never wanted here
    # and must not be loaded to satisfy an argument this class discards.
    needs_detector = False

    def __init__(self):
        self.app = _face_recogniser()

    def embed(self, frame, boxes):
        return _embed_faces(self.app, _to_bgr_u8(frame[0]))

    def embed_reference(self, frame, model, confidence):
        cands = _embed_faces(self.app, _to_bgr_u8(frame[0]))
        if not cands:
            return None
        j = max(range(len(cands)), key=lambda k: cands[k][0][3] - cands[k][0][1])
        return cands[j][1]

    def best_match(self, cands, ref):
        return _best_match(cands, ref)

    def merge(self, feats):
        return _unit_mean(feats)


class _CropEmbedder:
    """Embeds the face detector's own boxes. Base for the out-of-domain backends."""

    # These read the tracker's detector to find the face in the reference image.
    needs_detector = True

    def _features(self, crops: torch.Tensor) -> list:
        raise NotImplementedError

    def embed(self, frame, boxes):
        if not boxes:
            return []
        crops = torch.cat([_ident_crop(frame, b) for b in boxes], 0)
        feats = self._features(crops)
        return [(list(b), f) for b, f in zip(boxes, feats) if f is not None]

    def embed_reference(self, frame, model, confidence):
        # Locate the face in the reference with the SAME detector the clip uses. If it
        # finds nothing, embed the whole image - which is what CCIP is trained on anyway.
        boxes = []
        try:
            res = model.predict(_to_bgr_u8(frame[0]), conf=confidence, verbose=False)[0]
            boxes = res.boxes.xyxy.tolist() if len(res.boxes) else []
        except Exception:
            boxes = []
        if boxes:
            crop = _ident_crop(frame, max(boxes, key=lambda q: q[3] - q[1]))
        else:
            _, H, W, _ = frame.shape
            crop = _affine_crop(frame, (0.0, 0.0, float(W), float(H)),
                                _IDENT_CROP_PX, _IDENT_CROP_PX)
        feats = self._features(crop)
        return feats[0] if feats else None

    def merge(self, feats):
        return _unit_mean(feats)

    def best_match(self, cands, ref):
        return _best_match(cands, ref)


class _ClipVisionEmbedder(_CropEmbedder):
    """ComfyUI's own CLIP vision, wired in from a CLIPVisionLoader. No new dependency,
    and it is domain-agnostic - anime, 3D, stylised, puppets.

    It describes APPEARANCE rather than identity, so its similarities sit high and close
    together; the useful threshold is much higher than ArcFace's and is scene-dependent.
    The report prints the scores it actually saw so the number can be set from evidence.
    """

    name = "clip_vision"
    default_threshold = 0.80
    note = ""

    def __init__(self, clip_vision):
        if clip_vision is None:
            raise ValueError(
                "identity_model='clip_vision' needs a CLIP_VISION model connected to "
                "identity_clip_vision. Add a CLIPVisionLoader node and wire it in."
            )
        self.cv = clip_vision

    def _features(self, crops):
        out = self.cv.encode_image(crops)
        emb = getattr(out, "image_embeds", None)
        if emb is None:
            emb = out["image_embeds"]
        e = np.asarray(emb.detach().float().cpu().numpy(), dtype=np.float32)
        e = e.reshape(e.shape[0], -1)
        n = np.linalg.norm(e, axis=-1, keepdims=True)
        return list(e / np.maximum(n, 1e-8))


class _CCIPEmbedder(_CropEmbedder):
    """CCIP - "is this the same anime character?" - the illustration counterpart of
    ArcFace. Deliberately NOT a declared dependency of this pack: dghs-imgutils pins
    numpy<2 and pulls opencv-contrib-python, which shadows the OpenCV build ComfyUI
    ships in exactly the way onnxruntime-gpu shadows onnxruntime. Imported only when
    this backend is actually selected.

    Its distance comes from a learned metric model, not a dot product, so it cannot
    share the cosine path. The score below is that distance mapped so that the model's
    own published threshold lands on 0.5: higher is more alike, 0.5 is the operating
    point CCIP itself recommends, and raising it makes matching stricter.
    """

    name = "ccip"
    default_threshold = 0.5

    def __init__(self):
        try:
            from imgutils.metrics import (ccip_batch_differences,
                                          ccip_batch_extract_features,
                                          ccip_default_threshold, ccip_merge)
        except ImportError as exc:
            raise ImportError(
                "identity_model='ccip' needs dghs-imgutils, which this pack does not "
                "install for you (it pins numpy<2 and pulls opencv-contrib-python). "
                "Install it yourself with:  pip install dghs-imgutils\n"
                f"({exc})"
            ) from exc
        self._extract = ccip_batch_extract_features
        self._diffs = ccip_batch_differences
        self._merge = ccip_merge
        self._t = float(ccip_default_threshold())
        self.note = f"ccip native distance threshold {self._t:.3f} -> score 0.5"

    def _features(self, crops):
        from PIL import Image

        arr = (crops[..., :3].clamp(0, 1).detach().cpu().numpy() * 255.0).astype(np.uint8)
        return list(self._extract([Image.fromarray(a) for a in arr]))

    def best_match(self, cands, ref):
        if not cands or ref is None:
            return None, -1.0
        d = np.asarray(self._diffs([ref] + [f for _, f in cands]))[0, 1:]
        sims = 1.0 - d / (2.0 * max(self._t, 1e-6))
        i = int(np.argmax(sims))
        return i, float(sims[i])

    def merge(self, feats):
        return self._merge(list(feats))


def _make_embedder(mode: str, clip_vision=None):
    if mode == "ccip":
        return _CCIPEmbedder()
    if mode == "clip_vision":
        return _ClipVisionEmbedder(clip_vision)
    return _InsightFaceEmbedder()


def _iou(a, b) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    ua = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def _continuity_cost(box, last):
    """Distance from the predicted position, with a size-change penalty."""
    cx, cy, sz = (box[0]+box[2])/2.0, (box[1]+box[3])/2.0, box[3]-box[1]
    d = ((cx - last[0]) ** 2 + (cy - last[1]) ** 2) ** 0.5
    return d + abs(sz - last[2]) * 2.0


# How far the subject may move between resolved frames, as a multiple of its face height.
# A face further away than this is someone else: the step is refused and the frame is
# left unresolved rather than handed to whoever is nearest. Only applied in a shot that
# holds more than one face at some point; with nobody else in shot there is no one to
# confuse the subject with.
_REACH = 0.75
# Extra reach per frame since the subject was last resolved, and the most that can add.
# Small on purpose: a long gap must not grow the reach out to a neighbouring face.
_REACH_PER_FRAME = 0.05
_REACH_GAP_MAX = 0.25
# Resolved frames the subject's typical face height is taken over. A face box shrinks as
# the head turns away or is covered, and the reach must not shrink with it.
_REACH_HISTORY = 12


def _within_reach(box, last, gap: int, height: float = 0.0) -> bool:
    """Whether `box` can be the subject last resolved at `last` (cx, cy, face height),
    `gap` frames earlier. `height` is the subject's typical recent face height."""
    cx, cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
    grow = min(_REACH_PER_FRAME * max(int(gap) - 1, 0), _REACH_GAP_MAX)
    reach = (_REACH + grow) * max(float(last[2]), float(height), 1.0)
    return ((cx - last[0]) ** 2 + (cy - last[1]) ** 2) ** 0.5 <= reach


def _typical_height(heights) -> float:
    """Median of the subject's recent face heights; 0 when there are none."""
    h = sorted(heights[-_REACH_HISTORY:])
    return float(h[len(h) // 2]) if h else 0.0


def _holds_crowd(all_boxes, a: int, b: int) -> bool:
    """Whether any frame in [a, b) holds more than one face."""
    return any(len(all_boxes[j]) > 1 for j in range(max(0, a), min(b, len(all_boxes))))


def _frame_ranges(frames) -> str:
    """[3, 4, 5, 9] -> "3-5, 9"."""
    out, run = [], []
    for f in sorted(frames):
        if run and f == run[-1] + 1:
            run.append(f)
            continue
        if run:
            out.append(run)
        run = [f]
    if run:
        out.append(run)
    return ", ".join(str(r[0]) if len(r) == 1 else f"{r[0]}-{r[-1]}" for r in out)


def _build_clip_anchor(emb, images, all_boxes, max_samples=24):
    """Average embedding of the subject, taken from the CLIP ITSELF.

    A stylised reference image sits in a different domain from rendered video frames -
    measured cosine similarity between an illustration reference and a frame of the same
    character was only 0.305, where same-domain photos of one person score 0.5-0.7. So an
    absolute threshold against an external reference is unreliable.

    Anchoring in-domain fixes that: sample frames where ONE face clearly dominates (no
    ambiguity about who the subject is), and average their embeddings.
    """
    B = len(all_boxes)
    step = max(1, B // max_samples)
    embs = []
    for i in range(0, B, step):
        boxes = all_boxes[i]
        if not boxes:
            continue
        heights = sorted((b[3] - b[1] for b in boxes), reverse=True)
        # unambiguous = only one face, or the biggest is clearly the biggest
        if len(heights) > 1 and heights[0] < heights[1] * 1.6:
            continue
        # only the dominant box needs embedding - the filter above just established
        # that it is unambiguously the subject
        top = max(boxes, key=lambda b: b[3] - b[1])
        cands = emb.embed(images[i:i + 1], [top])
        if not cands:
            continue
        j = max(range(len(cands)), key=lambda k: cands[k][0][3] - cands[k][0][1])
        embs.append(cands[j][1])
    if not embs:
        return None, 0
    return emb.merge(embs), len(embs)


def _shots_without_subject(emb, images, all_boxes, segs, ref, threshold,
                           samples: int = 6):
    """Shots where no sampled frame holds a face matching `ref`.

    Sampled rather than exhaustive: embedding every face of every frame costs more
    than the render it saves. A shot the subject appears in only briefly can be
    missed, which is why this is off by default and every drop is reported.
    """
    out, scores = [], {}
    for k, (a, b) in enumerate(segs):
        frames = [i for i in range(a, b) if all_boxes[i]]
        if not frames:
            continue
        step = max(1, len(frames) // samples)
        best = -1.0
        for i in frames[::step][:samples]:
            cands = emb.embed(images[i:i + 1], all_boxes[i])
            j, sc = emb.best_match(cands, ref)
            if j is not None:
                best = max(best, float(sc))
        scores[k] = best
        if best < threshold:
            out.append(k)          # 0-based: the caller indexes segs with it
    return out, scores


def _track_continuity(all_boxes, start_frame: int, start_idx: int) -> list[int]:
    """Provisional subject track by continuity alone. Index into all_boxes[i], or -1.

    Pure geometry over boxes that are already detected, so it costs nothing, which is
    what makes it usable as a PRE-pass: it says which box is the subject on every frame
    before any embedding work happens. A frame with no face within reach stays -1.
    """
    B = len(all_boxes)
    track = [-1] * B
    if not (0 <= start_frame < B) or start_idx >= len(all_boxes[start_frame]):
        return track
    track[start_frame] = start_idx
    q = all_boxes[start_frame][start_idx]
    last = ((q[0] + q[2]) / 2.0, (q[1] + q[3]) / 2.0, q[3] - q[1])
    last_i, heights = start_frame, [last[2]]
    crowd = _holds_crowd(all_boxes, start_frame, B)
    for i in range(start_frame + 1, B):
        boxes = all_boxes[i]
        near = [j for j in range(len(boxes))
                if not crowd or _within_reach(boxes[j], last, i - last_i,
                                              _typical_height(heights))]
        if not near:
            continue
        k = min(near, key=lambda j: _continuity_cost(boxes[j], last))
        track[i] = k
        q = boxes[k]
        last = ((q[0] + q[2]) / 2.0, (q[1] + q[3]) / 2.0, q[3] - q[1])
        last_i = i
        heights.append(last[2])
    return track


def _track_back(all_boxes, lock, box_i, stop):
    """Continuity from a lock frame BACKWARDS to the start of its shot.

    Forward continuity abandons everything before the frame the subject was named on,
    leaving it to interpolation. Once the subject IS named, those earlier frames are
    the same person walking backwards, so plain geometry resolves them. Never crosses
    `stop`, which is the shot boundary. A frame with no face within reach is left out.
    """
    out = {}
    q = all_boxes[lock][box_i]
    last = ((q[0] + q[2]) / 2.0, (q[1] + q[3]) / 2.0, q[3] - q[1])
    last_i, heights = lock, [last[2]]
    crowd = _holds_crowd(all_boxes, stop, lock + 1)
    for i in range(lock - 1, stop - 1, -1):
        boxes = all_boxes[i]
        near = [j for j in range(len(boxes))
                if not crowd or _within_reach(boxes[j], last, last_i - i,
                                              _typical_height(heights))]
        if not near:
            continue
        k = min(near, key=lambda j: _continuity_cost(boxes[j], last))
        out[i] = k
        q = boxes[k]
        last = ((q[0] + q[2]) / 2.0, (q[1] + q[3]) / 2.0, q[3] - q[1])
        last_i = i
        heights.append(last[2])
    return out


def _shot_anchors(emb, images, all_boxes, segs, forced, n, threshold):
    """One anchor per picked shot, and the shots that hold a different person.

    A reviewed answer is one face per shot chosen by hand, and nothing makes those
    the same person. Merging two people yields an embedding that resembles neither,
    and that merge is what every later tie-break would consult, so the odd shots out
    are dropped from it rather than averaged in.
    """
    budget = max(4, 24 // max(1, len(segs)))
    per = []
    for a, b in segs:
        lock = next((f for f in range(a, b) if f in forced), -1)
        if lock < 0:
            per.append(None)
            continue
        part = _track_continuity(all_boxes[a:b], lock - a, forced[lock])
        tr = [-1] * n
        for j, v in enumerate(part):
            if a + j < n:
                tr[a + j] = v
        # The lock can sit well inside its shot, and everything before it is the same
        # person - worth sampling, since clean frames are scarce in a crowd.
        for f, k in _track_back(all_boxes, lock, forced[lock], a).items():
            tr[f] = k
        _pb = all_boxes[lock][forced[lock]]
        anchor, _used = _anchor_from_track(emb, images, all_boxes, tr, max_samples=budget,
                                           min_face=min(32.0, 0.9 * (_pb[3] - _pb[1])))
        per.append(anchor)

    have = [(k, e) for k, e in enumerate(per) if e is not None]
    if len(have) < 2:
        return [e for _k, e in have], []
    # The largest set of shots that agree with one another; the rest are the odd ones.
    keep = []
    for _k, e in have:
        grp = [j for j, f in have if emb.best_match([(None, f)], e)[1] >= threshold]
        if len(grp) > len(keep):
            keep = grp
    return ([e for k, e in have if k in keep],
            [k + 1 for k, _e in have if k not in keep])


def _anchor_from_track(emb, images, all_boxes, track, max_samples=24, min_face=32.0):
    """Average embedding of the TRACKED subject, sampled from frames where the tracked
    box is unambiguous.

    _build_clip_anchor assumes the subject is whoever is biggest. That is the wrong
    assumption the moment the user names a different one through select/select_index, so
    when they do, the anchor is built from their pick instead - otherwise identity
    matching would spend the rest of the clip dragging the crop back onto the dominant
    face the user deliberately did not choose.
    """
    clean = []
    for i, k in enumerate(track):
        if k < 0:
            continue
        boxes = all_boxes[i]
        tb = boxes[k]
        if tb[3] - tb[1] < min_face:          # tiny faces embed badly
            continue
        if any(_iou(tb, q) > 0.05 for j, q in enumerate(boxes) if j != k):
            continue
        clean.append(i)
    if not clean:
        return None, 0
    step = max(1, len(clean) // max_samples)
    embs = []
    for i in clean[::step][:max_samples]:
        tb = all_boxes[i][track[i]]
        cands = emb.embed(images[i:i + 1], [tb])
        if not cands:
            continue
        j, best = None, 0.0
        for k, (bb, _) in enumerate(cands):
            v = _iou(bb, tb)
            if v > best:
                best, j = v, k
        if j is None or best < 0.3:      # the embedder found other people, not ours
            continue
        embs.append(cands[j][1])
    if not embs:
        return None, 0
    return emb.merge(embs), len(embs)


def _to_bgr_u8(img: torch.Tensor) -> np.ndarray:
    """ComfyUI IMAGE frame [H,W,C] float 0..1 -> BGR uint8 for ultralytics."""
    a = (img[..., :3].clamp(0, 1).cpu().numpy() * 255.0).astype(np.uint8)
    return a[..., ::-1].copy()


def _interp_gaps(vals: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Fill non-detected frames by linear interpolation; hold at the ends."""
    n = len(vals)
    idx = np.arange(n)
    if not valid.any():
        return np.zeros(n, dtype=np.float64)
    return np.interp(idx, idx[valid], vals[valid])


def _smooth(vals: np.ndarray, window: int, method: str = "gaussian") -> np.ndarray:
    """Smooth a trajectory with reflected edges. window<=1 is a no-op.

    gaussian       - weighted kernel (sigma = window/6). Much better high-frequency
                     rejection than a boxcar, which has sinc sidelobes and leaves
                     residual jitter. Default.
    savgol         - local polynomial fit. Kills jitter while preserving ramps and
                     curves, so a push-in keeps its shape instead of being flattened.
    moving_average - plain boxcar. Kept for comparison.
    """
    if window <= 1 or len(vals) < 3:
        return vals
    window = min(int(window), len(vals))
    if window % 2 == 0:
        window += 1
    if window < 3:
        return vals
    pad = window // 2
    padded = np.pad(vals, pad, mode="reflect")

    if method == "savgol":
        try:
            from scipy.signal import savgol_filter

            polyorder = 2 if window > 3 else 1
            return np.asarray(savgol_filter(padded, window, polyorder))[pad : pad + len(vals)]
        except Exception:
            method = "gaussian"

    if method == "gaussian":
        x = np.arange(window, dtype=np.float64) - pad
        sigma = max(window / 6.0, 0.5)
        kernel = np.exp(-(x ** 2) / (2.0 * sigma ** 2))
        kernel /= kernel.sum()
    else:
        kernel = np.ones(window, dtype=np.float64) / window

    return np.convolve(padded, kernel, mode="valid")[: len(vals)]


# ----------------------------------------------------------------------------
# hard cuts
# ----------------------------------------------------------------------------
#
# Smoothing and interpolation run over the whole clip. Across a cut the kernel spans
# two shots and drags the box toward where the subject stood in the other one, and a
# dropout is filled along a line that existed in neither. Splitting at the cuts makes
# each shot its own window.

_CUT_MODES = ["none", "auto (pyscenedetect)"]


def _make_cut_detector(threshold: float):
    """PySceneDetect adaptive detector, fed frame by frame.

    Adaptive rather than content: it scores each frame against a rolling window of its
    neighbours, so sustained motion and lighting changes do not read as cuts. A fixed
    content threshold does, on exactly the fast-moving, hard-lit material this pack is
    pointed at.

    Rides along on the face-detection pass rather than decoding the clip a second time:
    that loop already converts every frame to BGR, which is what scenedetect consumes.
    """
    from scenedetect.common import FrameTimecode
    from scenedetect.detectors import AdaptiveDetector

    return AdaptiveDetector(adaptive_threshold=float(threshold)), FrameTimecode


def _segments(n: int, cuts) -> list:
    """[(start, end), ...] half-open ranges, split at each cut frame."""
    marks = sorted({int(c) for c in cuts if 0 < int(c) < n})
    bounds = [0] + marks + [n]
    return [(bounds[k], bounds[k + 1]) for k in range(len(bounds) - 1)]


def _interp_gaps_seg(vals: np.ndarray, valid: np.ndarray, segs) -> np.ndarray:
    """_interp_gaps, per shot. Filling a gap across a cut would draw a line between
    two positions that never coexisted."""
    if len(segs) <= 1:
        return _interp_gaps(vals, valid)
    clipwide = _interp_gaps(vals, valid)
    out = np.asarray(vals, dtype=np.float64).copy()
    for a, b in segs:
        if valid[a:b].any():
            out[a:b] = _interp_gaps(vals[a:b], valid[a:b])
        else:
            # No detection in this shot at all. The clip-wide fill is the wrong shot
            # but a plausible position; _interp_gaps alone would return zeros here.
            out[a:b] = clipwide[a:b]
    return out


def _smooth_seg(vals: np.ndarray, window: int, method: str, segs) -> np.ndarray:
    """_smooth, per shot, so the kernel never spans a cut.

    A shot shorter than the window is smoothed less, not rejected - _smooth clamps the
    window to the data it has. The report says when that happened.
    """
    if len(segs) <= 1:
        return _smooth(vals, window, method)
    out = np.asarray(vals, dtype=np.float64).copy()
    for a, b in segs:
        out[a:b] = _smooth(np.asarray(vals[a:b], dtype=np.float64), window, method)
    return out


def _affine_crop(img: torch.Tensor, box: tuple, cw: int, ch: int) -> torch.Tensor:
    """Sub-pixel crop+resize in one bilinear sample. img [1,H,W,C] -> [1,ch,cw,C].

    Integer slicing quantises the box to whole pixels, and that rounding is by far the
    largest remaining source of frame-to-frame jitter once the trajectory is smoothed
    (measured jerk 0.58 vs 0.06 for the smoothed float trajectory). Sampling at float
    coordinates removes it entirely.
    """
    import torch.nn.functional as F

    x, y, bw, bh = box
    _, H, W, _ = img.shape
    src = img[..., :3].movedim(-1, 1).float()
    theta = torch.tensor(
        [[[bw / W, 0.0, (2.0 * x + bw) / W - 1.0],
          [0.0, bh / H, (2.0 * y + bh) / H - 1.0]]],
        dtype=torch.float32, device=src.device,
    )
    grid = F.affine_grid(theta, (1, 3, int(ch), int(cw)), align_corners=False)
    out = F.grid_sample(src, grid, mode="bilinear", padding_mode="border", align_corners=False)
    return out.movedim(1, -1).to(img.dtype)


def _gaussian_blur_mask(mask: torch.Tensor, feather: int) -> torch.Tensor:
    """Separable Gaussian blur on a [1,1,H,W] mask. Mirrors Impact Pack's
    tensor_gaussian_blur_mask / feather_mask (sigma = thickness/3)."""
    import torch.nn.functional as F

    if feather <= 0:
        return mask
    k = 2 * int(feather) + 1
    shortest = min(mask.shape[-2], mask.shape[-1])
    if shortest <= k:
        k = max(3, int(shortest / 2) | 1)
    sigma = max(k / 6.0, 0.5)
    x = torch.arange(k, device=mask.device, dtype=torch.float32) - k // 2
    g = torch.exp(-(x ** 2) / (2 * sigma ** 2))
    g = (g / g.sum()).to(mask.dtype)
    pad = k // 2
    m = F.conv2d(F.pad(mask, (pad, pad, 0, 0), mode="replicate"), g.view(1, 1, 1, k))
    m = F.conv2d(F.pad(m, (0, 0, pad, pad), mode="replicate"), g.view(1, 1, k, 1))
    return m


def _face_region_mask(ch: int, cw: int, face_rect, dilation: int, feather: int,
                      shape: str, device, dtype) -> torch.Tensor:
    """FaceDetailer-style paste mask: solid over the FACE box inside the larger crop,
    dilated, then Gaussian-blurred. Everything outside keeps its original pixels.

    Impact Pack core.py:1256 builds exactly this - a crop-sized zeros canvas with 1s only
    where the detected face bbox sits - then blurs it. The generous crop exists to give the
    sampler CONTEXT; it is not what gets composited.
    """
    m = torch.zeros((1, 1, int(ch), int(cw)), device=device, dtype=torch.float32)
    fx, fy, fwd, fhd = face_rect
    fx -= dilation; fy -= dilation
    fwd += 2 * dilation; fhd += 2 * dilation

    if shape == "ellipse":
        yy = torch.arange(ch, device=device, dtype=torch.float32).view(-1, 1)
        xx = torch.arange(cw, device=device, dtype=torch.float32).view(1, -1)
        ccx, ccy = fx + fwd / 2.0, fy + fhd / 2.0
        rx, ry = max(fwd / 2.0, 1.0), max(fhd / 2.0, 1.0)
        m[0, 0] = (((xx - ccx) / rx) ** 2 + ((yy - ccy) / ry) ** 2 <= 1.0).float()
    else:
        x0 = max(0, int(round(fx))); y0 = max(0, int(round(fy)))
        x1 = min(int(cw), int(round(fx + fwd))); y1 = min(int(ch), int(round(fy + fhd)))
        if x1 > x0 and y1 > y0:
            m[0, 0, y0:y1, x0:x1] = 1.0

    return _gaussian_blur_mask(m, feather).clamp(0, 1).to(dtype)


def _feather_mask(h: int, w: int, feather: int, device, dtype) -> torch.Tensor:
    """[h,w] mask: 1 in the core, cosine ramp to 0 over `feather` px at every edge."""
    m = torch.ones((h, w), device=device, dtype=dtype)
    f = int(max(0, min(feather, min(h, w) // 2 - 1)))
    if f <= 0:
        return m
    ramp = 0.5 - 0.5 * torch.cos(
        torch.linspace(0, np.pi, f + 2, device=device, dtype=dtype)[1:-1]
    )
    m[:f, :] *= ramp.view(-1, 1)
    m[h - f :, :] *= ramp.flip(0).view(-1, 1)
    m[:, :f] *= ramp.view(1, -1)
    m[:, w - f :] *= ramp.flip(0).view(1, -1)
    return m


# ----------------------------------------------------------------------------
# subject selection + the index preview
# ----------------------------------------------------------------------------

# Ranking metrics. Every one is written so that MORE is FIRST under `descending`, which
# is why "largest" and "most_central" still mean what their names say at the default
# order while the raw coordinate metrics stay literal.
_SELECT_MODES = [
    "largest_face", "smallest_face",
    "left_most", "right_most", "top_most", "bottom_most",
    "centre_most", "closest_to_xy", "detector_score",
]

# Values a saved workflow may still hold. The front end rewrites them on load, reading the
# retired select_order to decide which direction a positional mode meant; this is the
# backstop for anything that reaches the node without passing through it, an API call
# against an old workflow being the usual case.
#
# Direction cannot be recovered here - the widget that carried it is gone - so the
# positional modes assume what select_order defaulted to, which was "descending". That
# is the same assumption that maps "largest" to largest_face rather than smallest_face.
# A workflow that relied on "ascending" resolves to the opposite end and should be
# opened and re-saved once in the UI, where the real direction is still readable.
_SELECT_ALIASES = {
    "largest": "largest_face",
    "area": "largest_face",
    "most_central": "centre_most",
    "confidence": "detector_score",
    "x1": "right_most", "x2": "right_most", "center_x": "right_most",
    "y1": "bottom_most", "y2": "bottom_most", "center_y": "bottom_most",
}


def _resolve_select(mode: str) -> str:
    return _SELECT_ALIASES.get(str(mode), str(mode))


def _select_metric(box, conf: float, W: int, H: int, mode: str,
                   tx: float = None, ty: float = None) -> float:
    """Score one box. HIGHER always wins, for every mode.

    Direction lives in the mode name now, so ranking is one-directional and there is no
    order to get the wrong way round: left_most and right_most are separate modes rather
    than one metric read forwards and backwards.
    """
    x0, y0, x1, y1 = float(box[0]), float(box[1]), float(box[2]), float(box[3])
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0

    if mode == "smallest_face":
        return -(y1 - y0)
    if mode == "left_most":
        return -cx
    if mode == "right_most":
        return cx
    if mode == "top_most":
        return -cy
    if mode == "bottom_most":
        return cy
    if mode == "centre_most":
        return -(((cx - W / 2.0) ** 2 + (cy - H / 2.0) ** 2) ** 0.5)
    if mode == "closest_to_xy":
        px = W / 2.0 if tx is None else float(tx)
        py = H / 2.0 if ty is None else float(ty)
        return -(((cx - px) ** 2 + (cy - py) ** 2) ** 0.5)
    if mode == "detector_score":
        return float(conf)
    return y1 - y0      # largest_face: face HEIGHT, the metric the tracker uses throughout


def _rank_boxes(boxes, confs, W: int, H: int, mode: str,
                tx: float = None, ty: float = None) -> list[int]:
    """Indices of `boxes`, best first. Position 0 is what select_index 0 picks.

    Ties break on x then y, so two equally-good boxes do not swap ranks between frames.
    """
    mode = _resolve_select(mode)
    vals = [_select_metric(b, (confs[i] if i < len(confs) else 1.0), W, H, mode, tx, ty)
            for i, b in enumerate(boxes)]
    return sorted(range(len(boxes)),
                  key=lambda k: (-vals[k], float(boxes[k][0]), float(boxes[k][1])))


# 5x7 digits, drawn straight into the tensor. A bitmap font rather than PIL because the
# default PIL font is a fixed few pixels tall - unreadable on a 1080p frame - and the
# sized variants are not available on every Pillow this pack has to run against.
_GLYPHS = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11111", "00010", "00100", "00010", "00001", "10001", "01110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
}


def _draw_rect(img: torch.Tensor, x0, y0, x1, y1, colour, thickness: int) -> None:
    """Hollow rectangle on an [H,W,3] frame, clipped to the frame."""
    H, W, _ = img.shape
    xa, xb = max(0, min(int(round(x0)), W)), max(0, min(int(round(x1)), W))
    ya, yb = max(0, min(int(round(y0)), H)), max(0, min(int(round(y1)), H))
    if xb <= xa or yb <= ya:
        return
    t = max(1, min(int(thickness), xb - xa, yb - ya))
    c = torch.tensor(colour, dtype=img.dtype, device=img.device)
    img[ya:ya + t, xa:xb] = c
    img[yb - t:yb, xa:xb] = c
    img[ya:yb, xa:xa + t] = c
    img[ya:yb, xb - t:xb] = c


def _draw_crosshair(img: torch.Tensor, x, y, colour, thickness: int) -> None:
    """Full-width and full-height guide lines crossing at (x, y).

    Edge to edge rather than a small cross: the point is being read off a card that gets
    scaled down to look at, and lines that run the whole frame stay findable at any size
    and let the position be judged against the frame edges. A dark edge either side keeps
    them legible over a pale background.
    """
    H, W, _ = img.shape
    cx, cy = int(round(x)), int(round(y))
    t = max(1, int(thickness))
    c = torch.tensor(colour, dtype=img.dtype, device=img.device)
    dark = torch.tensor((0.0, 0.0, 0.0), dtype=img.dtype, device=img.device)
    for pad, col in ((1, dark), (0, c)):
        ya, yb = cy - t // 2 - pad, cy - t // 2 + t + pad
        xa, xb = cx - t // 2 - pad, cx - t // 2 + t + pad
        ya, yb = max(0, min(ya, H)), max(0, min(yb, H))
        xa, xb = max(0, min(xa, W)), max(0, min(xb, W))
        if yb > ya:
            img[ya:yb, :] = col
        if xb > xa:
            img[:, xa:xb] = col


def _label_size(text: str, scale: int) -> tuple[int, int]:
    pad = 2 * scale
    adv = 6 * scale
    return (max(0, len(text) * adv - scale) + 2 * pad, 7 * scale + 2 * pad)


def _draw_label(img: torch.Tensor, x, y, text: str, scale: int, fg, bg) -> None:
    """Digits on a filled plate, nudged back inside the frame if they would fall off."""
    H, W, _ = img.shape
    bw, bh = _label_size(text, scale)
    x0 = 0 if bw >= W else max(0, min(int(round(x)), W - bw))
    y0 = 0 if bh >= H else max(0, min(int(round(y)), H - bh))
    x1, y1 = min(W, x0 + bw), min(H, y0 + bh)
    if x1 <= x0 or y1 <= y0:
        return
    img[y0:y1, x0:x1] = torch.tensor(bg, dtype=img.dtype, device=img.device)
    fgc = torch.tensor(fg, dtype=img.dtype, device=img.device)
    pad, adv = 2 * scale, 6 * scale
    px0 = x0 + pad
    for ch in text:
        rows = _GLYPHS.get(ch)
        if rows is not None:
            for r, row in enumerate(rows):
                for c, on in enumerate(row):
                    if on != "1":
                        continue
                    gx, gy = px0 + c * scale, y0 + pad + r * scale
                    if gx >= W or gy >= H:
                        continue
                    img[gy:min(gy + scale, H), gx:min(gx + scale, W)] = fgc
        px0 += adv


def _shot_preview(images: torch.Tensor, all_boxes, all_confs, segs, picks,
                  mode: str, tx: float = None, ty: float = None):
    """One card per SHOT: the frame that shot locks on, every face outlined and
    numbered, the chosen one filled green. Matches what the picker shows, so the
    numbers on the card are the numbers confirmed_pick takes.
    """
    _, H, W, _ = images.shape
    thick = max(2, int(round(min(H, W) / 240.0)))
    scale = max(2, int(round(min(H, W) / 140.0)))
    _, lh = _label_size("0", scale)

    cards = []
    for k, (a, b) in enumerate(segs):
        pk = picks[k] if k < len(picks) else {}
        frame = int(pk.get("frame", -1))
        if frame < 0:
            frame = next((i for i in range(a, b) if all_boxes[i]), -1)
        if frame < 0:
            cards.append(images[a, ..., :3].clone())
            continue
        img = images[frame, ..., :3].clone()
        ranked = _rank_boxes(all_boxes[frame], all_confs[frame], W, H, mode, tx, ty)
        chosen = -1 if pk.get("absent") else int(pk.get("box", -1))
        for rank, bi in enumerate(ranked):
            box = all_boxes[frame][bi]
            hit = bi == chosen
            col = (0.0, 1.0, 0.2) if hit else (0.15, 0.45, 1.0)
            _draw_rect(img, box[0], box[1], box[2], box[3], col, thick * (2 if hit else 1))
            _draw_label(img, box[0], box[1] - lh, str(rank), scale,
                        (0.0, 0.0, 0.0) if hit else (1.0, 1.0, 1.0), col)
        if mode == "closest_to_xy" and tx is not None and ty is not None:
            # Amber, so it reads against both the blue outlines and the green pick,
            # and drawn big: a card gets scaled down to look at, and a marker sized
            # like the face boxes disappears at that size.
            _draw_crosshair(img, tx, ty, (1.0, 0.75, 0.0),
                            max(3, int(round(min(H, W) / 200.0))))
        cards.append(img)
    if not cards:
        cards.append(images[0, ..., :3].clone())
    return torch.stack(cards, 0), len(cards)


# ----------------------------------------------------------------------------
# 1. track + crop
# ----------------------------------------------------------------------------


class H3FaceTrackCrop:
    """Detect a face per frame, build a smoothed per-frame crop, emit a constant-size batch.

    The crop SIZE varies per frame so the face fills a constant fraction of every
    crop; every crop is then resized to one canvas size, because H3 generates a
    single fixed WxH for a whole sequence. Result: the face is always large in
    H3's input regardless of how small it was in the source frame.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "detector": (_detector_list(),),
                "confidence": ("FLOAT", {"default": 0.35, "min": 0.05, "max": 0.95, "step": 0.05}),
                "crop_factor": ("FLOAT", {"default": 2.5, "min": 1.2, "max": 8.0, "step": 0.1,
                    "tooltip": "Crop side as a multiple of detected face HEIGHT. 2.5 puts the "
                               "face at ~40% of the crop, comfortably inside H3's good regime. "
                               "Bigger = more context so the seam lands in hair/background, but "
                               "less magnification. 2.0-3.0 is the useful range."}),
                "canvas_width": ("INT", {"default": 768, "min": 128, "max": 1344, "step": 32,
                    "tooltip": "Resolution H3 generates at. 768 is H3's native short edge and "
                               "the default here; 512 is cheaper. In manual mode this is used "
                               "exactly as typed, high or low. Ignored when canvas_mode is not "
                               "'manual', where the canvas comes from the crop instead and "
                               "never falls below 512x512. Cost scales with area: 768 is 2.25x "
                               "the latent tokens of 512."}),
                "canvas_height": ("INT", {"default": 768, "min": 128, "max": 1344, "step": 32}),
                "canvas_mode": (["manual", "auto_no_downscale", "auto_capped_768"],
                    {"default": "manual",
                     "tooltip": "manual: use canvas_width/height as given.\n"
                                "auto_no_downscale: size the canvas from the LARGEST crop in "
                                "the video so no frame is ever downscaled (magnification never "
                                "drops below 1.0x). Can get expensive on videos that include "
                                "close-ups.\n"
                                "auto_capped_768: same, but clamped to 768 - H3's native short "
                                "edge and a sane VRAM ceiling.\n"
                                "Both auto modes clamp UP to a minimum of 512x512, whatever "
                                "the crop, so a small face in a low-resolution clip is still "
                                "magnified. manual is not clamped - it uses canvas_width and "
                                "canvas_height exactly as typed."}),
                "smooth_window": ("INT", {"default": 21, "min": 1, "max": 201, "step": 2,
                    "tooltip": "Frames of smoothing on the crop CENTRE. 21 at 24fps is ~0.9s. "
                               "Raise if the box still shivers; lower if it lags behind fast "
                               "head movement."}),
                "size_smooth_window": ("INT", {"default": 51, "min": 1, "max": 201, "step": 2,
                    "tooltip": "Frames of smoothing on the crop SIZE. Wants MORE than the "
                               "centre: size jitter makes the crop breathe, which changes the "
                               "resample factor every frame and reads as shimmer. Real zoom "
                               "moves are slow, so heavy smoothing here costs nothing."}),
                "smooth_method": (["gaussian", "savgol", "moving_average"], {"default": "gaussian",
                    "tooltip": "gaussian: best jitter rejection. savgol: preserves the shape of "
                               "a push-in better at large windows. moving_average: the old "
                               "boxcar, leaves residual jitter."}),
                "size_mode": (["max_of_clip", "per_frame"], {"default": "per_frame",
                    "tooltip": "per_frame: constant face-fraction in every crop (correct for "
                               "push-ins). max_of_clip: one size for the whole video, only "
                               "useful when the shot is genuinely static."}),
            },
            "optional": {
                "identity_reference": ("IMAGE", {
                    "tooltip": "A clear face image of the person to track. When supplied, the "
                               "subject is chosen by FACE IDENTITY rather than by size, so a "
                               "crowd scene locks onto the right person even when someone else "
                               "is briefly larger or nearer.\n\n"
                               "OPTIONAL on this node. identity_track works without it: the "
                               "anchor is then built FROM THE CLIP, off frames where one face "
                               "clearly dominates, which is usually the better anchor since a "
                               "stylised reference sits in a different domain. Supply one only "
                               "to name a specific person.\n\n"
                               "Read only while identity_track is ON. With it off this input "
                               "is ignored, and the report says so.\n\n"
                               "Without either, 'largest_face' has no notion of WHO it is "
                               "following - it just takes the biggest box each frame, which "
                               "switches subject whenever the framing changes.\n\n"
                               "FOR MULTIPLE PEOPLE: run the pipeline once per subject, each "
                               "with that person's reference here and their own refs on the H3 "
                               "node, and chain them - feed run 1's stitched output in as run "
                               "2's base_images. The composites accumulate."}),
                "identity_track": ("BOOLEAN", {"default": True,
                    "tooltip": "Hold one subject through a crowd. Continuity (nearest box to "
                               "the previous position) decides most frames; the face-identity "
                               "embedding is consulted only when two candidates are similarly "
                               "plausible or their boxes overlap - which is both the accurate "
                               "and the cheap arrangement, since the embedding model then runs "
                               "on a handful of frames instead of all of them. "
                               "The anchor is taken FROM THE CLIP by default (frames where one "
                               "face clearly dominates), because an external stylised reference "
                               "sits in a different domain - measured similarity between an "
                               "illustration and a render of the same character was only 0.305, "
                               "where same-domain faces score 0.5-0.7.\n\n"
                               "This switch also gates identity_reference: with it OFF a connected "
                               "reference is not read at all, and the subject is chosen by select "
                               "instead. The report says so when that happens."}),
                "identity_threshold": ("FLOAT", {"default": 0.28, "min": 0.0, "max": 1.0,
                    "step": 0.01,
                    "tooltip": "Minimum score to accept a face as the reference person. Below "
                               "this the frame falls back to continuity (nearest to the "
                               "previous position at a similar size), which is what carries "
                               "tracking through profiles and partial occlusion where "
                               "embeddings become unreliable.\n"
                               "The scale depends on identity_model. 0.28 suits insightface "
                               "cosine; clip_vision sits much higher (~0.80) because its "
                               "similarities are compressed; ccip scores 0.5 at its own "
                               "published operating point.\n"
                               "SET 0 to use whichever default the chosen model recommends. "
                               "The report prints the scores actually seen, so it can be tuned "
                               "from evidence rather than guessed."}),
                "select": (_SELECT_MODES, {"default": "largest_face",
                    "tooltip": "How the subject is chosen. It is chosen ONCE per shot, on "
                               "the first frame that holds it, and continuity follows that "
                               "same face from there - it is NOT re-ranked each frame. A "
                               "subject who walks from the left of frame to the right while "
                               "someone else crosses the other way is still tracked "
                               "correctly.\n\n"
                               "largest_face / smallest_face: biggest or smallest face by "
                               "height.\n"
                               "left_most / right_most / top_most / bottom_most: by the "
                               "CENTRE of the face box.\n"
                               "centre_most: nearest the centre of the frame.\n"
                               "closest_to_xy: nearest the X, Y you give, measured on "
                               "frame_index.\n"
                               "detector_score: the detection the detector is most confident "
                               "about.\n\n"
                               "AT A HARD CUT a rank means nothing across the join - everyone "
                               "is renumbered. With cut_detection ON, each shot chooses again "
                               "by this rule, so a cut can land on a different person. With it "
                               "OFF the video counts as one shot and continuity runs straight "
                               "through a real cut onto whichever face is nearest the last "
                               "position, which may be anyone.\n\n"
                               "To hold one person across cuts, wire identity_reference, or "
                               "choose the face in H3 Load Video + Face Select.\n\n"
                               "Used only when no identity_reference is connected."}),
                "fallback_detector": (["none"] + _detector_list(), {"default": "none",
                    "tooltip": "Used only on frames where the FACE detector finds nothing "
                               "(subject turned away). A person/body model such as "
                               "segm\\person_yolov8m-seg.pt gives a real head position from the "
                               "top of the body box, which beats interpolating blindly between "
                               "the last and next face. Set 'none' to interpolate instead."}),
                "fallback_head_frac": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1.5, "step": 0.05,
                    "tooltip": "Head centre as a multiple of face height below the top of the "
                               "person box. 0.5 puts it half a face-height down, which is about "
                               "right for a head seen from behind."}),
                # These two sit at the END of the list on purpose: ComfyUI stores widget
                # values positionally, so inserting next to `select` would shift the two
                # fallback_* values in every workflow already saved against this node.
                "select_index": ("INT", {"default": 0, "min": 0, "max": 63,
                    "tooltip": "Which face in that ranking to track. 0 is the first, 1 the "
                               "second, and so on.\n"
                               "The subject is locked on the first frame that actually contains "
                               "this index, so a video that opens on one face and only later "
                               "shows a crowd still finds face 1. Frames before that lock-on are "
                               "interpolated from it and faded out of the composite, the same as "
                               "a detection dropout; the report counts them. Clamped, with a "
                               "warning in the report, if the video never shows that many faces "
                               "at once. Ignored entirely when identity_reference is connected.\n"
                               "H3 Load Video + Face Select shows the faces numbered, if "
                               "you need to see which number is who."}),
                "identity_model": (_IDENT_MODES, {"default": "insightface",
                    "tooltip": "Which model decides that two faces are the same person.\n"
                               "insightface: ArcFace/buffalo_l. Photographed human faces. It "
                               "reaches faces through its OWN detector, which does not fire on "
                               "illustration - so on anime it finds no candidates at all and "
                               "tracking quietly falls back to continuity.\n"
                               "clip_vision: ComfyUI's CLIP vision, wired into "
                               "identity_clip_vision from a CLIPVisionLoader. No extra install, "
                               "works on any domain - anime, 3D, stylised - but it describes "
                               "appearance rather than identity, so two characters with a "
                               "similar palette can collide.\n"
                               "ccip: the illustration counterpart of ArcFace, purpose-built "
                               "for 'same anime character?'. Best on anime. Needs "
                               "'pip install dghs-imgutils', which this pack deliberately does "
                               "not install for you - it pins numpy<2 and pulls "
                               "opencv-contrib-python.\n"
                               "The last two embed the boxes YOUR detector found, so pair them "
                               "with an anime face model in the detector slot."}),
                "cut_detection": (_CUT_MODES, {"default": "none",
                    "tooltip": "Hard-cut detection, so the crop is not smoothed ACROSS a cut.\n"
                               "none: off. Correct for single-shot videos, which is what H3 "
                               "generates.\n"
                               "auto: split at the cuts and treat each shot as its own "
                               "window for the smoothing, interpolation and composite "
                               "fade. The canvas is still sized once for the whole video."}),
                "cut_threshold": ("FLOAT", {"default": 3.0, "min": 0.5, "max": 20.0,
                    "step": 0.25,
                    "tooltip": "How far a frame has to stand out from its NEIGHBOURS to count "
                               "as a cut. 3.0 is PySceneDetect's adaptive default.\n"
                               "Lower catches more and risks splitting a continuous shot, "
                               "which costs smoothing on both sides. Higher takes only "
                               "unambiguous cuts.\n"
                               "Only used when cut detection is on. The report says how many "
                               "cuts were found and where, so tune it from that."}),
                "identity_clip_vision": ("CLIP_VISION", {
                    "tooltip": "A CLIP vision model from a CLIPVisionLoader, used only when "
                               "identity_model is clip_vision. Any of the usual ComfyUI "
                               "clip_vision checkpoints works; the bigger ViT-L/H models "
                               "separate characters better than the small ones."}),
                "absent_shots": (["off", "by_identity"], {"default": "off",
                    "tooltip": "Find shots the subject is not in, so they are not rendered "
                               "at all.\n"
                               "by_identity: a shot where no sampled face matches the "
                               "identity anchor above identity_threshold is treated as not "
                               "containing the subject. Its frames keep their original pixels "
                               "and drop out of the batch. Needs identity matching to be "
                               "working - it does nothing without an anchor.\n"
                               "Sampled, not exhaustive, so a brief appearance can be missed. "
                               "The report names every shot it drops and the score it saw."}),
                # At the END of the list on purpose: ComfyUI stores widget values
                # positionally, so these three have to follow everything already saved.
                "X": ("INT", {"default": 0, "min": 0, "max": 16384, "step": 1,
                    "tooltip": "Only used by select=closest_to_xy.\n\n"
                               "Horizontal position in PIXELS of the source video frame, "
                               "measured from the TOP-LEFT corner, increasing to the right. "
                               "On a 960x544 clip, 0 is the left edge, 960 the right, 480 the "
                               "middle.\n\n"
                               "The point does not have to sit on a face: the nearest face "
                               "CENTRE wins, however far away it is."}),
                "Y": ("INT", {"default": 0, "min": 0, "max": 16384, "step": 1,
                    "tooltip": "Only used by select=closest_to_xy.\n\n"
                               "Vertical position in PIXELS of the source video frame, "
                               "measured from the TOP-LEFT corner, increasing DOWNWARD. On a "
                               "960x544 clip, 0 is the top edge, 544 the bottom, 272 the "
                               "middle."}),
                "frame_index": ("INT", {"default": 0, "min": 0, "max": 999999, "step": 1,
                    "tooltip": "Only used by select=closest_to_xy.\n\n"
                               "The frame the X, Y measurement is taken on, counting from 0 "
                               "for the FIRST frame. It counts the frames this node loaded, so "
                               "with skip_first_frames or select_every_nth set it counts from "
                               "the first frame kept, not the first frame of the file.\n\n"
                               "The face found there is followed forwards and backwards "
                               "through its shot, so pick a frame where the subject is clearly "
                               "visible rather than one the detector may miss them on."}),
                "face_pick": ("H3FACEPICK", {
                    "tooltip": "The subject chosen upstream by H3 Load Video + Face Select.\n"
                               "Decides who to follow, per shot, and carries the boxes and "
                               "shot boundaries with it so this node does not detect the "
                               "video again. select, select_index, cut_detection and "
                               "cut_threshold are then unused, and detector and "
                               "confidence are too unless a fallback_detector or a "
                               "crop-based identity_model still needs them - they are then "
                               "unused - the report names the detector that actually "
                               "produced the boxes."}),
            },
        }

    # canvas_w / canvas_h MUST be wired into the H3 node's width/height. With canvas_mode
    # on auto the tracker decides the size, and nothing downstream can know it otherwise -
    # the crop and the AV latent would disagree and H3InjectVideoLatent would refuse.
    RETURN_TYPES = ("IMAGE", "H3FACEXFORM", "IMAGE", "STRING", "INT", "INT", "INT")
    RETURN_NAMES = ("crops", "transform", "preview", "report", "canvas_w", "canvas_h",
                    "frame_count")
    FUNCTION = "run"
    CATEGORY = "MiniMax H3/Face Refine"
    DESCRIPTION = (
        "Per-frame face track -> smoothed, normalised crop -> constant-size batch for H3, "
        "plus the transform needed to paste the result back."
    )

    def run(self, images, detector, confidence, crop_factor, canvas_width, canvas_height,
            canvas_mode, smooth_window, size_smooth_window, smooth_method, size_mode,
            select="largest_face", fallback_detector="none", fallback_head_frac=0.5,
            identity_reference=None, identity_threshold=0.28, identity_track=True,
            select_index=0, identity_model="insightface",
            identity_clip_vision=None, cut_detection="none", cut_threshold=3.0,
            absent_shots="off", X=0, Y=0, frame_index=0, face_pick=None):
        # Loaded on demand: with a face_pick the clip is never detected here, and the
        # insightface backend brings its own, so a run can finish without this file.
        _model_box = []

        def _get_model():
            if not _model_box:
                _model_box.append(_load_detector(detector))
            return _model_box[0]
        B, H, W, _ = images.shape

        cx = np.zeros(B); cy = np.zeros(B); sz = np.zeros(B); fw = np.zeros(B)
        valid = np.zeros(B, dtype=bool)       # a real FACE was seen
        via_body = np.zeros(B, dtype=bool)    # head located from a body box instead

        import comfy.model_management as _mm

        # ---- detect once, keep every box -------------------------------------------
        # Detection is the expensive part of this node and the boxes themselves are tiny,
        # so the whole clip is detected up front and cached. Subject selection, the
        # identity anchor and the index preview then all read the same set instead of
        # each re-running the detector over the frames they care about.
        all_boxes: list = []
        all_confs: list = []
        cuts: list = []
        cut_det = cut_tc = None
        cut_note = ""
        pick_note = ""
        segs_given = None
        forced = {}
        absent_frames = set()

        if face_pick is not None:
            # Detection already happened upstream; repeating it would be a second full
            # pass over the clip for boxes already in hand.
            if int(face_pick.get("frames", -1)) != B:
                raise ValueError(
                    f"face_pick describes {face_pick.get('frames')} frames but {B} arrived. "
                    f"The images and the pick have to come from the same loader - wire both "
                    f"from H3 Load Video + Face Select, and do not trim between them."
                )
            _src = face_pick.get("src_size") or (0, 0)
            if int(_src[0] or 0) and (int(_src[0]) != W or int(_src[1]) != H):
                raise ValueError(
                    f"face_pick describes {_src[0]}x{_src[1]} frames but {W}x{H} arrived. "
                    f"Every box would land in the wrong place. Do not resize between "
                    f"H3 Load Video + Face Select and this node."
                )
            all_boxes = [[[float(v) for v in q] for q in fr] for fr in face_pick["boxes"]]
            all_confs = [[float(c) for c in fr] for fr in face_pick["confs"]]
            segs_given = [(int(x), int(y)) for x, y in (face_pick.get("segments") or [])]
            # One forced subject per shot, so the same person can sit at a different
            # index either side of a cut.
            for pk in face_pick.get("picks") or []:
                if pk.get("absent"):
                    # Not "no face found" - the subject is not in this shot, so no
                    # frame of it gets a subject and the composite fades out across
                    # it, leaving the original pixels.
                    a2, b2 = int(pk["segment"][0]), int(pk["segment"][1])
                    absent_frames.update(range(max(0, a2), min(B, b2)))
                elif int(pk.get("frame", -1)) >= 0:
                    _f, _b = int(pk["frame"]), int(pk.get("box", -1))
                    # Guarded like every other read of a pick index: _track_back
                    # dereferences this directly and would raise on a malformed pick.
                    if 0 <= _f < B and 0 <= _b < len(all_boxes[_f]):
                        forced[_f] = _b
            pick_note = (f"boxes from face_pick (detector {face_pick.get('detector')}, "
                         f"confidence {face_pick.get('confidence')}), "
                         f"{len(forced)} shot(s) with a chosen subject")
        else:
            # Rides along on this pass: the BGR conversion below is needed anyway and
            # is what scenedetect consumes, so cuts cost no second decode.
            if cut_detection != "none":
                try:
                    cut_det, cut_tc = _make_cut_detector(cut_threshold)
                except Exception as exc:
                    # Cut detection is an assist, not a requirement - a missing or broken
                    # scenedetect must not kill a run that smooths fine as one shot.
                    cut_note = f"cut detection unavailable, treating the video as one shot: {exc}"
                    print(f"[H3FaceRefine] {cut_note}")

            for i in range(B):
                _mm.throw_exception_if_processing_interrupted()
                bgr = _to_bgr_u8(images[i])
                if cut_det is not None:
                    # fps only feeds scenedetect's own timing; cuts come back as frame
                    # numbers either way, and this node never sees a real frame rate.
                    for _t in cut_det.process_frame(cut_tc(i, fps=24.0), bgr):
                        cuts.append(int(_t.get_frames()))
                res = _get_model().predict(bgr, conf=confidence, verbose=False)[0]
                if len(res.boxes):
                    bx = [[float(v) for v in q] for q in res.boxes.xyxy.tolist()]
                    cf = getattr(res.boxes, "conf", None)
                    all_boxes.append(bx)
                    all_confs.append([float(c) for c in cf.tolist()] if cf is not None
                                     else [1.0] * len(bx))
                else:
                    all_boxes.append([])
                    all_confs.append([])

        segs = segs_given if segs_given else _segments(B, cuts)

        max_faces = max((len(b) for b in all_boxes), default=0)
        if max_faces == 0:
            raise ValueError(
                "No face detected in any frame. Lower `confidence`, or this video has no "
                "usable face and should be skipped."
            )

        # ---- which face is the subject ---------------------------------------------
        # select ranks the boxes within a frame; select_index takes one out
        # of that ranking. The pick happens ONCE, on the first frame that actually
        # contains the requested index, and continuity carries the subject from there: a
        # rank is a per-frame property, so re-ranking every frame would hop between people
        # the moment two of them cross or change size.
        requested_index = int(select_index)
        select_index = max(0, min(requested_index, max_faces - 1))
        first_face = next(i for i in range(B) if all_boxes[i])
        select = _resolve_select(select)
        if select == "closest_to_xy":
            # The frame the user named is where the measurement belongs. If it holds no
            # face, the search moves forward from there rather than back to the start,
            # so the lock stays as close to the named moment as the detections allow.
            _want = min(max(0, int(frame_index)), B - 1)
            index_lock = next((i for i in range(_want, B)
                               if len(all_boxes[i]) > select_index),
                              next(i for i in range(B)
                                   if len(all_boxes[i]) > select_index))
        else:
            index_lock = next(i for i in range(B) if len(all_boxes[i]) > select_index)
        selection_default = (select == "largest_face" and select_index == 0)
        # Named in the report so a surprising pick is traceable to what was asked for,
        # including the frame the measurement actually landed on.
        _sel_desc = (f"select={select} nearest X={int(X)} Y={int(Y)} measured on frame "
                     f"{index_lock} (asked for {int(frame_index)}) index={select_index}"
                     if select == "closest_to_xy"
                     else f"select={select} index={select_index}")
        # A named subject outranks the automatic "the biggest face is the subject"
        # guess, so the identity anchor is built from THAT pick instead. A face_pick
        # names one per shot, and the tracker's own select widgets are unused while it
        # is connected, so they cannot be what decides this.
        selection_leads = identity_reference is None and (
            bool(forced) or not selection_default)

        # ---- identity anchor -------------------------------------------------------
        # Without one, "largest" has no idea WHO it is following: it takes the biggest box
        # each frame, so in a crowd it hops subject whenever the framing changes. Observed
        # in testing: a single clip switched subject 4 times.
        ref_emb, embedder = None, None
        ref_failed = False
        pick_warn = ""
        ident_note, ident_scores = "", []
        ident_threshold = float(identity_threshold)
        n_ident, n_cont, n_conflict = 0, 0, 0
        # max_faces, not frame 0: a clip that opens on one face - or on none, which a
        # wide shot often does - and fills up later still needs an anchor built.
        multi = max_faces > 1

        if identity_track and (multi or identity_reference is not None):
            try:
                embedder = _make_embedder(identity_model, identity_clip_vision)
                if ident_threshold <= 0.0:
                    ident_threshold = float(embedder.default_threshold)
                if identity_reference is not None:
                    # _get_model() would load the detector file just to hand it to a
                    # backend that ignores it - and with a face_pick wired there is no
                    # other reason to load it at all, so a stale detector value would
                    # fail a run that never needed it.
                    _det = (_get_model()
                            if getattr(embedder, "needs_detector", True) else None)
                    ref_emb = embedder.embed_reference(identity_reference[:1],
                                                       _det, confidence)
                    if ref_emb is not None:
                        print("[H3FaceRefine] identity anchor from the supplied reference")
                    else:
                        # Only THIS backend can say whether the reference is usable -
                        # insightface reads it with SCRFD, the crop backends with the
                        # tracker's detector - so a second opinion from anything else
                        # would be answering a different question. Say which one failed,
                        # because the fix is usually to try another backend.
                        ref_failed = True
                if ref_emb is None and selection_leads:
                    # Anchor on the SELECTED subject, followed by continuity. Falling back
                    # to the clip anchor here would re-anchor on the dominant face, i.e.
                    # exactly the person select_index was used to avoid, so a failure here
                    # leaves ref_emb None and the track runs on continuity alone.
                    if forced:
                        _keep, _odd = _shot_anchors(embedder, images, all_boxes, segs,
                                                    forced, B, ident_threshold)
                        if _keep:
                            ref_emb = (embedder.merge(_keep) if len(_keep) > 1
                                       else _keep[0])
                            used = len(_keep)
                        if _odd:
                            pick_warn = (
                                f"\n!! shot(s) {_odd} hold a different face from the rest, "
                                f"so they were left out of the identity anchor.")
                    else:
                        seed = _rank_boxes(all_boxes[index_lock], all_confs[index_lock],
                                           W, H, select, X, Y)[select_index]
                        _tr = _track_continuity(all_boxes, index_lock, seed)
                        _sb = all_boxes[index_lock][seed]
                        ref_emb, used = _anchor_from_track(
                            embedder, images, all_boxes, _tr,
                            min_face=min(32.0, 0.9 * (_sb[3] - _sb[1])))
                    print(f"[H3FaceRefine] identity anchor built from the selected subject "
                          f"({used} unambiguous frames)" if ref_emb is not None else
                          "[H3FaceRefine] no clean frames to anchor the selected subject - "
                          "tracking by continuity alone")
                elif ref_emb is None:
                    if ref_failed:
                        print("[H3FaceRefine] !! no face found in identity_reference "
                              f"by {embedder.name}")
                    ref_emb, used = _build_clip_anchor(embedder, images, all_boxes)
                    if ref_emb is not None:
                        print(f"[H3FaceRefine] identity anchor built from the video itself "
                              f"({used} unambiguous frames)")
            except Exception as exc:
                # Identity is an assist, not a requirement - a missing backend must not
                # kill a run that continuity can still track. Surfaced in the report as
                # well as stdout, because a silent downgrade is the thing that wastes an
                # evening on a crowd scene.
                embedder, ref_emb = None, None
                ident_note = f"{identity_model} unavailable, tracking by continuity: {exc}"
                print(f"[H3FaceRefine] identity matching unavailable ({exc})")

        # Waiting for a frame that contains select_index only makes sense when the RANKING
        # is what picks the subject. A usable identity anchor overrules the ranking at
        # lock-on, so deferring there would discard leading frames identity resolves
        # perfectly - frames where the subject is often alone, i.e. the most reliable ones
        # in the clip.
        # Shots the reference person is not in. Marked absent, so those frames keep
        # their original pixels and drop out of the batch entirely.
        absent_note = ""
        if absent_shots == "by_identity" and ref_emb is not None and embedder is not None:
            try:
                _bad, _scores = _shots_without_subject(
                    embedder, images, all_boxes, segs, ref_emb, ident_threshold)
                for k in _bad:
                    absent_frames.update(range(segs[k][0], segs[k][1]))
                absent_note = (
                    ("no subject in shot(s) " + ", ".join(
                        f"{k + 1} (best {_scores.get(k, -1):.3f})" for k in _bad))
                    if _bad else "every shot contains the subject")
            except Exception as exc:
                absent_note = f"absent-shot detection failed: {exc}"
                print(f"[H3FaceRefine] {absent_note}")
        elif absent_shots == "by_identity":
            absent_note = "absent_shots=by_identity needs a usable identity anchor; ignored"

        ranking_picks = selection_leads or ref_emb is None
        lock_frame = index_lock if ranking_picks else first_face

        last = None   # (cx, cy, size) of the subject on the previous resolved frame
        last_i = -1   # the frame `last` was resolved on
        heights = []  # the subject's face heights on frames resolved since the last cut
        lost = []     # frames holding faces, none of them within reach of the subject
        crowd = [False] * B
        for _a, _b in segs:
            _c = _holds_crowd(all_boxes, _a, _b)
            for _j in range(max(0, _a), min(B, _b)):
                crowd[_j] = _c
        seg_starts = {int(x) for x, _ in segs}

        # Where each frame's shot locks on: per shot with a face_pick, otherwise the
        # single clip-wide lock as before.
        if forced:
            lock_of = [0] * B
            # Frames before a shot's lock, resolved backwards from it. Only where the
            # subject was named, so this never guesses on a rule-based lock.
            backfill = {}
            for _pk in (face_pick.get("picks") or []):
                _a, _b = int(_pk["segment"][0]), int(_pk["segment"][1])
                _lf = int(_pk.get("frame", -1))
                for _i in range(max(0, _a), min(B, _b)):
                    lock_of[_i] = _lf if _lf >= 0 else _a
                if _lf > _a and not _pk.get("absent") and all_boxes[_lf]:
                    _bi = int(_pk.get("box", -1))
                    if 0 <= _bi < len(all_boxes[_lf]):
                        backfill.update(_track_back(all_boxes, _lf, _bi, max(0, _a)))
        else:
            lock_of, backfill = None, {}
            if select == "closest_to_xy" and all_boxes[index_lock]:
                # A frame the user named is as much a naming of the subject as a
                # face_pick is, so the frames before it are that same person walking
                # backwards - the one rule-based lock where guessing backwards is not
                # a guess. Without this, moving frame_index later would quietly drop
                # everything before it out of the composite.
                _rk = _rank_boxes(all_boxes[index_lock], all_confs[index_lock],
                                  W, H, select, X, Y)
                if _rk:
                    _bi = _rk[min(select_index, len(_rk) - 1)]
                    _a = max((a for a, b in segs if a <= index_lock < b), default=0)
                    backfill = _track_back(all_boxes, index_lock, _bi, _a)

        for i in range(B):
            _mm.throw_exception_if_processing_interrupted()
            if i in seg_starts:
                # A cut ends continuity - "nearest box to where the subject was" means
                # nothing once the camera has changed shot.
                last = None
                heights = []
            if i in absent_frames:
                continue
            boxes = all_boxes[i]
            if not boxes:
                continue
            if (last is None and i not in backfill
                    and i < (lock_of[i] if lock_of is not None else lock_frame)):
                # Too few faces here to honour the selection. Leaving these frames
                # unresolved hands them to the same interpolation that covers detection
                # dropouts, which beats locking onto whoever happens to be alone.
                continue

            b = None
            if i in forced and 0 <= forced[i] < len(boxes):
                # Chosen upstream for this shot; take it verbatim.
                b = boxes[forced[i]]
                n_cont += 1
            elif i in backfill and 0 <= backfill[i] < len(boxes):
                # Before this shot's lock, resolved by continuity run backwards from it.
                b = boxes[backfill[i]]
                n_cont += 1
            elif last is None and len(boxes) == 1:
                b = boxes[0]
                n_cont += 1
            elif last is None:
                # first resolved frame: identity if we have it, else the ranking rule
                if ref_emb is not None and not selection_leads:
                    cands = embedder.embed(images[i:i + 1], boxes)
                    k, score = embedder.best_match(cands, ref_emb)
                    # Deliberately unthresholded, unlike the mid-shot path below. A
                    # small, poorly detailed face embeds weakly, and that face is the
                    # subject this node exists to fix; there is also no continuity yet
                    # to fall back on, so the best available match beats handing the
                    # choice to the ranking rule. Choose the face manually when it is
                    # the wrong one.
                    if k is not None:
                        ident_scores.append(score)
                        b = cands[k][0]
                        n_ident += 1
                if b is None:
                    # Clamped: when identity was in charge the lock frame is only
                    # guaranteed to hold a face, not to hold select_index of them, and
                    # insightface can come back empty on a frame the face detector liked.
                    ranked = _rank_boxes(boxes, all_confs[i], W, H, select, X, Y)
                    b = boxes[ranked[min(select_index, len(ranked) - 1)]]
                    n_cont += 1
            else:
                # Only faces within reach of where the subject was can continue it.
                if crowd[i]:
                    _h = _typical_height(heights)
                    reach = [q for q in boxes if _within_reach(q, last, i - last_i, _h)]
                else:
                    reach = boxes
                if not reach:
                    # The subject's face is not detected here, or is too far from where it
                    # was to be the same person. The frame stays unresolved and fades out
                    # of the composite; the subject is picked up again when a face
                    # reappears within reach of where it was lost.
                    lost.append(i)
                    continue
                elif len(reach) == 1:
                    b = reach[0]
                    n_cont += 1
                else:
                    # Continuity first: the nearest box to where the subject was, penalised
                    # for size change. Cheap and correct while people stay separated.
                    ranked = sorted(reach, key=lambda q: _continuity_cost(q, last))
                    best, second = ranked[0], ranked[1]
                    c0, c1 = _continuity_cost(best, last), _continuity_cost(second, last)

                    # AMBIGUOUS when two candidates are similarly plausible, or their boxes
                    # overlap - exactly when continuity alone picks the wrong person. Only
                    # then is the embedding worth computing.
                    conflict = (c1 < c0 * 2.0) or (_iou(best, second) > 0.2)

                    if conflict and ref_emb is not None:
                        n_conflict += 1
                        near = ([q for q in reach if _continuity_cost(q, last) < c0 * 3.0]
                                or reach)
                        cands = [c for c in embedder.embed(images[i:i + 1], near)
                                 if any(_iou(c[0], q) > 0.3 for q in near)]
                        k, score = embedder.best_match(cands, ref_emb)
                        if k is not None:
                            ident_scores.append(score)
                        if k is not None and score >= ident_threshold:
                            b = cands[k][0]
                            n_ident += 1
                    if b is None:
                        # No conflict, or the embedding was not confident enough. Embeddings
                        # degrade on profiles and occlusion - precisely where the subject is
                        # hardest to hold - so continuity is the safer default there.
                        b = best
                        n_cont += 1

            last = ((b[0]+b[2])/2.0, (b[1]+b[3])/2.0, b[3]-b[1])
            last_i = i
            heights.append(last[2])
            cx[i] = (b[0] + b[2]) / 2.0
            cy[i] = (b[1] + b[3]) / 2.0
            sz[i] = b[3] - b[1]          # face HEIGHT: more stable than width as the head turns
            fw[i] = b[2] - b[0]          # face WIDTH: needed for the FaceDetailer-style paste mask
            valid[i] = True

        found = int(valid.sum())
        if found == 0:
            if absent_frames and len(absent_frames) >= B:
                raise ValueError(
                    "Every shot is marked as not containing the subject, so there is "
                    "nothing to refine. Unmark a shot in Pick faces, or leave this video out."
                )
            raise ValueError(
                "No face detected in any frame. Lower `confidence`, or this video has no "
                "usable face and should be skipped."
            )

        # Body fallback for frames the face detector missed. Interpolated size feeds the
        # head-position estimate, so size comes from frames where a face WAS measured while
        # position comes from the body actually visible in this frame.
        #
        # Keyed on "no face was detected", NOT on "no subject was resolved": the frames the
        # lock deferral holds back DID have faces, and the body model would re-centre them
        # on the largest body in shot - usually the very person select_index was used to
        # avoid - then mark them via_body, which hides them from both the interpolation and
        # the dropout warning while smoothing that error into the frames after the lock.
        no_face = np.array([not b for b in all_boxes], dtype=bool)
        sz_seed = _interp_gaps_seg(sz, valid, segs)
        cx_seed = _interp_gaps_seg(cx, valid, segs)
        cy_seed = _interp_gaps_seg(cy, valid, segs)
        if fallback_detector != "none" and no_face.any():
            try:
                bmodel = _load_detector(fallback_detector)
                for i in np.nonzero(no_face)[0]:
                    res = bmodel.predict(_to_bgr_u8(images[i]), conf=confidence, verbose=False)[0]
                    if not len(res.boxes):
                        continue
                    bb = res.boxes.xyxy.tolist()
                    cls = (res.boxes.cls.tolist() if getattr(res.boxes, "cls", None) is not None
                           else [0] * len(bb))
                    people = [q for q, cc in zip(bb, cls) if int(cc) == 0] or bb
                    # The body whose head lands nearest where the subject is expected. In
                    # a group the largest body is often someone else.
                    _hd = fallback_head_frac * max(sz_seed[i], 8.0)
                    p = min(people, key=lambda q: ((q[0] + q[2]) / 2.0 - cx_seed[i]) ** 2
                            + (q[1] + _hd - cy_seed[i]) ** 2)
                    cx[i] = (p[0] + p[2]) / 2.0
                    cy[i] = p[1] + fallback_head_frac * max(sz_seed[i], 8.0)
                    sz[i] = sz_seed[i]
                    via_body[i] = True
            except Exception as exc:  # never let the fallback kill the run
                print(f"[H3FaceRefine] body fallback '{fallback_detector}' failed: {exc}")

        known = valid | via_body
        raw_cx = _interp_gaps_seg(cx, known, segs)
        raw_cy = _interp_gaps_seg(cy, known, segs)
        raw_sz = _interp_gaps_seg(sz, valid, segs)  # size ALWAYS from real face measurements
        raw_fw = _interp_gaps_seg(fw, valid, segs)
        sm_fw = _smooth_seg(raw_fw, size_smooth_window, smooth_method, segs)
        cx = _smooth_seg(raw_cx, smooth_window, smooth_method, segs)
        cy = _smooth_seg(raw_cy, smooth_window, smooth_method, segs)
        sz = _smooth_seg(raw_sz, size_smooth_window, smooth_method, segs)
        if size_mode == "max_of_clip":
            # Clip-wide on purpose, cuts or not: H3 generates one width/height for the
            # batch, and a single IMAGE batch cannot carry a canvas per shot.
            sz[:] = sz.max()

        # Shots the subject is not in. The section stays in the batch - the clip is one
        # H3 generation and cutting a hole in the middle would splice two unrelated
        # shots together - but there is no face to follow, so the crop is a centred box
        # of the frame rather than a position interpolated from other shots. Set after
        # smoothing so it lands exactly centred; the segment is its own smoothing
        # window, so neighbouring shots are not dragged toward the centre.
        absent = np.zeros(B, dtype=bool)
        if absent_frames:
            absent[np.array(sorted(absent_frames), dtype=int)] = True
            live = (~absent) & valid
            _sz = float(np.median(sz[live])) if live.any() else float(min(W, H)) / 3.0
            _fw = float(np.median(sm_fw[live])) if live.any() else _sz
            cx[absent] = W / 2.0
            cy[absent] = H / 2.0
            sz[absent] = _sz
            sm_fw[absent] = _fw

        # frame-to-frame movement, before vs after: the number that corresponds to
        # visible jitter. Residual is what still moves after smoothing.
        def _jit(a):
            return float(np.abs(np.diff(a)).mean()) if len(a) > 1 else 0.0

        jit_before = (_jit(raw_cx) + _jit(raw_cy)) / 2.0
        jit_after = (_jit(cx) + _jit(cy)) / 2.0
        sz_before, sz_after = _jit(raw_sz), _jit(sz)

        # Size the canvas from the clip itself so no frame is ever downscaled. The largest
        # crop is (largest smoothed face height) * crop_factor, clamped to the frame; matching
        # the canvas to it keeps magnification >= 1.0x everywhere.
        if canvas_mode != "manual":
            need = float(min(sz.max() * crop_factor, H))
            snapped = int(np.ceil(need / 32.0) * 32)
            if canvas_mode == "auto_capped_768":
                snapped = min(snapped, 768)
            # Floor at 512. The crop is bounded by the source frame, so on a small
            # face in a low-resolution clip the canvas would otherwise track the crop
            # down to a couple of hundred px and H3 would be handed the same small
            # face it renders badly in the first place.
            snapped = max(512, min(snapped, 1344))
            if snapped != canvas_height:
                print(f"[H3FaceRefine] canvas_mode={canvas_mode}: "
                      f"{canvas_width}x{canvas_height} -> {snapped}x{snapped} "
                      f"(largest crop {need:.0f}px)")
            canvas_width = canvas_height = snapped

        # Frames the subject is not in are dropped from the batch rather than refined
        # and thrown away. Their boundaries are hard cuts, so removing them joins two
        # shots that were already discontinuous - no motion continuity is broken.
        # H3 needs a frame count on the 17k+5 grid, so as many absent frames are kept
        # back as padding as it takes to land on one. Real footage from the clip is
        # better padding than the reference content H3InjectVideoLatent would use.
        keep = np.arange(B)
        drop_note = ""
        if absent.any():
            present = np.nonzero(~absent)[0]
            spare = np.nonzero(absent)[0]
            need = len(present)
            if need >= 5:
                while need % 17 != 5:
                    need += 1
                pad = need - len(present)
                if pad <= len(spare):
                    keep = np.array(sorted(present.tolist() + spare[:pad].tolist()), int)
                    drop_note = (f"dropped {B - len(keep)} of {int(absent.sum())} absent "
                                 f"frame(s); {len(keep)} rendered"
                                 f"{f' ({pad} absent kept to reach the grid)' if pad else ''}")
                else:
                    drop_note = (f"{int(absent.sum())} absent frame(s) kept: dropping them "
                                 f"cannot reach H3's 17k+5 grid")
            else:
                drop_note = ("subject absent from almost the whole video; nothing dropped")

        kept = keep.tolist()
        pos = {f: k for k, f in enumerate(kept)}
        K = len(kept)

        aspect = canvas_width / float(canvas_height)
        boxes: list[tuple[int, int, int, int]] = []
        crops = torch.zeros((K, canvas_height, canvas_width, 3), dtype=images.dtype)
        preview = images[..., :3].clone()

        for k, i in enumerate(kept):
            bh = sz[i] * crop_factor
            bw = bh * aspect
            # keep aspect while fitting inside the frame
            if bw > W:
                bw, bh = float(W), float(W) / aspect
            if bh > H:
                bh, bw = float(H), float(H) * aspect
            # FLOAT box - deliberately not rounded. Integer rounding is the dominant
            # residual jitter once the trajectory is smoothed.
            x = min(max(cx[i] - bw / 2.0, 0.0), max(0.0, W - bw))
            y = min(max(cy[i] - bh / 2.0, 0.0), max(0.0, H - bh))
            box = (float(x), float(y), float(bw), float(bh))
            boxes.append(box)

            crops[k : k + 1] = _affine_crop(
                images[i : i + 1], box, canvas_width, canvas_height
            ).to(crops.dtype)

            # preview: draw the crop rectangle (rounded for drawing only)
            xi, yi = int(round(x)), int(round(y))
            wi, hi = max(4, int(round(bw))), max(4, int(round(bh)))
            xi = min(xi, W - wi); yi = min(yi, H - hi)
            # green = real face, yellow = head located from the body box, red = interpolated
            if valid[i]:
                r, g = 0.0, 1.0
            elif via_body[i]:
                r, g = 1.0, 1.0
            else:
                r, g = 1.0, 0.0
            for (yy0, yy1, xx0, xx1) in (
                (yi, yi + 2, xi, xi + wi), (yi + hi - 2, yi + hi, xi, xi + wi),
                (yi, yi + hi, xi, xi + 2), (yi, yi + hi, xi + wi - 2, xi + wi),
            ):
                preview[i, yy0:yy1, xx0:xx1, 0] = r
                preview[i, yy0:yy1, xx0:xx1, 1] = g
                preview[i, yy0:yy1, xx0:xx1, 2] = 0.0

        if select == "closest_to_xy" and not forced:
            # The point sits still while the crop travels past it, so the two together
            # read as what was asked for against what it followed. This node cannot
            # preview before the graph runs - its frames arrive with the run - so the
            # only place to check the point picked the right person is here.
            _g = max(3, int(round(min(H, W) / 200.0)))
            for i in kept:
                _draw_crosshair(preview[i], X, Y, (1.0, 0.75, 0.0), _g)

        # Per-frame confidence weight. When the subject turns away there is no face to
        # refine, and asking H3 to "improve a face" on the back of a head invites it to
        # hallucinate one. Fade the composite out across those runs instead. Smoothed so
        # it ramps over ~half a second rather than popping.
        # Split at cuts too: otherwise a dropout at the end of one shot fades the
        # composite out over the start of the next, which had a perfectly good face.
        weights = _smooth_seg(valid.astype(np.float64), max(9, smooth_window // 2),
                              "gaussian", segs)
        weights = np.clip(weights, 0.0, 1.0)

        runs, cur = [], 0
        for v in known:
            if v:
                if cur:
                    runs.append(cur)
                cur = 0
            else:
                cur += 1
        if cur:
            runs.append(cur)
        longest_gap = max(runs) if runs else 0

        mags = [canvas_height / float(b[3]) for b in boxes]
        # Shot boundaries remapped into crop index space, since the crops are now a
        # subset of the source frames.
        segs_kept = []
        for a2, b2 in segs:
            inside = [pos[f] for f in range(a2, b2) if f in pos]
            if inside:
                segs_kept.append((min(inside), max(inside) + 1))
        if not segs_kept:
            segs_kept = [(0, K)]

        transform = {
            "boxes": boxes,
            "canvas": (int(canvas_width), int(canvas_height)),
            "src_size": (int(W), int(H)),
            # every per-frame list below is per KEPT frame, in crop order
            "frames": int(K),
            # which source frame each crop came from, so the stitch puts it back in the
            # right place when frames have been dropped
            "source": [int(f) for f in kept],
            "source_frames": int(B),
            # Shot boundaries for anything downstream that smooths over time. Always
            # present; no cuts means one segment covering every frame.
            "segments": [(int(a), int(b)) for a, b in segs_kept],
            # Frames the subject is not in: there is no face there to refine, so
            # downstream nodes should not spend work on them.
            "absent": [bool(absent[f]) for f in kept],
            "weights": [float(weights[f]) for f in kept],
            "detected": [bool(valid[f]) for f in kept],
            # Face rect per frame in CANVAS pixel coords, centred in the crop. This is what
            # the stitch pastes through - matching FaceDetailer, which builds its paste mask
            # from the face bbox inside the larger crop region, NOT from the crop itself.
            "face_rect": [
                (
                    float(canvas_width) * 0.5
                    - 0.5 * float(sm_fw[kept[k]]) / max(b[2], 1e-6) * canvas_width,
                    float(canvas_height) * 0.5
                    - 0.5 * float(sz[kept[k]]) / max(b[3], 1e-6) * canvas_height,
                    float(sm_fw[kept[k]]) / max(b[2], 1e-6) * canvas_width,
                    float(sz[kept[k]]) / max(b[3], 1e-6) * canvas_height,
                )
                for k, b in enumerate(boxes)
            ],
            "crop_factor": float(crop_factor),
        }

        # A magnification below 1.0 means the crop is DOWNSCALED into the canvas, i.e. we
        # throw away real detail before handing it to H3 and upscale the result back on
        # stitch. On those frames this pipeline is a net loss versus leaving them alone.
        # H3 rounds its own length UP to the next 17k+5, so an off-grid clip gives an
        # AV latent longer than these crops: H3InjectVideoLatent pads the difference with
        # reference content that the sampler refines and the stitch throws away.
        # With face_pick wired this node's own detector/confidence widgets are inert,
        # so the report names the detector that actually produced the boxes.
        pickline = f"pick: {pick_note}\n" if pick_note else ""
        absentline = f"absent: {absent_note}\n" if absent_note else ""
        shortest_shot = min((b - a for a, b in segs), default=B)
        if cut_note:
            cutline = f"cuts: {cut_note}\n"
        elif cut_detection != "none" or len(segs) > 1:
            _cutsrc = ("carried on face_pick" if segs_given else
                       f"at threshold {cut_threshold:.1f}")
            cutline = (f"cuts: {len(segs) - 1} found {_cutsrc} -> "
                       f"{len(segs)} shot(s), shortest {shortest_shot} frames; smoothing "
                       f"and interpolation run per shot\n")
        else:
            cutline = ""

        cutwarn = ""
        if len(segs) > 1:
            _win = max(int(smooth_window), int(size_smooth_window))
            if shortest_shot < _win:
                cutwarn = (
                    f"\n!! shortest shot is {shortest_shot} frames, under the "
                    f"{_win}-frame smoothing window, so it gets less smoothing than the "
                    f"rest of the video and the crop may shiver there. Lower smooth_window "
                    f"/ size_smooth_window, or raise cut_threshold if that split was "
                    f"spurious."
                )

        gridwarn = ""
        if K % 17 != 5:
            _aligned = K
            while _aligned % 17 != 5:
                _aligned += 1
            gridwarn = (
                f"\n!! {K} rendered frame(s) is off H3's 17k+5 grid. H3 rounds up "
                f"to {_aligned} and the {_aligned - K} extra frame(s) get padded with "
                f"reference content, refined, then discarded on stitch."
            )

        gapwarn = ""
        if longest_gap >= 12:
            gapwarn = (
                f"\n!! longest dropout is {longest_gap} frames ({longest_gap/24.0:.1f}s). The crop "
                f"box is linearly interpolated across it, so it may drift if the subject moved "
                f"while turned away. Detection weighting fades the composite out there, so those "
                f"frames keep their original pixels - check the preview over that stretch."
            )

        n_down = sum(1 for m in mags if m < 1.0)
        warn = ""
        if n_down:
            need = max(b[3] for b in boxes)
            warn = (
                f"\n!! {n_down}/{len(mags)} frames ({n_down/max(1, len(mags))*100:.0f}%) have magnification < 1.0x - "
                f"their crops are DOWNSCALED into the canvas, losing real detail.\n"
                f"   Fix: raise canvas to >= {need}px (rounded up to a multiple of 32), or lower "
                f"crop_factor, or skip this video if it is close-up throughout."
            )

        # Frames that had a face but were held back waiting for select_index. They are
        # interpolated and faded out of the composite like any dropout, so they must be
        # stated rather than left to look like detection failures.
        # With a face_pick every shot locks on its own frame, so nothing is held back
        # clip-wide waiting for select_index and there is no such stretch to report.
        prelock = 0 if forced else sum(1 for i in range(lock_frame) if all_boxes[i])
        idxwarn = ""
        if requested_index > 0 and not ranking_picks and not forced:
            idxwarn += (
                f"\n!! select_index={requested_index} was ignored: identity_reference "
                f"decides the subject when one is connected."
            )
        if prelock >= 12:
            idxwarn += (
                f"\n!! {prelock} frames before the lock-on contain fewer than "
                f"{select_index + 1} faces, so they are interpolated from frame {lock_frame} "
                f"and faded out of the composite. Lower select_index if that stretch matters."
            )
        if requested_index != select_index and not forced:
            idxwarn += (
                f"\n!! select_index={requested_index} is out of range: at most {max_faces} "
                f"face(s) were ever detected in a single frame, so index {select_index} was "
                f"used instead."
            )

        # Identity accounting. The scores are printed because identity_threshold means a
        # different thing per backend, and a number nobody can calibrate is a number
        # nobody will touch.
        if ident_note:
            identline = f"identity: {ident_note}\n"
        elif embedder is None:
            identline = "identity: not used (single face, or identity_track off)\n"
        else:
            if ident_scores:
                scores = (f"  scores min={min(ident_scores):.3f} "
                          f"mean={sum(ident_scores)/len(ident_scores):.3f} "
                          f"max={max(ident_scores):.3f}")
            elif ref_emb is None:
                scores = "  (no anchor could be built - tracked by continuity alone)"
            else:
                scores = "  (anchor built, never consulted - continuity was unambiguous)"
            identline = (
                f"identity: {embedder.name}  threshold={ident_threshold:.3f}"
                f"{' (model default)' if identity_threshold <= 0.0 else ''}"
                f"{'  [' + embedder.note + ']' if getattr(embedder, 'note', '') else ''}"
                f"{scores}\n"
            )

        identwarn = ""
        if identity_reference is not None and not identity_track:
            identwarn += (
                f"\n!! identity_reference is connected but identity_track is off, so it "
                f"was not read. The subject was chosen by select={select} instead. Turn "
                f"identity_track on to match against that image."
            )
        if ref_failed:
            identwarn += (
                f"\n!! {embedder.name} found no face in identity_reference, so "
                f"the anchor was built from the video's dominant face instead - which "
                f"may not be the person in that image. Try a different identity_model, or "
                f"a clearer reference."
            )
        if ident_scores and min(ident_scores) >= ident_threshold:
            identwarn += (
                f"\n!! every identity score cleared identity_threshold={ident_threshold:.3f}, "
                f"so it rejected nothing. Lowest seen was {min(ident_scores):.3f}. Raise it, or "
                f"set identity_threshold to 0 for {embedder.name}'s own default."
            )

        box_jit = float(np.mean([abs(boxes[i][0] - boxes[i-1][0]) + abs(boxes[i][1] - boxes[i-1][1])
                                 for i in range(1, len(boxes))])) if len(boxes) > 1 else 0.0
        # Which mechanism actually chose the subject. A pick outranks a reference,
        # which outranks the ranking rule; the reference still anchors the tracking.
        if forced:
            _lk = sorted(forced)
            _shown = ", ".join(str(v) for v in _lk[:6]) + (" ..." if len(_lk) > 6 else "")
            lockdesc = f"locked on per shot at {_shown} of {B}"
            if identity_reference is not None:
                lockdesc += "; the reference anchors tracking only"
        else:
            lockdesc = f"locked on at frame {lock_frame} of {B}"

        render_line = f"rendering: {K} of {B} frames  [{drop_note}]\n" if drop_note else ""
        lost_line = (f"lost: {len(lost)} frame(s) held faces but none within reach of the "
                     f"subject ({_frame_ranges(lost)}) - left unresolved\n" if lost else "")
        report = (
            f"subject: {'from face_pick, one per shot' if forced else _sel_desc}"
            f"{'' if (ranking_picks or forced) else ' (ignored - identity_reference decides)'}, "
            f"{lockdesc}"
            f"{f', {prelock} earlier frames interpolated' if prelock else ''}\n"
            f"faces: max {max_faces} in one frame\n"
            f"{pickline}"
            f"{absentline}"
            f"{cutline}"
            f"tracking: {n_cont} by continuity, {n_conflict} ambiguous "
            f"({n_ident} resolved by face identity)\n"
            f"{lost_line}"
            f"{identline}"
            f"{render_line}"
            f"frames={B}  face={found} ({found/B*100:.0f}%)  "
            f"body-fallback={int(via_body.sum())}  interpolated={B-int(known.sum())}\n"
            f"face height  min={sz.min():.0f}px  mean={sz.mean():.0f}px  max={sz.max():.0f}px\n"
            f"face fills   ~{100.0/crop_factor:.0f}% of every crop (crop_factor={crop_factor})\n"
            f"crop box     min={min(b[3] for b in boxes):.1f}px  max={max(b[3] for b in boxes):.1f}px\n"
            f"magnification into {canvas_width}x{canvas_height}: "
            f"min={min(mags):.2f}x  mean={sum(mags)/len(mags):.2f}x  max={max(mags):.2f}x\n"
            f"jitter ({smooth_method}) centre {jit_before:.2f} -> {jit_after:.2f} px/frame"
            f"   size {sz_before:.2f} -> {sz_after:.2f} px/frame\n"
            f"box movement {box_jit:.2f} px/frame (sub-pixel float boxes - no integer rounding)\n"
            f"dropout runs: {len(runs)}  longest={longest_gap} frames ({longest_gap/24.0:.1f}s "
            f"at 24fps)  -> composite fades out across these"
            f"{identwarn}{pick_warn}{idxwarn}{gridwarn}{cutwarn}{gapwarn}{warn}"
        )
        # Always print. The report is also returned as an output, but that output is
        # usually left unconnected, and then a run gives no account of how it tracked -
        # which is exactly the information needed to tell whether identity matching did
        # any work on a crowd scene.
        print("[H3FaceRefine] " + report.replace("\n", "\n[H3FaceRefine] "))
        return (crops, transform, preview, report, int(canvas_width), int(canvas_height),
                int(K))


# ----------------------------------------------------------------------------
# 2. stitch back
# ----------------------------------------------------------------------------


class H3FaceStitch:
    """Paste refined crops back using the per-frame transform, with feather + colour match.

    Mirrors Impact Pack's detailer paste - only the face region composites, through a
    dilated then Gaussian-blurred mask - but warps rather than slices: one batched
    `grid_sample` maps each crop back onto the float box it came from, so a trajectory
    smoothed to sub-pixel precision is not re-quantised on the way home.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "base_images": ("IMAGE",),
                "refined_crops": ("IMAGE",),
                "transform": ("H3FACEXFORM",),
                "paste_region": (["face_only", "face_ellipse", "full_crop"],
                    {"default": "face_only",
                     "tooltip": "WHAT gets composited back. face_only / face_ellipse paste just "
                                "the detected face box (FaceDetailer's behaviour - the wider "
                                "crop exists to give the sampler context, not to be pasted). "
                                "full_crop pastes the whole crop including hair, shoulders and "
                                "background, which risks a visible rectangle if H3 alters them."}),
                "mask_dilation": ("INT", {"default": 16, "min": 0, "max": 256, "step": 2,
                    "tooltip": "Grow the face box before blurring, in canvas px. Impact Pack "
                               "dilates the same way so the blur has room and the blend does "
                               "not eat into the face itself."}),
                "feather": ("INT", {"default": 6, "min": 0, "max": 256, "step": 2,
                    "tooltip": "Gaussian blur radius on the paste mask, in SOURCE pixels. "
                               "Measured against the final frame, not the canvas, so the blend "
                               "is the same physical width whatever this frame's magnification "
                               "happens to be.\n\n"
                               "Canvas-relative feather is a trap: a 75px crop blown up to 512 "
                               "makes a 40px canvas feather only ~6 source px, while a 720px "
                               "crop makes it ~56. The blend ends up TIGHTEST exactly where the "
                               "face is smallest and the composite needs the most help - which "
                               "reads as a hard edge appearing as a shot zooms out."}),
                "colour_match": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "Match the refined crop's per-channel mean/std to the region it "
                               "replaces. The crop and the full frame went through independent "
                               "passes, so without this the face can come back subtly brighter "
                               "or differently tinted and read as pasted on."}),
                "blend": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "Global opacity of the refined face. Below 1.0 mixes back toward "
                               "the original - useful to dial back over-sharpening."}),
                "undetected_frames": (["fade_out", "skip", "composite_anyway"],
                    {"default": "fade_out",
                     "tooltip": "What to do on frames where no FACE was found (turned away / "
                                "occluded). ALL frames are still sent through H3 either way - "
                                "that is what keeps it temporally consistent - this only "
                                "controls whether the result is pasted back.\n"
                                "fade_out: ramp the composite to zero across the gap (smooth, "
                                "no pop, recommended).\n"
                                "skip: hard cut - those frames keep original pixels exactly.\n"
                                "composite_anyway: paste regardless. Risks H3 hallucinating a "
                                "face onto the back of a head."}),
            },
            "optional": {
                # OPTIONAL, not required - adding a required input breaks every existing
                # workflow and API caller with "Required input is missing".
                "feather_scales_with_crop": ("BOOLEAN", {"default": False,
                    "tooltip": "Old behaviour: treat feather as CANVAS pixels, so the blend "
                               "narrows as the crop shrinks. Leave off."}),
                "masks": ("MASK", {
                    "tooltip": "Optional per-frame paste masks in CANVAS space, e.g. from "
                               "H3 Face Mask (SAM). Overrides paste_region. This is the "
                               "FaceDetailer bbox+SAM path: the mask follows the actual face so "
                               "the blend falls on the jaw and hairline instead of an arbitrary "
                               "rectangle. With a SAM mask use a SMALL feather (4-8); a "
                               "rectangle needs much more."}),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    FUNCTION = "run"
    CATEGORY = "MiniMax H3/Face Refine"
    DESCRIPTION = "Composite H3-refined face crops back into the source frames."

    def run(self, base_images, refined_crops, transform, paste_region, mask_dilation, feather,
            colour_match, blend, undetected_frames="fade_out", masks=None,
            feather_scales_with_crop=False):
        boxes = transform["boxes"]
        if undetected_frames == "composite_anyway":
            weights = None
        elif undetected_frames == "skip":
            weights = [1.0 if d else 0.0 for d in transform.get("detected", [])] or None
        else:
            weights = transform.get("weights")
        B = min(len(boxes), refined_crops.shape[0])
        # Where each crop belongs in the source clip. The track node drops frames the
        # subject is not in, so crops are not necessarily one-per-source-frame.
        source = transform.get("source")
        if not source or len(source) < B:
            source = list(range(min(B, base_images.shape[0])))
            B = len(source)
        if len(source) != base_images.shape[0]:
            print(f"[H3FaceRefine] compositing {B} refined frame(s) into "
                  f"{base_images.shape[0]} source frame(s)")
        elif base_images.shape[0] != refined_crops.shape[0]:
            print(f"[H3FaceRefine] frame count mismatch: base={base_images.shape[0]} "
                  f"refined={refined_crops.shape[0]} transform={len(boxes)} -> using {B}")

        import torch.nn.functional as F

        cw, ch = transform["canvas"]
        W, H = transform["src_size"]
        face_rects = transform.get("face_rect")

        # ---- GPU, batched. The previous version was a per-frame Python loop on CPU
        # tensors: measured at ~1 core of 24 busy with the GPU idle, ~8 minutes for 362
        # frames. Every operation here (affine warp, blur, blend) is a tensor op, so it
        # belongs on the GPU and can be batched over frames.
        import comfy.model_management as mm

        try:
            dev = mm.get_torch_device()
        except Exception:
            dev = base_images.device
        dt = base_images.dtype
        out = base_images[..., :3].clone()

        # chunked so the warped batch does not blow VRAM: N x H x W x 3 at once
        per_frame_mb = (H * W * 3 * 4) / 2 ** 20
        chunk = max(1, min(32, int(1024 / max(per_frame_mb, 1e-6))))

        for c0 in range(0, B, chunk):
            mm.throw_exception_if_processing_interrupted()
            c1 = min(c0 + chunk, B)
            n = c1 - c0

            # feather is given in SOURCE pixels; the mask is built in canvas space, so
            # convert using this chunk's magnification (canvas / crop height). Without
            # this the blend is ~10x tighter on distant frames than on close ones.
            if feather_scales_with_crop:
                f_can = int(feather)
            else:
                bh_mid = float(boxes[(c0 + c1 - 1) // 2][3])
                f_can = int(round(feather * (ch / max(bh_mid, 1.0))))
                f_can = max(1, min(f_can, ch // 3))

            # --- batched paste-mask in canvas space ---
            if masks is not None:
                mk = masks[c0:c1].to(dev).float()
                if mk.shape[-2:] != (ch, cw):
                    mk = F.interpolate(mk.unsqueeze(1), size=(ch, cw),
                                       mode="bilinear", align_corners=False)
                else:
                    mk = mk.unsqueeze(1)
                if mask_dilation > 0:
                    k = 2 * int(mask_dilation) + 1
                    mk = F.max_pool2d(mk, k, stride=1, padding=k // 2)
                mask_can = _gaussian_blur_mask(mk, f_can).clamp(0, 1)
            elif paste_region == "full_crop":
                one = _feather_mask(ch, cw, f_can, dev, torch.float32)
                mask_can = one.view(1, 1, ch, cw).expand(n, 1, ch, cw)
            else:
                mask_can = torch.cat([
                    _face_region_mask(
                        ch, cw,
                        face_rects[i] if face_rects and i < len(face_rects)
                        else (cw * 0.25, ch * 0.25, cw * 0.5, ch * 0.5),
                        int(mask_dilation), f_can,
                        "ellipse" if paste_region == "face_ellipse" else "rect",
                        dev, torch.float32)
                    for i in range(c0, c1)], dim=0)

            # --- one affine grid for the whole chunk ---
            th = torch.empty((n, 2, 3), dtype=torch.float32, device=dev)
            for j, i in enumerate(range(c0, c1)):
                x, y, bw, bh = (float(v) for v in boxes[i])
                th[j, 0, 0] = W / bw; th[j, 0, 1] = 0.0
                th[j, 0, 2] = (W - 2.0 * x) / bw - 1.0
                th[j, 1, 0] = 0.0;    th[j, 1, 1] = H / bh
                th[j, 1, 2] = (H - 2.0 * y) / bh - 1.0
            grid = F.affine_grid(th, (n, 3, int(H), int(W)), align_corners=False)

            patch_can = refined_crops[c0:c1, ..., :3].to(dev).movedim(-1, 1).float()
            patch = F.grid_sample(patch_can, grid, mode="bilinear",
                                  padding_mode="zeros", align_corners=False)
            m = F.grid_sample(mask_can.to(dev), grid, mode="bilinear",
                              padding_mode="zeros", align_corners=False).clamp(0, 1)

            patch = patch.movedim(1, -1)                 # [n,H,W,3]
            m = m.movedim(1, -1)                         # [n,H,W,1]
            dst = torch.as_tensor(source[c0:c1], dtype=torch.long, device=out.device)
            base = out[dst].to(dev).float()

            # --- weighted colour match, no boolean gather/scatter ---
            if colour_match > 0.0:
                wsum = m.sum(dim=(1, 2), keepdim=True).clamp_min(1e-6)
                bmu = (base * m).sum(dim=(1, 2), keepdim=True) / wsum
                pmu = (patch * m).sum(dim=(1, 2), keepdim=True) / wsum
                bsd = (((base - bmu) ** 2 * m).sum(dim=(1, 2), keepdim=True)
                       / wsum).sqrt().clamp_min(1e-6)
                psd = (((patch - pmu) ** 2 * m).sum(dim=(1, 2), keepdim=True)
                       / wsum).sqrt().clamp_min(1e-6)
                adj = (patch - pmu) * (bsd / psd) + bmu
                patch = patch + (adj - patch) * float(colour_match)
                patch = patch.clamp(0, 1)

            # --- per-frame opacity (blend x detection weight) ---
            wv = torch.full((n, 1, 1, 1), float(blend), device=dev, dtype=torch.float32)
            if weights is not None:
                for j, i in enumerate(range(c0, c1)):
                    if i < len(weights):
                        wv[j] *= float(weights[i])
            mm_ = m * wv

            out[dst] = ((1.0 - mm_) * base + mm_ * patch).to(out.device, dt)

        return (out,)

# ----------------------------------------------------------------------------
# 3. inject real video into the AV latent
# ----------------------------------------------------------------------------
