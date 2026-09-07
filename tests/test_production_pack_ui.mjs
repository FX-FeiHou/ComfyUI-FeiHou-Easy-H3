import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

// Exercise the real preview functions, without a ComfyUI browser or GPU.
const source = fs.readFileSync(new URL("../web/feihou_easy_h3_ui.js", import.meta.url), "utf8");
const start = source.indexOf("function productionFallbackValues(node)");
const end = source.indexOf("function installPromptEditorSoon(node)", start);
const context = vm.createContext({
    EMBEDDED_MEDIA_PROP: "media",
    isTarget: () => true,
    getWidget: (n, name) => n.widgets.find((w) => w.name === name),
    getWidgetValue: (n, name) => n.widgets.find((w) => w.name === name)?.value,
    ensureEmbeddedMedia: () => {},
    setPromptFromOptimizedText: (n, text) => { n.prompt = text; },
    syncModeWidgets: () => {}, renderEmbeddedMediaGallery: () => {},
    renderEditorFromNode: () => {}, refreshVueNodeWidgets: () => {},
});
vm.runInContext(source.slice(start, end).replace("export function", "function"), context);
const node = { widgets: [{ name: "fps", value: 24 }, { name: "resolution", value: "720P" }, { name: "aspect_ratio", value: "16:9" }] };
context.applyProductionShotPreview(node, { media: [{ filename: "P1.png" }], params: { fps: 30 }, prompt: "first" });
assert.equal(node.widgets[0].value, 30);
assert.equal(context.productionFallbackValues(node).fps, 24);
context.applyProductionShotPreview(node, { media: [{ filename: "P2.png" }], params: {}, prompt: "second" });
assert.equal(node.widgets[0].value, 24);
assert.equal(node.properties.media.length, 1);
assert.equal(node.properties.media[0].filename, "P2.png");
node.widgets[0].value = 60; // user explicitly changes the default
assert.equal(context.productionFallbackValues(node).fps, 60);
const restored = JSON.parse(JSON.stringify(node));
context.applyProductionShotPreview(restored, { media: [], params: { fps: 48 }, prompt: "third" });
assert.equal(context.productionFallbackValues(restored).fps, 60);
assert.equal(restored.widgets[0].value, 48);
console.log("PASS: per-shot gallery replacement, fallback isolation, manual changes, workflow restore");

const extensionSource = fs.readFileSync(new URL("../web/feihou_easy_h3_production_pack.js", import.meta.url), "utf8").replace(/^import .*;\r?\n/gm, "");
let extension;
vm.runInNewContext(extensionSource, {
    app: { ui: { settings: { getSettingValue: () => "en" } }, registerExtension: (e) => { extension = e; } },
    api: {}, navigator: { language: "zh-CN" }, applyProductionShotPreview() {}, renderShotlist() {},
});
class Loader {
    constructor() {
        this.widgets = ["package_id", "control_after_generate", "source", "shotlist_file", "audio_file"].map((name) => ({ name, value: name === "control_after_generate" ? "fixed" : "" }));
        this.size = [200, 200]; this.outputs = [{}];
    }
    addWidget(type, name, value, callback, options) { this.widgets.push({ type, name, value, callback, options }); }
}
extension.beforeRegisterNodeDef(Loader, { name: "FeiHouEasyH3ProductionPackLoader" });
const loader = new Loader();
loader.onNodeCreated();
assert.equal(loader.widgets.find((w) => w.name === "control_after_generate").value, "increment");
assert.equal(loader.widgets.find((w) => w.name === "source").label, "Package folder / ZIP path");
assert.ok(loader.widgets.find((w) => w.name === "package_id").options?.serialize !== false);
assert.equal(loader.widgets.filter((w) => w.options?.serialize === false).length, 4);
console.log("PASS: native increment default, English UI follows Comfy.Locale, preparation ID stays serializable");
