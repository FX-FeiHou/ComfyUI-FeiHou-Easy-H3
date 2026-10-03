"""Read-only H3 capability probes and advisory patch inventory.

No version allow-list, global monkey patches, or changes to sampling inputs.
"""
import inspect
import logging
from collections.abc import Mapping


def probe_core():
    """Exercise tiny CPU sentinels, not a loaded model or user media."""
    import torch
    from comfy.ldm.minimax.model import PackedLayout
    from comfy.model_base import MiniMaxH3

    result = {}
    key = torch.zeros((1, 24, 1, 2, 2), device='cpu')
    ref = torch.ones_like(key)
    keyframes = [{'resolved_frame_index': 0, 'latent': key}]
    refs = [{'kind': 'image', 'latent_h': 2, 'latent_w': 2, 'latent': ref}]
    try:
        dummy = object.__new__(MiniMaxH3)
        dummy.concat_keys = ()
        dummy.latent_shapes = None
        payload = MiniMaxH3.extra_conds(dummy, minimax_keyframes=keyframes,
                                      minimax_refs=refs, seed=0)['minimax_payload'].cond
        values = payload.get('cond_video_latents', [])
        result['condition_order'] = ('keyframe+reference' if len(values) == 2
            and values[0] is key and values[1] is ref else
            'legacy_reference_overwrites_keyframe' if len(values) == 1
            and values[0] is ref else 'unknown')
    except Exception as exc:
        result['condition_order'] = f'unverified ({type(exc).__name__})'
    try:
        options = dict(keyframes=keyframes, refs=refs)
        if 'frame_count' in inspect.signature(PackedLayout.__init__).parameters:
            options['frame_count'] = 5
        layout = PackedLayout(1, 2, 2, 2, 1, **options)
        kinds = [kind for _, _, kind in layout.segments]
        result['layout'] = ','.join(kinds)
        cond = next(a for a, _, kind in layout.segments if kind == 'cond')
        audio = next(a for a, _, kind in layout.segments if kind == 'audio')
        result['anchor_aligned'] = bool(torch.isclose(
            layout.position_ids[cond, 0], layout.position_ids[audio, 0]))
    except Exception as exc:
        result['layout'] = f'unverified ({type(exc).__name__})'
    return result


def patch_inventory(model):
    options = getattr(model, 'model_options', {})
    transform = options.get('transformer_options', {})
    def count(value):
        return len(value) if isinstance(value, (Mapping, list, tuple)) else int(value is not None)
    return {
        'weight_patch_targets': count(getattr(model, 'patches', {})),
        'injections': count(getattr(model, 'injections', {})),
        'objects': tuple(sorted(str(k) for k in getattr(model, 'object_patches', {}))),
        'dit_blocks': tuple(str(k) for k in transform.get('patches_replace', {}).get('dit', {})),
        'transformer_patch_groups': count(transform.get('patches', {})),
        'wrappers': count(options.get('wrappers', {})),
        'attention_options': tuple(str(k) for k in transform if 'att' in str(k).lower()),
    }


def diagnose(model, stage):
    # Diagnostic failure must not block generation; sampling exceptions are NOT caught here.
    try:
        logging.info('Easy H3 compatibility [%s]: core=%s; patches=%s',
                     stage, probe_core(), patch_inventory(model))
        logging.info('Easy H3 compatibility: inventory only; stacked patch execution/quality is unverified. Existing patches retained.')
    except Exception as exc:
        logging.warning('Easy H3 compatibility: diagnostic unavailable (%s); continuing unchanged', type(exc).__name__)


def sampling_diagnostics(executor, *args, **kwargs):
    guider = getattr(executor, 'class_obj', None)
    patcher = getattr(guider, 'model_patcher', None)
    if patcher is not None:
        diagnose(patcher, 'sampling (including downstream patches)')
    return executor(*args, **kwargs)
