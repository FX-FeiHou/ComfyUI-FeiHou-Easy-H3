# ComfyUI-FeiHou-Easy-H3

Standard-edition experimental feature: [Production Pack Loader](docs/production-packs.md#english-quick-start). Read a folder/ZIP Shotlist, apply per-shot prompts, audio ranges and referenced images, and advance with native queue increment.

**English** | [中文](README.md)

A MiniMax H3 custom-node package with all reference-media loading embedded directly in the main node.

The main node provides:

- nine image slots in a fixed 3 × 3 gallery;
- three video slots;
- three standalone audio slots;
- click-to-pick and drag-and-drop upload;
- inline image/video previews and persistent workflow metadata;
- the original `@` reference editor for `<Picture i>`, `<Video i>`, and `<Audio i>`;
- default 24 FPS and 10-second generation;
- reference-image short-edge presets of 480, 544, 640, 736, 768, 832, 928, 1024, and 1088;
- Advanced options include **Force offload (with post-sampling cache release)** and optional **Complete low-VRAM streamed blocks (experimental)**. The former releases unused allocator cache after sampling and before VAE decoding; the latter streams H3 QKV, attention, MLP/SwiGLU, and output-head work. Do not chain `ModelAttentionBackend / comfy kitchen attention` when this option is enabled;
- prompt-optimizer settings for Zhipu, xFlow API aggregation, Ollama, Alibaba Cloud, DeepSeek, and custom OpenAI/Gemini/Ollama-compatible APIs;
- local GGUF, complete Transformers directories, and bare `.safetensors` files with built-in architecture detection; the Standard edition embeds Qwen3 0.6B, 1.7B, 4B, 8B, 14B, 32B, and Mistral 7B Instruct v0.3 configurations, so renaming a file does not break detection;
- built-in and user-defined prompt schemes.

Video soundtracks remain paired with their source videos. The three audio slots are standalone audio references. In image/first-last-frame mode, only the first two image slots are active; switching modes preserves the remaining gallery selections.

## Install

Copy the current `Easy H3` folder into `ComfyUI/custom_nodes/` (optionally rename it to `ComfyUI-FeiHou-Easy-H3`), update ComfyUI to a release that includes the official MiniMax H3 nodes, and restart ComfyUI.

Nodes appear under `FeiHou Easy H3`:

- `加载LoRA（旁路，仅模型）（用于调试）` (the same native canvas stack UI as `FeiHou LoRA Stack (Merge/Extract)`)
- `FeiHou Easy H3 Loader`
- `ComfyUI-FeiHou-Easy-H3`
- `FeiHou Easy H3 Model Adapter`
- `FeiHou Easy H3 Output`
- `FeiHou Easy H3 Prompt Preview`
- `FeiHou Easy H3 接续合并` (`FeiHouEasyH3ContinueOut`): trims overlapping video/audio and provides the continuation tail. Connect `tail_latent` to [FeiHou Toolbox Video Combine V2](https://github.com/FX-FeiHou/ComfyUI-FeiHou-Toolbox) to save tails and merge completed segments.

Place the LoRA stack to the left of `FeiHou Easy H3 Loader` and connect its `lora_stack` output to the loader's left-side `LoRA stack` input. The loader applies every enabled LoRA internally; the LoRA node is not inserted into the main node's downstream `MODEL` chain.

Prompt configuration is shown directly in the dedicated `🐵Easy H3` page in ComfyUI Settings, without secondary dialogs. Base URLs are editable, pasted API keys save automatically, and preset providers contain neither keys nor models. The same page contains the single prompt-optimization rule list. The main node shows the API and scheme controls only when Advanced options is enabled; the scheme is ignored until a configured API provider is selected. The final prompt is carried in H3 Context and can be connected from `FeiHou Easy H3 Output` to the bundled Prompt Preview node.

### Local-model dependencies

Manager can run the bundled install hook. Manual ZIP users can open Easy H3 settings → Local models → **Check dependencies** / **Install / repair dependencies**, or run `install-local-llm.bat` on Windows. The installer uses ComfyUI's Python and selects matching GGUF binaries. Restart when prompted. See the [dependency guide](docs/local-model-dependencies.md#english-quick-start).

### Custom APIs

Custom APIs can be entered directly as complete HTTPS URLs, such as https://api.example.com/v1; no domain allow-list file is required. Configure the API key and models in the Easy H3 settings page. API keys remain in the local ComfyUI user directory and are not exported with workflows.

External prompt APIs still require HTTPS. Local Ollama may use http://localhost:11434, 127.0.0.1, or ::1. API settings, model discovery, and immediate prompt-optimization routes remain available only from the machine running ComfyUI.

The package uses unique FeiHouEasyH3* node IDs and dedicated prompt-optimizer routes, so it can be installed alongside the original project.

### RefMod references

Both the main node and `FeiHou Easy H3 Setup` accept optional `refmods` (`H3_REF_MODS`). Install [ComfyUI-MiniMaxH3Mod](https://github.com/Luisacaotica/ComfyUI-MiniMaxH3Mod), connect `Load H3 RefMods.mods` (or Create / Master `.mods`) directly, and select Reference Video mode. Existing first/second-pass wiring stays the same. Do not also Apply or Text Encode the same bundle, which would inject it twice.

External RefMods do not consume the gallery's 9-image / 3-video / 3-audio slots. They still add reference tokens and memory cost. Upstream strengths, copies and modality selections are honored; stored reference latents go directly to the sampler, with visual decoding only for H3 text/vision presentation. Saved Apply / Step Curve overrides are not applied automatically. Advanced reference-text-only mode omits RefMod latent blocks as it does for ordinary references.

RefMods come first, followed by ordinary media, with independent Picture / Video / Audio counters. Connecting the original Load H3 RefMods / Axis populates the main node and Setup's `@` menu with each active reference's type, number and name. No placeholder upload is needed: video-kind Kirara is `<Video 1>` and an uploaded video becomes `<Video 2>`. Eight image-kind RefMods use `<Picture 1>`–`<Picture 8>`; nine gallery images then use `<Picture 9>`–`<Picture 17>`. Eight video-kind RefMods use `<Video 1>`–`<Video 8>`, while gallery images still start at `<Picture 1>`. Copies and selected bundle members receive individual labels; zero-strength and excluded references do not.

RefMod mentions persist with their source in saved workflows. Removing ordinary media leaves those mentions intact; disabling or removing the referenced mod marks it unresolved rather than rebinding it to an upload. Replace old placeholder-video mentions through the new `@` menu. Stacked photos can be video-kind. Connect `Easy H3 Output.reference_map` to Prompt Preview or a text display for the actual mapping, which is also logged. The menu reads metadata without loading tensors or models. Direct Create / Master connections still support manually typed official tags. Keep different characters in separate references. Identity binding and generation quality need practical testing.

Visual-RefMod-only reference workflows are supported. Audio-only references still require ordinary or RefMod visuals. Video presentation follows upstream Text Encode defaults (24 fps reconstruction, 2 fps presentation), without recovering original timestamps. Image/first-last-frame mode reports a switch-to-reference-mode error when active RefMods are connected. New sockets are appended to preserve existing connections.

## Attribution, changes, and license

This is a **modified work**, not an independent reimplementation. Its H3-node upstream source is [ComfyUI-MiniMaxH3-Easy](https://github.com/nkxx188/ComfyUI-MiniMaxH3-Easy) by `nkxx188`, which is MIT-licensed. Its original copyright notice and MIT text are retained in [LICENSES/MIT-ComfyUI-MiniMaxH3-Easy.txt](LICENSES/MIT-ComfyUI-MiniMaxH3-Easy.txt). The original project asks substantial reuses/adaptations to credit `nkxx188` and `ComfyUI-MiniMaxH3-Easy`; this repository does so in the node header, this README, [NOTICE](NOTICE), and every release.

The API-service configuration, model-discovery, and prompt-optimization implementation borrows from [yawiii/ComfyUI-Prompt-Assistant](https://github.com/yawiii/ComfyUI-Prompt-Assistant) by `yawiii`, which is GNU GPL v3-licensed. Because this repository includes adapted portions of that project, the repository as a whole is distributed under the [GNU GPL v3](LICENSE).

**Complete low-VRAM streamed blocks (experimental)** contains a derivative of `H3 Streamed Blocks` from [matlowai/ComfyUI-MAINodes](https://github.com/matlowai/ComfyUI-MAINodes), Copyright (C) 2026 MATLOWAI, under GPL-3.0-or-later. The retained source, full license text, and integration notice are available in [NOTICE](NOTICE), `third_party/mainodes_h3_streamed_blocks.py`, and `LICENSES/GPL-3.0-or-later-ComfyUI-MAINodes.txt`.

FeiHou-specific changes include the fixed 9-image / 3-video / 3-audio embedded gallery, gallery upload/persistence, FeiHou LoRA Stack integration, settings-page API and prompt-rule management, configured service/model selection, runtime prompt optimization, prompt-preview outputs, and an independent RefMod input. Ordinary reference conditioning follows ComfyUI's official `MiniMax H3 Reference to Video` behavior. Gallery limits do not include external RefMods.

The complete license and preservation notice are in [LICENSE](LICENSE) and [NOTICE](NOTICE). The software is provided as-is, without warranty.
