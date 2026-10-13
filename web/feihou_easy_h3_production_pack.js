import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { renderShotlist } from "./production_pack_parser.js";
import { applyProductionShotPreview } from "./feihou_easy_h3_ui.js";

const CLASS = "FeiHouEasyH3ProductionPackLoader";
// Original serialized positions: presentation-only buttons must never shift them.
const SAVED_WIDGETS = ["source", "shotlist_file", "audio_file", "shot_index", "control_after_generate", "prompt_language", "package_id", "continuous_runtime_config", "continuation", "context_frames", "continue_video", "use_pack"];
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

async function uploadContinueFile(file, batch) {
    const response = await api.fetchApi("/feihou_easy_h3/production_pack/continue_upload", {
        method: "POST",
        credentials: "same-origin",
        headers: {
            "X-FeiHou-Pack": "1", "Content-Type": "application/octet-stream",
            "X-FeiHou-Name": encodeURIComponent(file.name), "X-FeiHou-Batch": batch,
        },
        body: file,
    });
    const result = await response.json();
    if (!response.ok || result.error) throw new Error(result.error || `HTTP ${response.status}`);
    return result;
}

const AUTO = "自动";
const baseName = (path) => String(path || "").split(/[\\/]/).pop();

// The visible "接续视频" combo: 自动 plus the last chosen file (shown by its name).
// The real value lives in the hidden continue_video string the backend reads.
function syncContinue(node) {
    const shown = widget(node, "continue_video_choice");
    if (!shown) return;
    const current = value(node, "continue_video") || AUTO;
    node.properties ||= {};
    if (current !== AUTO) node.properties.feihou_continue_selected = current;
    const selected = node.properties.feihou_continue_selected;
    shown.options ||= {};
    shown.options.values = selected ? [AUTO, baseName(selected)] : [AUTO];
    shown.value = current === AUTO ? AUTO : baseName(current);
    node.setDirtyCanvas?.(true, true);
}

// Windows file dialog for the continuation video. A video already inside ComfyUI's
// output/input folder (same name and size) is used in place, so its saved .latent is
// found next to it; any other video (plus a .latent picked with it) is uploaded to input.
function pickContinueVideo(node) {
    const picker = document.createElement("input");
    picker.type = "file";
    picker.multiple = true;
    picker.accept = "video/*,.mp4,.webm,.mov,.mkv,.m4v,.latent";
    picker.onchange = async () => {
        const files = [...(picker.files || [])];
        if (!files.length) return;
        const video = files.find((f) => !/\.latent$/i.test(f.name)) || files[0];
        const stem = (name) => name.replace(/\.[^.]+$/, "");
        try {
            status(node, tr("Locating the continuation video…", "正在定位接续视频…"));
            let chosen = (await request("continue_find", { name: video.name, size: video.size })).value;
            if (!chosen) {
                const batch = [...crypto.getRandomValues(new Uint8Array(6))].map((b) => b.toString(16).padStart(2, "0")).join("");
                for (const file of files) {
                    if (file !== video && !(/\.latent$/i.test(file.name) && stem(file.name) === stem(video.name))) continue;
                    status(node, tr(`Uploading ${file.name}…`, `正在上传 ${file.name}…`));
                    const result = await uploadContinueFile(file, batch);
                    if (file === video) chosen = result.value;
                }
            }
            set(node, "continue_video", chosen);
            syncContinue(node);
            status(node, tr(`Continuation video: ${chosen}\nThe first queued shot continues from it, then this returns to Auto.`,
                `接续视频：${chosen}\n本次排队的第一个分镜会接着它生成，之后自动改回「自动」。`));
            app.graph?.change?.();
            node.setDirtyCanvas?.(true, true);
        } catch (error) {
            status(node, tr("Continuation video failed: ", "接续视频选择失败：") + error.message);
        }
    };
    picker.click();
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
                if (["FeiHouEasyH3", "FeiHouEasyH3Setup"].includes(target.comfyClass || target.type)
                    && target.inputs?.[link.target_slot]?.name === "production_shot") {
                    applyProductionShotPreview(target, shot);
                    applied++;
                } else if (/reroute/i.test(target.type || "")) visit(target);
                else if ((target.type || "") === "SetNode") {
                    // Follow KJNodes Set/Get pairs as well.
                    const name = target.widgets?.[0]?.value;
                    for (const getter of graph?._nodes || []) {
                        if ((getter.type || "") === "GetNode" && getter.widgets?.[0]?.value === name) visit(getter);
                    }
                }
            }
        }
    };
    visit(node);
    const join = { continue: tr("continue", "接续"), cut: tr("hard cut", "硬切") }[shot.transition] || tr("unspecified", "未指定");
    status(node, `${shot.index}/${shot.total} · ${shot.id} · ${shot.params.seconds}s · ${tr("transition", "衔接")}: ${join}`
        + `${shot.continuation_frames ? " " + shot.continuation_frames : ""}\n${shot.range}\n${shot.refs.join(" → ")}\n`
        + shot.media.filter((m) => m.media_type === "audio").map((m) => `Audio ${m.ordinal}: ${m.filename} | ${m.audio_trim || "full"}\n`).join("")
        + (shot.media.filter((m) => m.media_type === "audio").length > 3
            ? tr("The gallery shows 3 audio slots; all package audio is supplied through the connected production-shot input.\n", "上传区仅显示 3 个音频格；全部制作包音频通过已连接的制作包分镜接口参与执行。\n") : "")
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
        continuation: ["Shot transition", "分镜衔接"],
        context_frames: ["Continuation frames (1st+2nd pass)", "接续帧数（一采+二采）"],
        continue_video: ["Continue from video", "接续视频"],
        pick_continue_video: ["Choose continuation video…", "选择接续视频…"],
        continue_video_choice: ["Continue from video", "接续视频"],
        use_pack: ["Run production pack in batch", "批量运行制作包"],
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
            // With the pack switched off the loader only continues videos: keep the shot index still.
            for (const hook of ["beforeQueued", "afterQueued"]) {
                const original = control?.[hook];
                if (typeof original === "function") {
                    control[hook] = (...args) => (value(this, "use_pack") === false ? undefined : original.apply(control, args));
                }
            }
            // A chosen continuation video applies to the first queued shot only.
            const chosen = widget(this, "continue_video");
            if (chosen) {
                const after = chosen.afterQueued;
                chosen.afterQueued = (...args) => {
                    after?.apply(chosen, args);
                    if (chosen.value !== AUTO) { set(this, "continue_video", AUTO); syncContinue(this); }
                };
            }
            for (const name of ["shotlist_file", "audio_file", "package_id", "control_after_generate", "continuous_runtime_config", "continue_video"]) hideInternal(widget(this, name));
            this.addWidget("combo", "generation_order", orderNames().increment, (label) => {
                const entry = Object.entries(orderNames()).find(([, text]) => text === label);
                if (entry) { set(this, "control_after_generate", entry[0]); app.graph?.change?.(); }
            }, { values: Object.values(orderNames()), serialize: false });
            const usePack = widget(this, "use_pack");
            if (usePack) {
                this.widgets.splice(this.widgets.indexOf(usePack), 1);
                this.widgets.unshift(usePack);
                const toggled = usePack.callback;
                usePack.callback = (...args) => {
                    toggled?.apply(usePack, args);
                    status(this, usePack.value
                        ? tr("Batch production pack ON: shots run from the package.", "批量运行制作包：开，按制作包内容批量运行。")
                        : tr("Batch production pack OFF: Setup uses its own prompt and media; choose a video below to continue from it.",
                            "批量运行制作包：关，Setup 使用自己的提示词和素材，只作为接续加载器；在下面选择要接续的视频。"
                            + "没选视频时，分镜衔接为「接续」会中止任务，「自动」/「硬切」正常生成（硬切）。"));
                };
            }
            const order = widget(this, "generation_order"), language = widget(this, "prompt_language");
            if (order && language) {
                this.widgets.splice(this.widgets.indexOf(order), 1);
                this.widgets.splice(this.widgets.indexOf(language) + 1, 0, order);
            }
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
            this.addWidget("combo", "continue_video_choice", AUTO, (label) => {
                const selected = this.properties?.feihou_continue_selected;
                set(this, "continue_video", label !== AUTO && selected && label === baseName(selected) ? selected : AUTO);
                syncContinue(this);
                app.graph?.change?.();
            }, { values: [AUTO], serialize: false });
            this.addWidget("button", "pick_continue_video", null, () => pickContinueVideo(this), { serialize: false });
            for (const name of ["pick_continue_video", "continue_video_choice"]) {
                const button = widget(this, name), anchor = widget(this, "continue_video");
                if (button && anchor) {
                    this.widgets.splice(this.widgets.indexOf(button), 1);
                    this.widgets.splice(this.widgets.indexOf(anchor) + 1, 0, button);
                }
            }
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
            const restore = () => {
                if (!Array.isArray(info?.widgets_values)) return;
                SAVED_WIDGETS.forEach((name, i) => {
                    let saved = info.widgets_values[i];
                    // Older saves stored the continuation switch as true/false.
                    if (name === "continuation" && typeof saved === "boolean") saved = saved ? "自动" : "硬切";
                    if (i < info.widgets_values.length && saved !== null && saved !== undefined) set(this, name, saved);
                });
            };
            restore();
            syncContinue(this);
            // Some frontends re-apply widgets_values by position after onConfigure,
            // which would put the hidden slots (e.g. the package ID) on the wrong widgets.
            setTimeout(() => { restore(); syncContinue(this); }, 0);
            for (const name of ["shotlist_file", "audio_file", "package_id", "control_after_generate", "continuous_runtime_config", "continue_video"]) hideInternal(widget(this, name));
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
                if (["FeiHouEasyH3", "FeiHouEasyH3Setup"].includes(node.comfyClass || node.type)) {
                    const input = node.inputs?.find((s) => s.name === "production_shot");
                    if (input) input.label = tr("Production shot", "制作包分镜");
                }
            }
        };
        api.addEventListener?.("graphConfigured", refresh);
        app.ui?.settings?.addEventListener?.("change", refresh);
    },
    nodeCreated(node) {
        if (["FeiHouEasyH3", "FeiHouEasyH3Setup"].includes(node.comfyClass || node.type)) {
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
