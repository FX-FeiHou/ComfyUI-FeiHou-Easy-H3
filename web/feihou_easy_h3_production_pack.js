import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { renderShotlist } from "./production_pack_parser.js";
import { applyProductionShotPreview } from "./feihou_easy_h3_ui.js";

const CLASS = "FeiHouEasyH3ProductionPackLoader";
const zh = () => /^zh/i.test(String(app.ui?.settings?.getSettingValue?.("Comfy.Locale") || navigator.language));
const tr = (en, cn) => zh() ? cn : en;
const widget = (node, name) => node.widgets?.find((w) => w.name === name);
const value = (node, name) => widget(node, name)?.value;
function set(node, name, data) {
    const w = widget(node, name);
    if (!w) return;
    w.value = data;
    if (w._state) w._state.value = data;
}

async function request(action, data, file) {
    const response = await api.fetchApi(`/feihou_easy_h3/production_pack/${action}`, {
        method: "POST",
        headers: { "X-FeiHou-Pack": "1", "Content-Type": file ? "application/octet-stream" : "application/json" },
        body: file || JSON.stringify(data),
    });
    const result = await response.json();
    if (!response.ok || result.error) throw new Error(result.error || `HTTP ${response.status}`);
    return result;
}

function payload(node) {
    return Object.fromEntries(["source", "shotlist_file", "audio_file", "shot_index", "prompt_language", "package_id"]
        .map((name) => [name, value(node, name)]));
}

function status(node, message) {
    const w = widget(node, "pack_status");
    if (w) w.value = message;
    node.setDirtyCanvas?.(true, true);
}

function display(node, shot) {
    status(node, `${shot.index}/${shot.total} · ${shot.id} · ${shot.params.seconds}s\n${shot.range}\n${shot.refs.join(" → ")}`);
    const graph = node.graph;
    // Follow standard reroutes as well as direct links. Never modify unrelated nodes.
    const visited = new Set();
    const visit = (origin) => {
        if (!origin || visited.has(origin.id)) return;
        visited.add(origin.id);
        for (const output of origin.outputs || []) {
            for (const id of output.links || []) {
                const link = graph?.links?.get?.(id) || graph?.links?.[id];
                const target = graph?.getNodeById(link?.target_id);
                if (!target) continue;
                if ((target.comfyClass || target.type) === "FeiHouEasyH3"
                    && target.inputs?.[link.target_slot]?.name === "production_shot") {
                    applyProductionShotPreview(target, shot);
                } else if (/reroute/i.test(target.type || "")) visit(target);
            }
        }
    };
    visit(node);
}

async function preview(node) {
    display(node, await request("preview", payload(node)));
}

async function prepare(node, file) {
    if (node.__h3PackBusy) return;
    node.__h3PackBusy = true;
    set(node, "package_id", "");
    status(node, tr("Reading final Shotlist…", "正在读取最终分镜页面…"));
    try {
        const info = await request(file ? "upload" : "inspect", payload(node), file);
        if (info.source) set(node, "source", info.source);
        const shots = await renderShotlist(info.html);
        const ready = await request("prepare", {
            package_id: info.package_id, shots, audio_file: value(node, "audio_file"),
        });
        set(node, "package_id", ready.package_id);
        // Do not reset a saved resume index, even if it is outside the new pack.
        // The preview then reports the exact invalid range rather than wrapping.
        await preview(node);
        app.graph?.change?.();
    } catch (error) {
        status(node, tr("Load failed: ", "载入失败：") + error.message);
        app.extensionManager?.toast?.add?.({ severity: "error", summary: "Easy H3", detail: error.message, life: 12000 });
    } finally {
        node.__h3PackBusy = false;
    }
}

function localize(node) {
    const names = {
        source: ["Package folder / ZIP path", "制作包文件夹 / ZIP 路径"],
        shotlist_file: ["Shotlist path (optional)", "分镜 HTML 相对路径（可留空）"],
        audio_file: ["Soundtrack path (optional)", "歌曲相对路径（可留空）"],
        shot_index: ["Shot index", "分镜序号"],
        prompt_language: ["Prompt language", "提示词语言"],
        package_id: ["Prepared package ID (automatic)", "制作包准备编号（自动）"],
        load_pack: ["Load / refresh package", "载入 / 刷新制作包"],
        upload_zip: ["Upload ZIP", "上传 ZIP 压缩包"],
        preview_shot: ["Preview current shot", "预览当前分镜"],
        pack_status: ["Package status", "制作包状态"],
    };
    for (const w of node.widgets || []) if (names[w.name]) w.label = tr(...names[w.name]);
    if (node.outputs?.[0]) node.outputs[0].label = tr("Production shot", "制作包分镜");
}

app.registerExtension({
    name: "FeiHou.EasyH3.ProductionPack",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== CLASS) return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = created?.apply(this, arguments);
            const cache = widget(this, "package_id");
            if (cache) {
                // Keep native serialization: this ID must survive save/restart.
                if (cache.inputEl) cache.inputEl.readOnly = true;
            }
            // Native ComfyUI batching snapshots this index for each queued prompt.
            // onConfigure restores the user's saved fixed/increment choice later.
            const control = widget(this, "control_after_generate");
            if (control) control.value = "increment";
            this.addWidget("button", "load_pack", null, () => prepare(this), { serialize: false });
            this.addWidget("button", "upload_zip", null, () => {
                const picker = document.createElement("input");
                picker.type = "file";
                picker.accept = ".zip,application/zip";
                picker.onchange = () => picker.files?.[0] && prepare(this, picker.files[0]);
                picker.click();
            }, { serialize: false });
            this.addWidget("button", "preview_shot", null, () => preview(this).catch((e) => status(this, e.message)), { serialize: false });
            this.addWidget("text", "pack_status", tr("Load a package, then queue N shots with increment enabled.", "先载入制作包；序号生成后选 increment，再设置队列次数。"), () => {}, { serialize: false });
            for (const name of ["source", "shotlist_file", "audio_file"]) {
                const w = widget(this, name), callback = w?.callback;
                if (w) w.callback = (...args) => {
                    callback?.apply(w, args);
                    set(this, "package_id", "");
                    status(this, tr("Source changed. Load / refresh again.", "来源已改变，请重新载入 / 刷新。"));
                };
            }
            localize(this);
            this.size[0] = Math.max(410, this.size[0]);
            return result;
        };
        const executed = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            const result = executed?.apply(this, arguments);
            if (message?.production_shot?.[0]) display(this, message.production_shot[0]);
            return result;
        };
    },
    setup() {
        const refresh = () => {
            for (const node of app.graph?._nodes || []) {
                if ((node.comfyClass || node.type) === CLASS) localize(node);
                if ((node.comfyClass || node.type) === "FeiHouEasyH3") {
                    const input = node.inputs?.find((s) => s.name === "production_shot");
                    if (input) input.label = tr("Production shot", "制作包分镜");
                }
            }
        };
        api.addEventListener?.("graphConfigured", refresh);
        app.ui?.settings?.addEventListener?.("change", refresh);
    },
    nodeCreated(node) {
        if ((node.comfyClass || node.type) === "FeiHouEasyH3") {
            const input = node.inputs?.find((s) => s.name === "production_shot");
            if (input) input.label = tr("Production shot", "制作包分镜");
        }
    },
});
