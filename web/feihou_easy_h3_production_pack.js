import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { renderShotlist } from "./production_pack_parser.js";
import { applyProductionShotPreview } from "./feihou_easy_h3_ui.js";

const CLASS = "FeiHouEasyH3ProductionPackLoader";
// Original serialized positions: presentation-only buttons must never shift them.
const SAVED_WIDGETS = ["source", "shotlist_file", "audio_file", "shot_index", "control_after_generate", "prompt_language", "package_id"];
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
        credentials: "same-origin",
        headers: { "X-FeiHou-Pack": "1", "Content-Type": file ? "application/octet-stream" : "application/json" },
        body: file || JSON.stringify(data),
    });
    const result = await response.json();
    if (!response.ok || result.error) throw new Error(result.error || `HTTP ${response.status}`);
    return result;
}

function sourcePath(node) {
    const uploaded = node.properties?.feihou_pack_upload;
    return uploaded && value(node, "source") === uploaded.name ? uploaded.path : value(node, "source");
}

function payload(node) {
    return { ...Object.fromEntries(["source", "shot_index", "prompt_language", "package_id"]
        .map((name) => [name, value(node, name)])), source: sourcePath(node) };
}

function status(node, message) {
    const w = widget(node, "pack_report");
    if (w) w.value = message;
    if (node.__h3PackReport) node.__h3PackReport.value = message;
    node.setDirtyCanvas?.(true, true);
}

function display(node, shot) {
    let applied = 0;
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
                    applied++;
                } else if (/reroute/i.test(target.type || "")) visit(target);
            }
        }
    };
    visit(node);
    status(node, `${shot.index}/${shot.total} · ${shot.id} · ${shot.params.seconds}s\n${shot.range}\n${shot.refs.join(" → ")}\n`
        + (applied ? tr(`Applied to ${applied} Easy H3 node(s).`, `已载入 ${applied} 个 Easy H3 节点的提示词、参数和参考媒体。`)
            : tr("Connect the production-shot output to Easy H3 first.", "未找到连接的 Easy H3 节点，请连接制作包分镜接口。")));
}

async function preview(node) {
    display(node, await request("preview", payload(node)));
}

async function prepare(node, file) {
    // A later upload/path edit wins, even if an earlier request finishes last.
    const revision = node.__h3PackRevision = (node.__h3PackRevision || 0) + 1;
    const current = () => revision === node.__h3PackRevision;
    if (file) {
        node.properties ||= {};
        delete node.properties.feihou_pack_upload;
        set(node, "source", file.name);
    }
    set(node, "package_id", "");
    status(node, tr("Reading final Shotlist…", "正在读取最终分镜页面…"));
    try {
        const info = await request(file ? "upload" : "inspect", payload(node), file);
        if (!current()) return;
        if (file && info.source) {
            node.properties.feihou_pack_upload = { name: file.name, path: info.source };
            app.graph?.change?.();
        }
        const shots = await renderShotlist(info.html);
        if (!current()) return;
        const ready = await request("prepare", {
            package_id: info.package_id, shots, diagnose: true, prompt_language: value(node, "prompt_language"),
        });
        if (!current()) return;
        set(node, "package_id", ready.package_id);
        node.properties ||= {};
        node.properties.feihou_pack_total = ready.total;
        const index = widget(node, "shot_index");
        if (index) { index.options ||= {}; index.options.max = ready.total; }
        status(node, [ready.errors?.length ? tr("INCOMPLETE — fix the following items before generation.", "检测未通过：制作包不完整，请修复下列问题后重新检测。")
            : tr("PASS — referenced files, prompts and timing are valid.", "检测通过：引用文件、提示词和时间参数完整（不代表已验证媒体解码或生成效果）。"),
            `Shotlist: ${ready.html_file || info.html_file}`, `Audio: ${ready.audio_file || "—"}`,
            tr(`Shots: ${ready.total}`, `分镜总数：${ready.total}`), ...(ready.errors || []), ...(ready.report || [])].join("\n"));
        app.graph?.change?.();
    } catch (error) {
        if (!current()) return;
        status(node, tr("Load failed: ", "载入失败：") + error.message);
        app.extensionManager?.toast?.add?.({ severity: "error", summary: "Easy H3", detail: error.message, life: 12000 });
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
        load_pack: ["Check / refresh production package", "制作包状态检测"],
        upload_zip: ["Upload ZIP", "上传 ZIP 压缩包"],
        preview_shot: ["Preview current shot", "预览当前分镜"],
        pack_report: ["Detection report", "检测信息"],
        generation_order: ["Generation order", "生成顺序控制"],
    };
    for (const w of node.widgets || []) if (names[w.name]) w.label = tr(...names[w.name]);
    const order = widget(node, "generation_order");
    if (order) {
        order.options.values = Object.values(orderNames());
        order.value = orderNames()[value(node, "control_after_generate")] || orderNames().fixed;
    }
    if (node.outputs?.[0]) node.outputs[0].label = tr("Production shot", "制作包分镜");
}

function orderNames() {
    return { increment: tr("Forward", "正序"), decrement: tr("Reverse", "倒序"), randomize: tr("Random", "随机"), fixed: tr("Fixed", "固定") };
}

function hideInternal(w) {
    if (!w) return;
    // Retain original array positions and native serialization for old workflows.
    w.hidden = true;
    w.options ||= {};
    w.options.hidden = true;
    w.type = "hidden";
    w.computeSize = () => [0, -4];
    if (w.inputEl) w.inputEl.style.display = "none";
}

app.registerExtension({
    name: "FeiHou.EasyH3.ProductionPack",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== CLASS) return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = created?.apply(this, arguments);
            // Native ComfyUI batching snapshots this index for each queued prompt.
            // onConfigure restores the user's saved fixed/increment choice later.
            const control = widget(this, "control_after_generate");
            if (control) control.value = "increment";
            for (const name of ["shotlist_file", "audio_file", "package_id", "control_after_generate"]) hideInternal(widget(this, name));
            this.addWidget("combo", "generation_order", orderNames().increment, (label) => {
                const entry = Object.entries(orderNames()).find(([, text]) => text === label);
                if (entry) { set(this, "control_after_generate", entry[0]); app.graph?.change?.(); }
            }, { values: Object.values(orderNames()), serialize: false });
            this.addWidget("button", "upload_zip", null, () => {
                const picker = document.createElement("input");
                picker.type = "file";
                picker.accept = ".zip,application/zip";
                picker.onchange = () => picker.files?.[0] && prepare(this, picker.files[0]);
                picker.click();
            }, { serialize: false });
            const upload = widget(this, "upload_zip");
            this.widgets.splice(this.widgets.indexOf(upload), 1);
            this.widgets.splice(this.widgets.indexOf(widget(this, "source")) + 1, 0, upload);
            this.addWidget("button", "preview_shot", null, () => preview(this).catch((e) => status(this, e.message)), { serialize: false });
            this.addWidget("button", "load_pack", null, () => prepare(this), { serialize: false });
            const report = document.createElement("textarea");
            report.readOnly = true;
            report.setAttribute("aria-label", tr("Detection report", "检测信息"));
            report.style.cssText = "width:100%;height:100%;min-height:72px;min-width:0;box-sizing:border-box;resize:none;overflow:auto;background:#202020;color:#ddd;border:1px solid #555;padding:8px;font:12px/18px monospace;";
            this.__h3PackReport = report;
            this.addDOMWidget("pack_report", "text", report, {
                serialize: false, getValue: () => report.value, setValue: (v) => { report.value = v; },
                getMinHeight: () => 72, getMaxHeight: () => Infinity,
            });
            status(this, tr("Check the package, then preview the current shot. Reverse starts from the selected index.", "先点击制作包状态检测，再预览当前分镜。倒序从所选分镜开始；随机可能重复；固定重复当前分镜。"));
            for (const name of ["source"]) {
                const w = widget(this, name), callback = w?.callback;
                if (w) w.callback = (...args) => {
                    callback?.apply(w, args);
                    nodeSourceEdited(this);
                    set(this, "package_id", "");
                    status(this, tr("Source changed. Load / refresh again.", "来源已改变，请重新载入 / 刷新。"));
                };
            }
            const source = widget(this, "source");
            if (source) source.serializeValue = () => sourcePath(this);
            localize(this);
            this.size[0] = Math.max(410, this.size[0]);
            return result;
        };
        const serialized = nodeType.prototype.onSerialize;
        nodeType.prototype.onSerialize = function (info) {
            const result = serialized?.apply(this, arguments);
            info.widgets_values = SAVED_WIDGETS.map((name) => value(this, name));
            return result;
        };
        const configured = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (info) {
            const result = configured?.apply(this, arguments);
            // Both older workflows and new saves use the same named schema.
            if (Array.isArray(info?.widgets_values)) {
                SAVED_WIDGETS.forEach((name, i) => {
                    if (i < info.widgets_values.length) set(this, name, info.widgets_values[i]);
                });
            }
            for (const name of ["shotlist_file", "audio_file", "package_id", "control_after_generate"]) hideInternal(widget(this, name));
            const index = widget(this, "shot_index");
            if (index && this.properties?.feihou_pack_total > 0) {
                index.options ||= {};
                index.options.max = this.properties.feihou_pack_total;
            }
            localize(this);
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

function nodeSourceEdited(node) {
    node.__h3PackRevision = (node.__h3PackRevision || 0) + 1;
    if (node.properties) delete node.properties.feihou_pack_upload;
    app.graph?.change?.();
}
