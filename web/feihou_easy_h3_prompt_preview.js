import { app } from "../../scripts/app.js";

const NODE_CLASS = "FeiHouEasyH3PromptPreview";
const VALUE_PROP = "feihou_h3_prompt_preview_text";
function isChineseLocale() {
    const locale = app.ui?.settings?.getSettingValue?.("Comfy.Locale") || navigator.language || "en";
    return /^zh(?:[-_]|$)/i.test(String(locale));
}

function previewTitle() {
    return isChineseLocale() ? "FeiHou Easy H3 提示词预览" : "FeiHou Easy H3 Prompt Preview";
}

function previewPlaceholder() {
    return isChineseLocale()
        ? "执行工作流后在这里显示最终扩写 / 反推提示词"
        : "The final expanded / inferred prompt appears here after execution";
}

function installStyle() {
    if (document.getElementById("feihou-h3-prompt-preview-style")) return;
    const style = document.createElement("style");
    style.id = "feihou-h3-prompt-preview-style";
    style.textContent = `
      .fh-h3-prompt-preview{width:100%;height:100%;min-height:150px;box-sizing:border-box;padding:10px 12px;border:1px solid rgba(255,255,255,.13);border-radius:7px;background:#17181c;color:#e7e8ed;resize:none;font:13px/1.55 system-ui,sans-serif;white-space:pre-wrap}.fh-h3-prompt-preview:focus{outline:1px solid #3488f6}
    `;
    document.head.append(style);
}

function installPreview(node) {
    if (!node || node.__fhPromptPreviewInstalled || typeof node.addDOMWidget !== "function") return;
    node.__fhPromptPreviewInstalled = true;
    node.properties ||= {};
    const textarea = document.createElement("textarea");
    textarea.className = "fh-h3-prompt-preview";
    textarea.readOnly = true;
    textarea.placeholder = previewPlaceholder();
    textarea.value = String(node.properties[VALUE_PROP] || "");
    textarea.addEventListener("pointerdown", (event) => event.stopPropagation());
    textarea.addEventListener("wheel", (event) => event.stopPropagation(), { passive: true });
    const widget = node.addDOMWidget("prompt_preview_display", "prompt_preview_display", textarea, {
        serialize: false,
        getMinHeight: () => 160,
    });
    if (widget) widget.serialize = false;
    node.__fhPromptPreviewElement = textarea;
    if (Array.isArray(node.size) && (node.size[0] < 420 || node.size[1] < 240)) {
        node.setSize?.([Math.max(420, node.size[0]), Math.max(240, node.size[1])]);
    }
}

function updatePreview(node, message) {
    const payload = Array.isArray(message?.text) ? message.text[0] : message?.text;
    const value = String(payload ?? "");
    node.properties ||= {};
    node.properties[VALUE_PROP] = value;
    if (node.__fhPromptPreviewElement) node.__fhPromptPreviewElement.value = value;
    node.setDirtyCanvas?.(true, true);
}

app.registerExtension({
    name: "FeiHouEasyH3.PromptPreview",
    setup() {
        installStyle();
    },
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData?.name !== NODE_CLASS) return;
        nodeData.display_name = previewTitle();
        nodeData.category = "FeiHou Easy H3";
        const originalCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function onNodeCreatedPromptPreview() {
            const result = originalCreated?.apply(this, arguments);
            this.title = nodeData.display_name;
            installPreview(this);
            return result;
        };
        const originalConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function onConfigurePromptPreview(info) {
            const result = originalConfigure?.apply(this, arguments);
            this.title = previewTitle();
            installPreview(this);
            if (info?.properties?.[VALUE_PROP] != null) updatePreview(this, { text: [info.properties[VALUE_PROP]] });
            return result;
        };
        const originalExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function onExecutedPromptPreview(message) {
            const result = originalExecuted?.apply(this, arguments);
            updatePreview(this, message);
            return result;
        };
        const originalSerialize = nodeType.prototype.onSerialize;
        nodeType.prototype.onSerialize = function onSerializePromptPreview(info) {
            const result = originalSerialize?.apply(this, arguments);
            if (info) {
                info.properties ||= {};
                info.properties[VALUE_PROP] = String(this.__fhPromptPreviewElement?.value || this.properties?.[VALUE_PROP] || "");
            }
            return result;
        };
    },
});
