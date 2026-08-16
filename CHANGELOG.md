# Changelog

## v1.3.5

- Embedded images, videos, and audio can now be dragged within their own galleries to change their reference order. The new order is saved in the workflow and used by H3 and prompt optimization.

## v1.3.4

- Fixes the “＋ Add custom API” button in ComfyUI Settings. It now creates a custom API tab directly instead of relying on a browser-native prompt dialog that some ComfyUI hosts suppress.
- Custom API cards now include an auto-saving, editable service-name field.

## v1.3.3

- Fixes FeiHou Easy H3 Loader model dropdowns retaining a stale plugin-level
  file list. Newly saved models under `models/diffusion_models` now appear the
  next time ComfyUI refreshes node definitions, without requiring a further
  server restart.

## v1.3.2

- Built-in prompt-optimization services now start with only their editable default API address; no model is preloaded or selected automatically.
- Migrates only models injected by older plugin defaults: user-added models and API keys are preserved, while an obsolete default selection is cleared.

## v1.3.1

- Fixes saved workflows failing validation while prompt optimization is disabled. Dynamic API service/model and prompt-guide selections are no longer validated against the current Settings list before the node can read the optimization switch.

## v1.3.0

- Prompt optimization now sends API-only JPEG copies of reference images, capped by the node's selected `ref_image_size` and encoded at quality 85. Original H3 media is never modified.
- Reference videos now contribute compact first, middle, and last visual keyframes to compatible prompt-optimization APIs; standalone audio and video soundtracks remain local to H3 generation.
- Adds safe prompt-API diagnostics: logs include endpoint, model, API format, and media-size summaries while redacting API keys, filenames, Base64 payloads, and media contents.

## v1.2.0

- Removes filename-based filtering from the H3 Loader. Renamed community model files are now listed from their respective ComfyUI model folders.
- Lets users manually assign the selected diffusion model, text encoder, video VAE, and audio VAE to each H3 Loader role.
- Keeps `.safetensors` and `.gguf` discovery across the relevant ComfyUI model directories.

## v1.1.0

- Fixes workflow validation when prompt optimization is disabled: the serialized service value remains valid while the optimization switch controls whether an API request is made.
- Fixes an issue where ComfyUI serializing a Boolean as the string `"false"` could still incorrectly run prompt optimization.
- Improves compatibility with saved workflows and avoids unnecessary prompt-optimization API calls when the feature is off.

## v1.0.0

- Adds a visible `modified` declaration to the main node header, including H3 source (`nkxx188/ComfyUI-MiniMaxH3-Easy`) and API source (`yawiii/ComfyUI-Prompt-Assistant`).
- Embeds up to 9 images, 3 videos, and 3 standalone audio files directly in the Easy H3 node.
- Adds the FeiHou LoRA Stack input flow for the Easy H3 Loader.
- Adds ComfyUI Settings integration for API providers, configured service/model selection, and prompt-optimization rules.
- Applies the selected prompt scheme during normal node execution, and carries the final result to H3 Context / Prompt Preview.

## Attribution

This release includes modified work derived from [nkxx188/ComfyUI-MiniMaxH3-Easy](https://github.com/nkxx188/ComfyUI-MiniMaxH3-Easy) (MIT) and adapted API/prompt-optimizer portions from [yawiii/ComfyUI-Prompt-Assistant](https://github.com/yawiii/ComfyUI-Prompt-Assistant) (GNU GPL v3). The repository as a whole is released under GNU GPL v3; see [NOTICE](NOTICE), [LICENSE](LICENSE), and [LICENSES/MIT-ComfyUI-MiniMaxH3-Easy.txt](LICENSES/MIT-ComfyUI-MiniMaxH3-Easy.txt).
