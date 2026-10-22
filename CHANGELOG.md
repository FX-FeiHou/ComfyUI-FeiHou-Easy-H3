# Changelog

## v1.4.23

- Expands automatic duration alignment to Video 1-3 and Audio 1-3, using each selected source's trimmed duration.
- Changes the Output node's audio output to emit the selected alignment source's audio: the video soundtrack when aligning to a video, or the trimmed audio when aligning to an audio reference. Off keeps the previous Audio 1 behaviour.
- Keeps legacy `audio`/`video`, Boolean and localized alignment values working in saved workflows.

## v1.4.22

- Distinguishes identically named custom rules from built-in rules so selecting RefMod keeps the intended rule binding.

- Adds the saved RefMod filmmaking prompt rule, unchanged, as the read-only built-in `refmod` template (`RefMod`). Main and Setup can select it without creating a custom rule; existing custom rules remain intact.
- Restores prompt-optimization rule names after ComfyUI's R / Refresh Node Definitions command. Custom rules such as RefMod stay selectable by name in the main node and Setup, preserving the selected rule's ID and reloading rules saved in another page.

## v1.4.21

- Adds optional upstream `H3_REF_MODS` inputs to the main node and Setup, with joint text/vision presentation, direct reference latents, strengths/copies, independent gallery slots and an appended `Output.reference_map` output. RefMods survive high-resolution/trigger-word condition rebuilds in both sampling passes; image mode asks users to switch to reference mode.
- Adds original RefMod Loader/Axis references to the main node and Setup's `@` menu, with metadata-only discovery, saved source binding, and strengths/copies/component filtering. RefMods lead each modality's numbering; ordinary uploads follow them. Removing ordinary media no longer invalidates RefMod mentions. Replace old placeholder-video mentions via the new menu; genuinely removed references still fail clearly. Both sampling passes and prompt optimization use the same ordering.
- Saves continuation tails for independent manual segments when production-pack batching is off, including the first segment in Auto mode and hard cuts. Tail saving is independent of applying a previous-segment guide, in both manual and package runs.
- Preserves zero overlap for independent segments and prefers the saved native tail when a later segment selects the previous video. Existing V7 wiring remains valid.
- Adds local-model dependency checks and an install/repair button to Standard settings, including native GGUF import, GPU offload and Qwen/MTMD interface checks.
- Adds a Manager install.py hook and Windows manual-ZIP install scripts that use ComfyUI's Python, choose matching GGUF binary wheels and retain existing Torch/NumPy versions.
- Checks dependencies before unloading H3 models for local prompt optimization, and reports missing dependencies at startup without automatically installing during node import.
- Supports the generic MTMD interface used by current official CPU wheels, and respects CPU mode for the vision projector. RH is unchanged.

## v1.4.20

- Brings the Standard GitHub package into parity with the local installation, including the previously local-only `FeiHouEasyH3ContinueOut` continuation/merge node and its registration.
- Includes the matching Setup continuation guidance, production-pack transition/context controls, continuation-video upload/selection, per-shot state, and frontend persistence.
- Connect `tail_latent` to FeiHou Toolbox Video Combine V2 to save continuation tails and join the completed segments.
- Includes the local face-refinement canvas presets from 360P through 1080P and preserves the v1.4.19 local LLM features. RH is unchanged.

## v1.4.19

- Standard local prompt optimization adds embedded bare-safetensors profiles for Qwen3 0.6B, 1.7B, 4B, 14B, 32B, and Mistral 7B Instruct v0.3; existing Qwen3-8B support remains compatible.
- Bare-safetensors detection now uses exact tensor structure instead of filename guesses, so renamed files remain detectable without confusing unrelated models with the same layer count.
- DeepSeek-V4 runtime detection now checks whether the installed Transformers package actually contains its model implementation instead of assuming every 5.x package supports it.

## v1.4.18

- Standard local prompt optimization can load a single `.safetensors` file for the built-in Qwen3-8B, DeepSeek-V4 Flash, and DeepSeek-V4 Pro profiles.
- Detects those profiles from the safetensors tensor header, so renamed files still work; complete Transformers directories and GGUF behavior remain unchanged.
- Resolves a matching tokenizer from local files or the model's Hugging Face repository. RH is unchanged.

## v1.4.17

- Scans standalone `.safetensors` files under `models/llm`, including files whose names were changed.
- Shows standalone weights in Standard settings as metadata-only entries when their `config.json` and tokenizer/model directory are missing; they are visible for diagnosis but cannot be selected for generation.
- Keeps complete Transformers directories runnable and prevents duplicate entries for their weight shards. RH is unchanged.

## v1.4.16

- Adds an optional Standard-edition local LLM/VLM prompt optimizer. It discovers Transformers and GGUF models under `ComfyUI/models/llm`, with optional GGUF `mmproj` selection, device, context, and output-token settings.
- Keeps local model controls inside the existing prompt-optimizer settings and hides the node's optimizer provider and guide controls until prompt optimization is enabled.
- Local optimization unloads ComfyUI models around the request and releases temporary caches before H3 sampling continues. The RH edition is unchanged.
- Adds optional local-model dependencies in `requirements-local-llm.txt`; API prompt optimization remains available without installing them.
- Identifies GGUF prompt models from their metadata, including Qwen-Image PE weights using the Qwen35 architecture; distinguishes the I2I vision projector from the T2I text model.

## v1.4.14

- Production-pack preview now loads the shot's images, videos and audio into the Media node connected to FeiHou Easy H3 Setup (previously only the standalone main node showed them; Setup and its Media node stayed empty).
- The pack loader also finds Setup through KJNodes Set/Get pairs, not only direct links and reroutes, so prompts, parameters and media load in such workflows.
- Front-end only; generation already used the pack media at execution. Validation: JavaScript syntax checks. Not re-tested in a live ComfyUI session.

## v1.4.15

- Removes the standalone Dual Sample 1st/2nd nodes from the node menu. Sample Enhancer 1st/2nd remain available and continue to use their shared internal sampling implementation.

## v1.4.13

- Removes the custom API host and LAN endpoint allow-lists. Custom HTTPS-compatible API URLs can be configured directly without allowed_api_hosts.json.
- Keeps URL parsing, the external HTTPS requirement, TLS verification, redirect blocking, and local-only settings routes.
- Validation: Python and JavaScript syntax checks plus static reference checks. GPU generation and live third-party API requests were not performed.

## v1.4.12

- Adds a Standard-edition official-node two-pass H3 enhancement node. It samples a coarse high-noise pass, enlarges the denoised video latent, and continues with a low-noise refinement pass.
- Adds a `Default Trigger Words` switch to both Sample Enhancer passes. It defaults on and prepends the built-in detail trigger to that pass's encoded prompt without exposing the trigger text in the node UI.
- LoRA loading is intentionally not integrated; connect externally configured first/refinement models, including any user-selected LoRA chain.
- Resizes MiniMax H3 reference/keyframe latent metadata with the video latent and preserves the nested audio latent. GPU quality and memory usage were not re-tested.

## v1.4.11

- Production-pack loader accepts shots with no reference images. Such shots run as text-to-video (image mode with no media); shots that reference audio still need at least one image or video, with a clear error.
- Resolves asset IDs through the Shotlist's inline project data, so a user image declared as `P1` (for example `../人物.jpg`) loads as P1 without being renamed. Files named after the ID (`P3.png`) still take priority; paths outside the package are ignored.
- Clearer missing/ambiguous asset messages. Folder paths for local ComfyUI are unchanged and need no ZIP. No UI or widget-order changes; RH edition unchanged. Loader validation tested with sample packs; GPU generation not re-tested.

## v1.4.10

- Removes the face-refinement 24 FPS restriction; uses the frame rate supplied by H3 context for existing timing calculations and output.
- No new controls, interpolation checks, or widget-order changes. Syntax and file synchronization verified; non-24-FPS GPU quality not re-tested.

## v1.4.9

- Checks standard linear LoRA dimensions before bypass loading. Skips incompatible layers with per-layer warnings and a partial-load summary; rejects LoRAs with no usable matched layers.
- Preserves matching adapters, strength, raw caches and multiple-LoRA composition. Other adapter types remain delegated to ComfyUI with an explicit unvalidated warning.
- No UI/widget-order changes. CPU compatibility regressions tested; full GPU quality not re-tested.

## v1.4.8

- Hides the LoRA stack bypass toggle and consistently enables additive bypass loading, including saved regular-mode workflows and legacy API inputs.
- Preserves LoRA names, strengths, enable flags, stack chaining and widget order. Does not change external official LoRA loaders or the RH edition.
- Validates saved-workflow migration, backend stack normalization and syntax. Full GPU quality and memory usage were not re-tested for this release.

## v1.4.7

- Restores explicitly authorized LAN inference APIs, including remote Ollama and HTTP OpenAI-compatible services, through the server-local allowed_api_hosts.json lan_endpoints list.
- Matches exact scheme, RFC1918 IPv4 address and port; does not automatically allow entire private networks. Existing public-host allow-lists and local Ollama remain compatible.
- Retains public HTTPS requirements, TLS verification, redirect blocking and local-only settings routes. The web UI cannot modify the allow-list.
- Documents LAN configuration and deployment requirements in both READMEs and the API Settings panel.
- Validation: allow-list regression tests, Python/frontend syntax and diff checks. No live LAN server connection was tested. RH is unchanged.

## v1.4.6

- Adds reference-video time ranges and play/stop previews; video frames and source audio are cropped together with 100 ms input normalization.
- Replaces the automatic-duration toggle with Off / Reference audio / Reference video alignment. Uses the first reference of the selected type, preserves legacy enabled toggles as audio alignment, and retains frame rounding and output-duration cropping.
- Includes clone-safe sampling handoff/cache fixes, multi-LoRA regular/bypass selection, and the standalone resolution selector with conditional custom dimensions.
- Improves production-pack multi-audio reference handling and documents the HTML integration contract.
- Adds experimental face refinement with optional original-audio conditioning, clean second-pass model selection, and automatic cropped reference-face input. Optional dependencies and limitations are documented in FACE_REFINE.md.
- Validation: Python/JavaScript syntax, JSON parsing, crop/playback simulations and saved-widget migration tests. No new GPU quality or live-cloud compatibility guarantees.

## v1.4.5

- Supports production-pack ZIP upload, inspection and preview from same-origin cloud ComfyUI browsers, including gateways that strip Origin while preserving same-origin Fetch Metadata. Adds credential-free access diagnostics.
- Resolves relative folder/ZIP paths under ComfyUI/input; retains absolute paths with remote directory restrictions and a server-local remote_roots configuration. Cloud deployments still require platform authentication.
- Leaves generation resolution and pixel dimensions under manual control: package inspection no longer validates or applies them, and old cached previews cannot override them. Aspect-ratio and FPS handling are retained.
- Documents cloud setup and folder permissions without a fixed domain allow-list. Local configuration and development tests are excluded from publication.
- Validation: seven local route/regression tests, syntax checks and user-confirmed cloud access. No GPU performance claims.

## v1.4.4

- Fixes the streamed final-layer wrapper for the updated ComfyUI 0.35 H3 calling convention, retaining compatibility fallbacks.
- Improves Production Pack Loader folder/HTML discovery, referenced-asset selection, optional voice-reference handling, generation order, preview, ZIP/path selection and responsive diagnostics.
- Adds an opt-in advanced text-encoder-only reference mode, without reference VAE latents. The built-in sparse-attention experiment is not included; use the official node for configurable sparse attention.
- Preserves saved widget positions, including migration from the brief two-switch experimental build, and adds a stable gap beneath the prompt editor.
- Preserves frontend cross-plugin isolation. Removes development test directories from the distributed package.
- Validation: targeted CPU/frontend regression checks and user-reported local testing; no new GPU benchmark claims.

## v1.4.3

- Isolates standard-edition frontend installation markers and matches exact node class IDs, preventing cross-plugin media parameter interference.
- Stops reference generation explicitly when visual media is missing or prompt references are unresolved.
- Adds the experimental Production Pack Loader: folder/ZIP Shotlist import, ordered referenced assets, per-shot prompts/audio ranges, and native queue index increment.
- Preserves existing main-node widget/output ordering and isolates preview settings between queued shots. Includes Chinese/English UI and usage documentation.
- Validation: CPU regression tests, frontend syntax and transport-order simulations, and the 36-shot sample import. Full GPU/cloud generation was not performed for this release.

## v1.4.2

- Adds a local custom-API host allow-list, validates every outbound prompt-API request, and blocks redirects to unvalidated destinations.
- Restricts Easy H3 settings, model-discovery, prompt-optimization, and LoRA-list routes to the local ComfyUI host.
- Documents the allow-list in the Settings UI and both READMEs.
- Uses English as the backend/default node-language fallback and completes native ComfyUI `locales/en` and `locales/zh` definitions for the Remix Loader and Digital Human/MV Duration Crop.

## v1.4.1

- Adds a play/stop control beside each reference-audio trim range. Preview playback starts at the normalized trim start, stops at the trim end, and never changes the source file or the audio sent to H3.

## v1.4.0

- Adds **Digital human/MV auto duration**. When enabled, Audio 1's trimmed duration controls generation and locks the manual duration field.
- Adds **FeiHou Easy H3 Digital human/MV Duration Crop** and a matching `duration_control` output on FeiHou Easy H3 Output. H3 now rounds automatic-duration sampling up to its required `5 + 17N` frame count; the crop node restores the precise trimmed-audio duration after decoding, while passing ordinary workflows through unchanged.

## v1.3.12

- Adds per-slot **reference-audio trimming** below the embedded Audio 1–3 gallery. Start and end are separate fields; ranges are saved with their audio when reordered. Times accept forgiving input and normalize to 100ms precision (`MM:SS:MMM`); trimming affects only the temporary H3 audio payload, never the uploaded source file.
- Adds **CLIP** and trimmed **Audio 1** outputs to FeiHou Easy H3 Output. They are appended after existing outputs so saved workflow links keep their original slot positions.
- Aligns the reference-image **Match generation size** rule with the official H3 node's downscale-only output-pixel-area behavior. When an I2V/FL2V workflow actually requests second-pass sampling, its shared context now uses H3's reference-to-video latent representation.
- Prevents the embedded media panel from self-expanding indefinitely in ComfyUI Modern Nodes mode.

## v1.3.11

- Fixes native ComfyUI external-input support for the embedded Easy H3 UI. Connected widgets now preserve their graph links instead of being overwritten by panel defaults during workflow serialization.
- The fix covers duration, resolution, aspect ratio, width/height, FPS, advanced settings, force offload, reference options, and prompt-optimizer controls. Embedded image/video/audio uploads and ordering are unchanged.

## v1.3.10

- Adds **FeiHou Easy H3 Remix Loader**: FL2VA and REF2VA share one Remix transformer, while first-pass and second-pass LoRA stacks remain independent. The optional second-pass model selector now uses **None** to keep the normal model path.
- Adds an Ollama-only **Disable thinking** setting. When enabled, Easy H3 sends the native Ollama `think: false` request option.
- Adds non-blocking **complete low-VRAM streamed-block conflict diagnostics**. When that experimental switch is enabled, existing H3 block, output-head, and attention patches are reported in the console before generation continues.

## v1.3.9

- **Force offload** now also runs a one-time ComfyUI-native allocator cleanup immediately after each sampler returns and before VAE decoding. It releases only unused cached memory; it does not clear memory during denoising steps.
- Replaces the attention-only experiment with optional **Complete low-VRAM streamed blocks (experimental)**. The embedded GPL-3.0-or-later-derived MAINodes implementation streams H3 QKV, attention, MLP/SwiGLU, and output-head work using exact bf16 K/V defaults. See NOTICE for attribution and license details.
- Updates the low-VRAM sample workflow to use the built-in complete block-streaming switch and removes the competing `ModelAttentionBackend / comfy kitchen attention` patch from that workflow.

## v1.3.8

- Fixes prompt-guide titles to read ComfyUI's current locale at display time rather than locking in the language during frontend module initialization.
- Adds the missing Chinese/English fallback label for **R2VA 加强版 / R2VA Enhanced**, so the guide remains correctly named while the backend scheme list is loading or when frontend cache is refreshed.

## v1.3.7

- Adds the built-in **R2VA Enhanced** prompt guide directly below “General only”. It uses the supplied six-section full-reference prompt template for complex reference-video, identity-retention, source-video, audio, and digital-human workflows.
- With **Force offload** enabled, a cached second-pass transformer from the prior workflow execution is now released before the next first-pass model is prepared, preventing the two stages from unnecessarily sharing VRAM.
- Further improves the embedded media gallery and prompt-editor responsive layout when resizing the node.

## v1.3.6

- Adds optional custom second-pass FL2VA and REF2VA model selection in the Loader, plus a dedicated second-sampling model output from the main node.
- Adds optional second-pass LoRA disabling and releases first-pass model / LoRA resources before second-pass sampling.
- Releases the text encoder and video/audio VAEs from VRAM after preprocessing and before sampling, reducing peak VRAM pressure while retaining automatic ComfyUI offload behavior.
- Adds ComfyUI-native Chinese/English node-definition translations and localizes embedded LoRA-stack and prompt-preview UI text according to the ComfyUI language setting.

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
