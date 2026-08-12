# Changelog

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
