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

Place the LoRA stack to the left of `FeiHou Easy H3 Loader` and connect its `lora_stack` output to the loader's left-side `LoRA stack` input. The loader applies every enabled LoRA internally; the LoRA node is not inserted into the main node's downstream `MODEL` chain.

Prompt configuration is shown directly in the dedicated `🐵Easy H3` page in ComfyUI Settings, without secondary dialogs. Base URLs are editable, pasted API keys save automatically, and preset providers contain neither keys nor models. The same page contains the single prompt-optimization rule list. The main node shows the API and scheme controls only when Advanced options is enabled; the scheme is ignored until a configured API provider is selected. The final prompt is carried in H3 Context and can be connected from `FeiHou Easy H3 Output` to the bundled Prompt Preview node.

### Custom API host allow-list

The built-in Zhipu, xFlow, Alibaba Cloud Bailian, and DeepSeek hosts are already allowed. Ollama is limited to local `localhost`, `127.0.0.1`, or `::1`. Before configuring a new third-party API, start ComfyUI once so the plugin creates this local file:

`ComfyUI/user/default/ComfyUI-FeiHou-Easy-H3/allowed_api_hosts.json`

Add the API **hostname only** to its `hosts` array—do not enter `https://`, a path, port, wildcard, or IP address:

```json
{
  "version": 1,
  "hosts": [
    "api.example.com",
    "llm.company.cn"
  ]
}
```

Save the file and restart ComfyUI. You can then enter `https://api.example.com/v1`, its API key, and models in the Custom API panel. The allow-list is intentionally maintained in a local file and cannot be changed from the Settings page: it prevents an exposed ComfyUI server from being induced to request private-network addresses or send API keys to a malicious server. Settings, model discovery, and immediate prompt-optimization routes also accept requests only from the host running ComfyUI.

The package uses unique `FeiHouEasyH3*` node IDs and dedicated prompt-optimizer routes, so it can be installed alongside the original project.

## Attribution, changes, and license

This is a **modified work**, not an independent reimplementation. Its H3-node upstream source is [ComfyUI-MiniMaxH3-Easy](https://github.com/nkxx188/ComfyUI-MiniMaxH3-Easy) by `nkxx188`, which is MIT-licensed. Its original copyright notice and MIT text are retained in [LICENSES/MIT-ComfyUI-MiniMaxH3-Easy.txt](LICENSES/MIT-ComfyUI-MiniMaxH3-Easy.txt). The original project asks substantial reuses/adaptations to credit `nkxx188` and `ComfyUI-MiniMaxH3-Easy`; this repository does so in the node header, this README, [NOTICE](NOTICE), and every release.

The API-service configuration, model-discovery, and prompt-optimization implementation borrows from [yawiii/ComfyUI-Prompt-Assistant](https://github.com/yawiii/ComfyUI-Prompt-Assistant) by `yawiii`, which is GNU GPL v3-licensed. Because this repository includes adapted portions of that project, the repository as a whole is distributed under the [GNU GPL v3](LICENSE).

**Complete low-VRAM streamed blocks (experimental)** contains a derivative of `H3 Streamed Blocks` from [matlowai/ComfyUI-MAINodes](https://github.com/matlowai/ComfyUI-MAINodes), Copyright (C) 2026 MATLOWAI, under GPL-3.0-or-later. The retained source, full license text, and integration notice are available in [NOTICE](NOTICE), `third_party/mainodes_h3_streamed_blocks.py`, and `LICENSES/GPL-3.0-or-later-ComfyUI-MAINodes.txt`.

FeiHou-specific changes include the fixed 9-image / 3-video / 3-audio embedded gallery, gallery upload/persistence, FeiHou LoRA Stack integration, settings-page API and prompt-rule management, configured service/model selection, runtime prompt optimization, and prompt-preview outputs. Reference conditioning follows ComfyUI's official `MiniMax H3 Reference to Video` behavior and limits: 9 images, 3 videos, and 3 standalone audio clips.

The complete license and preservation notice are in [LICENSE](LICENSE) and [NOTICE](NOTICE). The software is provided as-is, without warranty.
