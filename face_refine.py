"""Opt-in single-node face refinement. Tracking attribution: LICENSE-FaceRefine.txt."""
from __future__ import annotations

import gc
import math
import threading
from types import SimpleNamespace

import torch
import folder_paths

_LOCK = threading.RLock()


def _detectors():
    # Schema discovery must not import optional detector libraries.
    import os
    choices = []
    for key in ('ultralytics_bbox', 'ultralytics'):
        try:
            choices.extend(folder_paths.get_filename_list(key))
        except KeyError:
            pass
    root = os.path.join(folder_paths.models_dir, 'ultralytics', 'bbox')
    if os.path.isdir(root):
        choices.extend(n for n in os.listdir(root) if n.lower().endswith('.pt'))
    choices = sorted(set(choices))
    preferred = next((n for n in choices if n.replace('\\', '/').endswith('face_yolov8m.pt')), None)
    if preferred:
        choices.remove(preferred)
        choices.insert(0, preferred)
    return choices or ['face_yolov8m.pt']


def _windows(start, end, limit, overlap=17):
    while start < end:
        stop = min(start + limit, end)
        yield start, stop
        if stop == end:
            break
        start = stop - overlap


def _pad_frames(frames):
    from comfy_extras import nodes_minimax_h3 as h3
    target = h3.align_frame_count(max(5, len(frames)))
    return torch.cat((frames, frames[-1:].expand(target-len(frames), -1, -1, -1)), 0) if target > len(frames) else frames


def _release(model, context):
    import comfy.model_management as mm
    for patcher in (model, getattr(context.clip, 'patcher', None),
                    getattr(context.video_vae, 'patcher', None), getattr(context.audio_vae, 'patcher', None)):
        if patcher is not None:
            mm.unload_model_and_clones(patcher, unload_additional_models=False)
    gc.collect()
    mm.soft_empty_cache()


def _audio_window(audio, first_frame, frame_count, padded_frames, fps):
    """Use source-video time, including overlap; never repeat speech for grid padding."""
    wave = audio['waveform'].detach().cpu()
    sr = int(audio['sample_rate'])
    first = round(first_frame * sr / fps)
    last = round((first_frame + frame_count) * sr / fps)
    size = round(padded_frames * sr / fps)
    result = wave.new_zeros((1, wave.shape[1], size))
    clip = wave[..., first:min(last, wave.shape[-1])]
    copied = min(clip.shape[-1], size)
    result[..., :copied] = clip[..., :copied]
    return {'waveform': result, 'sample_rate': sr}


def _lock_target_audio(latent, audio_vae, audio):
    # Native ComfyUI H3 interprets an all-zero audio mask as clean conditioning.
    # No global model monkey-patch and no legacy lock flag are needed.
    from comfy_extras.nodes_minimax_h3 import _encode_ref_audio
    from comfy.nested_tensor import NestedTensor
    video, template = latent['samples'].unbind()
    encoded, _ = _encode_ref_audio(audio_vae, audio)
    if encoded.shape[:-1] != template.shape[:-1]:
        raise ValueError('音频锁定失败：音频 VAE 与 H3 音频 latent 形状不兼容。')
    locked = torch.zeros_like(template)
    length = min(encoded.shape[-1], template.shape[-1])
    locked[..., :length] = encoded[..., :length].to(locked)
    return {**latent, 'samples': NestedTensor((video, locked)),
            'noise_mask': NestedTensor((torch.ones_like(video), torch.zeros_like(locked)))}


def _prepare_reference_face(tr, image, detector, confidence, crop_factor, size):
    """Largest detected face, square crop without changing its aspect ratio."""
    if not isinstance(image, torch.Tensor) or image.ndim != 4 or not len(image) or image.shape[-1] < 3:
        raise ValueError('参考人脸必须为非空 IMAGE 图像。')
    frame = image[:1, ..., :3].detach().cpu()
    model = tr._load_detector(detector)
    prediction = model.predict(tr._to_bgr_u8(frame[0]), conf=confidence, verbose=False)[0]
    boxes = prediction.boxes.xyxy.tolist() if prediction.boxes is not None else []
    height, width = frame.shape[1:3]
    valid = []
    for box in boxes:
        if len(box) != 4 or not all(math.isfinite(float(v)) for v in box):
            continue
        x1, y1, x2, y2 = box
        x1, x2 = max(0, min(width, x1)), max(0, min(width, x2))
        y1, y2 = max(0, min(height, y1)), max(0, min(height, y2))
        if x2 > x1 and y2 > y1:
            valid.append((x1, y1, x2, y2))
    if not valid:
        raise ValueError('参考图片未检测到人脸。请外接清晰人脸图，或检查检测模型和阈值。')
    x1, y1, x2, y2 = max(valid, key=lambda b: (b[2]-b[0])*(b[3]-b[1]))
    side = max(x2-x1, y2-y1) * float(crop_factor)
    if not math.isfinite(side) or side <= 0:
        raise ValueError('参考人脸裁剪范围无效。')
    square = ((x1+x2-side)/2, (y1+y2-side)/2, side, side)
    # Border extension preserves a square field near image edges instead of stretching a clipped rectangle.
    cropped = tr._affine_crop(frame, square, size, size)
    return cropped, f'参考图检测到 {len(valid)} 张脸，选取最大脸，按裁剪范围 {crop_factor:g} 裁至 {size}×{size}。'


class FeiHouEasyH3FaceRefine:
    CATEGORY = 'FeiHou Easy H3'
    FUNCTION = 'refine'
    RETURN_TYPES = ('IMAGE', 'AUDIO', 'FLOAT', 'STRING')
    RETURN_NAMES = ('images', 'audio', 'fps', 'report')
    DESCRIPTION = 'Experimental H3 face crop/refine/stitch. Adds sampling; does not guarantee lip sync. Original audio passes through.'

    @classmethod
    def INPUT_TYPES(cls):
        import comfy.samplers
        return {'required': {
            'images': ('IMAGE',), 'model': ('MODEL',), 'h3_context': ('MINIMAX_H3_CONTEXT',),
            'enabled': ('BOOLEAN', {'default': True}),
            'detector': (_detectors(),),
            'target': (['largest_face', 'reference_identity'],),
            'denoise': ('FLOAT', {'default': 0.25, 'min': 0.0, 'max': 1.0, 'step': 0.01}),
            'steps': ('INT', {'default': 8, 'min': 1, 'max': 100}),
            'seed': ('INT', {'default': 0, 'min': 0, 'max': 0xffffffffffffffff}),
            'advanced': ('BOOLEAN', {'default': False}),
            'canvas_size': (['512', '768'], {'default': '768'}),
            'confidence': ('FLOAT', {'default': 0.35, 'min': 0.05, 'max': 0.95, 'step': 0.05}),
            'crop_factor': ('FLOAT', {'default': 2.5, 'min': 1.2, 'max': 5.0, 'step': 0.1}),
            'chunk_frames': (['不分段', '240', '192', '120', '72'], {'default': '不分段'}),
            'sampler_name': (comfy.samplers.KSampler.SAMPLERS, {'default': 'euler'}),
            'scheduler': (comfy.samplers.SCHEDULER_NAMES, {'default': 'simple'}),
            'prompt': ('STRING', {'default': 'Refine the face in this video crop. Preserve identity, expression, gaze, mouth movement, lighting and motion. Natural facial detail; no new motion or camera changes.', 'multiline': True}),
            'force_offload': ('BOOLEAN', {'default': True}),
            # Append only: existing workflows serialize widgets by position.
            'audio_lock': ('BOOLEAN', {'default': False, 'tooltip': 'Experimental: condition face sampling on the connected, time-aligned original audio. Not a guaranteed lip-sync correction.'}),
            'clean_second_model': ('BOOLEAN', {'default': True, 'tooltip': 'Use the recorded pre-LoRA second-pass base. Requires an Easy H3 second-pass model; removes external patches as well.'}),
        }, 'optional': {'reference_face': ('IMAGE',), 'audio': ('AUDIO',)}}

    def refine(self, images, model, h3_context, enabled=True, detector='face_yolov8m.pt',
               target='largest_face', denoise=0.25, steps=8, seed=0, advanced=False,
               canvas_size='768', confidence=0.35, crop_factor=2.5, chunk_frames='不分段',
               sampler_name='euler', scheduler='simple', prompt='', force_offload=True,
               reference_face=None, audio=None, audio_lock=False, clean_second_model=True):
        # Independent of original-audio reference slicing: only explicitly supplied final audio is passed through.
        fps = float(h3_context.fps)
        if not enabled or denoise <= 0:
            return images, audio, fps, '已跳过人脸精修；原画面和原音频保持不变。'
        if images.ndim != 4 or len(images) == 0:
            raise ValueError('人脸精修需要非空 IMAGE 视频帧。')
        if reference_face is None:
            reference_face = getattr(h3_context, 'reference_image_1', None)
        if target == 'reference_identity' and reference_face is None:
            raise ValueError('参考图匹配模式需要连接 reference_face。')
        if clean_second_model:
            getter = getattr(model, 'get_attachment', None)
            base = getter('feihou_h3_clean_second_base') if getter else None
            if base is None:
                raise ValueError('未找到二采基础模型记录。请重新运行更新后的 Easy H3 加载/主节点；外部模型请在高级选项中关闭“使用纯净二采模型”。')
            # Shared weight storage: eject the active regular/bypass branch before
            # switching, even when optional post-sampling cache cleanup is disabled.
            import comfy.model_management as mm
            mm.unload_model_and_clones(model, unload_additional_models=False)
            model = base.clone()
        if audio_lock:
            wave = audio.get('waveform') if isinstance(audio, dict) else None
            if (not isinstance(wave, torch.Tensor) or wave.ndim != 3 or wave.shape[0] != 1
                    or wave.shape[1] not in (1, 2) or wave.shape[-1] == 0
                    or not isinstance(audio.get('sample_rate'), (int, float)) or audio['sample_rate'] <= 0):
                raise ValueError('音频约束需要连接与视频从第 0 帧对齐的原音频（单条单声道或立体声音频）。')
            if not hasattr(getattr(model, 'model', None), '_denoise_mask_values'):
                raise ValueError('当前模型/ComfyUI 不支持 H3 原生音频遮罩；请更新 ComfyUI 或关闭音频约束。')
        with _LOCK, torch.inference_mode():
            from . import face_refine_tracking as tr
            try:
                return self._run(tr, images, model, h3_context, detector, target, denoise,
                                 steps, seed, canvas_size, confidence, crop_factor,
                                 chunk_frames, sampler_name, scheduler, prompt, force_offload,
                                 reference_face, audio, fps, audio_lock)
            except ImportError as exc:
                raise RuntimeError('人脸精修缺少可选依赖。按 FACE_REFINE.md 安装 requirements-face-refine.txt；其他 Easy H3 节点不受影响。') from exc
            finally:
                # These are private to the vendored helper, not another plugin's model caches.
                for det in tr._DETECTOR_CACHE.values():
                    det.to('cpu')
                tr._DETECTOR_CACHE.clear()
                tr._REC_CACHE.clear()
                if force_offload:
                    _release(model, h3_context)

    def _run(self, tr, images, model, context, detector, target, denoise, steps, seed,
             canvas_size, confidence, crop_factor, chunk_frames, sampler_name, scheduler,
             prompt, force_offload, reference_face, audio, fps, audio_lock=False):
        import comfy.model_management as mm
        import comfy.nested_tensor
        import comfy.samplers
        from comfy_extras import nodes_custom_sampler as cs
        from nodes import VAEDecode
        from .nodes import _reference_conditioning, _MediaInput
        if force_offload:
            _release(model, context)
        # Fail clearly rather than silently smoothing across unknown cuts.
        import scenedetect  # noqa: F401 -- optional install, required for this all-in-one node
        size = int(canvas_size)
        limit = 0 if chunk_frames == '不分段' else int(chunk_frames)
        # Keep numeric legacy API calls working; saved UI nodes migrate to presets.
        if size not in (512, 768) or (limit != 0 and not 39 <= limit <= 362):
            raise ValueError('精修画布须为 512/768；请选择有效的分段帧数。')
        reference_report = '未提供图片参考，使用视频分段的人脸裁剪帧作为参考。'
        if reference_face is not None:
            source = 'H3 context 第一张原图' if reference_face is getattr(context, 'reference_image_1', None) else '外接参考图片'
            reference_face, reference_report = _prepare_reference_face(
                tr, reference_face, detector, confidence, crop_factor, size)
            reference_report = source + '；' + reference_report
        try:
            crops, transform, preview, report, cw, ch, count = tr.H3FaceTrackCrop().run(
                images=images.cpu(), detector=detector, confidence=confidence,
                crop_factor=crop_factor, canvas_width=size, canvas_height=size,
                canvas_mode='manual', smooth_window=21, size_smooth_window=51,
                smooth_method='gaussian', size_mode='per_frame',
                select='largest_face', identity_track=target == 'reference_identity',
                identity_reference=reference_face, cut_detection='auto (pyscenedetect)')
        except ValueError as exc:
            if str(exc).startswith('No face detected in any frame'):
                return images, audio, fps, '未检测到可修复人脸；保留原画面。\n' + str(exc)
            raise
        del preview
        report = reference_report + '\n' + report
        for det in tr._DETECTOR_CACHE.values():
            det.to('cpu')
        tr._DETECTOR_CACHE.clear()
        tr._REC_CACHE.clear()
        gc.collect()
        mm.soft_empty_cache()
        refined = crops.cpu().clone()
        weights = torch.zeros(len(crops))
        source_bundle = SimpleNamespace(clip=context.clip, video_vae=context.video_vae, audio_vae=context.audio_vae)
        # No new attention or per-frame model patches: inherit only what the user wired.
        working_model = model.clone()
        segment_count = 0
        source = transform.get('source', list(range(len(crops))))
        for a, b in transform.get('segments', [(0, len(crops))]):
            for start, end in _windows(a, b, limit or max(1, b-a)):
                mm.throw_exception_if_processing_interrupted()
                if not any(transform['detected'][start:end]):
                    continue
                frames = _pad_frames(crops[start:end])
                ref = reference_face if reference_face is not None else crops[start:start+1]
                local_prompt = prompt or 'Preserve the face and motion. Refine natural facial details.'
                if audio_lock:
                    local_prompt += '\nThe person speaks or sings in sync with the supplied target audio. Preserve the original mouth timing, expression and head pose. Do not invent speech.'
                conditioning, latent, _ = _reference_conditioning(source_bundle, local_prompt,
                    cw, ch, len(frames), 'match', [_MediaInput(1, 'image', ref)])
                encoded = context.video_vae.encode(frames[..., :3])
                members = list(latent['samples'].unbind())
                if tuple(encoded.shape) != tuple(members[0].shape):
                    raise ValueError(f'人脸精修 latent 尺寸不匹配: {tuple(encoded.shape)} / {tuple(members[0].shape)}')
                members[0] = encoded.to(members[0].device, members[0].dtype)
                latent['samples'] = comfy.nested_tensor.NestedTensor(tuple(members))
                if audio_lock:
                    indices = source[start:end]
                    if not indices or indices != list(range(indices[0], indices[0] + end-start)):
                        raise ValueError('音频约束遇到不连续的视频帧映射，已停止以避免错位。')
                    segment_audio = _audio_window(audio, indices[0], end-start, len(frames), fps)
                    latent = _lock_target_audio(latent, context.audio_vae, segment_audio)
                    del segment_audio
                if force_offload:
                    for component in (context.clip, context.video_vae, context.audio_vae):
                        p = getattr(component, 'patcher', None)
                        if p is not None:
                            mm.unload_model_and_clones(p, unload_additional_models=False)
                    mm.soft_empty_cache()
                sigmas = cs.BasicScheduler.execute(working_model, scheduler, steps, denoise)[0]
                guider = cs.BasicGuider.execute(working_model, conditioning)[0]
                sampled = cs.SamplerCustomAdvanced.execute(cs.Noise_RandomNoise((int(seed)+segment_count) % 2**64),
                    guider, comfy.samplers.sampler_object(sampler_name), sigmas, latent)[0]
                if force_offload:
                    mm.unload_model_and_clones(working_model, unload_additional_models=False)
                # Official decoding handles AV stream selection and [B,T,H,W,C]
                # -> [B*T,H,W,C]. len(raw_vae_output) counts batches, not frames.
                decoded = VAEDecode().decode(context.video_vae, sampled)[0].cpu()
                if decoded.ndim != 4 or tuple(decoded.shape[1:]) != tuple(crops.shape[1:]):
                    raise ValueError(f'精修解码图像形状异常：{tuple(decoded.shape)}；预期 [帧数, {ch}, {cw}, 3]。')
                if len(decoded) < end-start:
                    raise ValueError(f'精修解码帧数不足：分段 {start}:{end} 需要 {end-start} 帧，实际 {len(decoded)} 帧（补齐输入 {len(frames)} 帧）；未输出截短视频。')
                decoded = decoded[:end-start]
                previous = weights[start:end].view(-1,1,1,1)
                refined[start:end] = (refined[start:end]*previous + decoded)/(previous+1)
                weights[start:end] += 1
                segment_count += 1
                del frames, encoded, latent, members, conditioning, guider, sampled, decoded
                if force_offload:
                    _release(working_model, context)
        result = tr.H3FaceStitch().run(images.cpu(), refined, transform,
            paste_region='face_ellipse', mask_dilation=16, feather=6,
            colour_match=1.0, blend=1.0, undetected_frames='fade_out')[0]
        if result.shape != images[..., :3].shape:
            raise ValueError('人脸精修输出尺寸或帧数改变，已中止。')
        audio_report = ('已按原视频时间截取并锁定采样音频；网格补齐部分填静音。音频约束不保证逐音素口型准确。'
                        if audio_lock else '未启用音频约束。')
        if audio_lock and audio['waveform'].shape[-1] / audio['sample_rate'] < len(images) / fps:
            audio_report += ' 输入音频短于视频，不足部分仅在采样时补静音。'
        return result, audio, fps, (f'人脸精修完成：{segment_count} 个采样分段；{len(result)} 帧，{fps:g} FPS。\n'
            + audio_report + ' 原音频原样输出；未启用逐帧降噪补丁。请检查口型、身份及分段接缝。\n' + report)
