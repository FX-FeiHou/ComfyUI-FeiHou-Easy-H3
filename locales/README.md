# ComfyUI i18n

This folder uses ComfyUI's native custom-node localization format.

- Keep `en/nodeDefs.json` as the fallback/base translation.
- Add every new node input, output, option, display name, or description to both `en/nodeDefs.json` and `zh/nodeDefs.json` in the same change.
- Custom canvas widgets and dialogs must read `Comfy.Locale` and provide Chinese plus English text; do not rely only on the browser or operating-system language.

ComfyUI loads these translations at startup. Restart ComfyUI after modifying locale files, then select the language from `Comfy > Locale`.
