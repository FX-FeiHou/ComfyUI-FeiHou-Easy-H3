// Motion Collect: queue the next motion-transfer segment until the last one is done.
// Shot Prompts: drop a markdown shot list on the node (or click its button), and
// show the running shot inside the connected Setup node.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const SHOT_NODE = "FeiHouEasyH3ShotPrompts";
const MARKDOWN_PATTERN = /\.(md|markdown|txt)$/i;
const DROP_LABEL = "选择 md 文件";
const REFRESH_LABEL = "刷新提示词";
const START_RESTART = "从头开始";
const START_MANUAL = "手动设置";

function setups() {
  return (app.graph?._nodes || []).filter((node) => node.type === "FeiHouEasyH3Setup");
}

function promptWidget(node) {
  return (node?.widgets || []).find((widget) => widget?.name === "prompt_text") || null;
}

function markdownButton(node) {
  const picker = node?.__feihouMdPicker;
  if (picker && typeof picker === "object") return picker;
  return (node?.widgets || []).find((widget) => widget?.type === "button") || null;
}

function notify(severity, detail) {
  const toast = app.extensionManager?.toast;
  if (toast?.add) {
    toast.add({ severity, summary: "分镜提示词", detail, life: severity === "error" ? 6000 : 3000 });
  } else if (severity === "error" || severity === "warn") {
    console.warn(`[Easy H3] ${detail}`);
  } else {
    console.info(`[Easy H3] ${detail}`);
  }
}

function setPromptText(node, text) {
  const widget = promptWidget(node);
  if (!widget) return false;
  widget.value = text;
  if (widget._state) widget._state.value = text;
  // The multiline STRING widget is a real DOM textarea in the new frontend.
  const element = widget.element;
  const textarea = element?.tagName === "TEXTAREA" ? element : element?.querySelector?.("textarea");
  if (textarea) textarea.value = text;
  node.setDirtyCanvas?.(true, true);
  app.graph?.setDirtyCanvas?.(true, true);
  app.graph?.change?.();
  return true;
}

function setButtonLabel(node, name) {
  const button = markdownButton(node);
  if (!button) return;
  const short = String(name || "").length > 22 ? `${String(name).slice(0, 19)}…` : String(name || "");
  button.name = short ? `md: ${short}` : DROP_LABEL;
  node.setDirtyCanvas?.(true, true);
}

function pathWidget(node) {
  return (node?.widgets || []).find((widget) => widget?.name === "md_path") || null;
}

function setPathValue(node, path) {
  const widget = pathWidget(node);
  if (!widget) return;
  widget.value = path;
  if (widget._state) widget._state.value = path;
}

function baseName(path) {
  return String(path || "").split(/[\\/]/).pop() || "";
}

// Remembering the md file. A page cannot learn a file's real path, so:
// 1. 「选择 md 文件」 asks the ComfyUI server (this machine) for the native dialog
//    and keeps the full path in md_path, saved with the workflow: refresh works
//    after reloads, ComfyUI restarts and reboots.
// 2. A dropped file (or a ComfyUI opened from another computer) only gives a
//    browser file handle; it is kept in IndexedDB under a key saved in the
//    node's properties, and the browser may ask once to allow reading it again.
const HANDLE_DB = "feihou-easy-h3";
const HANDLE_STORE = "md-handles";

function handleDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(HANDLE_DB, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(HANDLE_STORE);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function handleStore(mode, action) {
  const db = await handleDb();
  try {
    return await new Promise((resolve, reject) => {
      const transaction = db.transaction(HANDLE_STORE, mode);
      const request = action(transaction.objectStore(HANDLE_STORE));
      transaction.oncomplete = () => resolve(request?.result);
      transaction.onerror = () => reject(transaction.error);
    });
  } finally {
    db.close();
  }
}

function handleKey(node) {
  node.properties = node.properties || {};
  if (!node.properties.feihou_md_key) {
    node.properties.feihou_md_key = `md-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  }
  return node.properties.feihou_md_key;
}

async function saveHandle(node, handle) {
  try {
    await handleStore("readwrite", (store) => store.put(handle, handleKey(node)));
  } catch (error) {
    console.warn("[Easy H3] 记住 md 文件失败（浏览器不允许保存文件句柄）：", error);
  }
}

async function savedHandle(node) {
  const key = node?.properties?.feihou_md_key;
  if (!key) return null;
  try {
    return (await handleStore("readonly", (store) => store.get(key))) || null;
  } catch {
    return null;
  }
}

async function readHandle(handle) {
  if (handle.queryPermission && (await handle.queryPermission({ mode: "read" })) !== "granted") {
    // Runs inside the button click, so the browser may show its one-line prompt.
    if ((await handle.requestPermission({ mode: "read" })) !== "granted") {
      throw new Error("没有得到读取这个文件的许可");
    }
  }
  return (await handle.getFile()).text();
}

async function shotMdApi(action, body) {
  const response = await api.fetchApi(`/feihou_easy_h3/shot_md/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-FeiHou-Shot-Md": "1" },
    body: JSON.stringify(body || {}),
  });
  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }
  if (!response.ok || !data.ok) {
    const error = new Error(data.error || `HTTP ${response.status}`);
    // Not local, or a server without this route yet (not restarted): pick in the browser.
    error.remote = data.local === false || data.ok === undefined;
    throw error;
  }
  return data;
}

function applyMarkdown(node, text, name, { path = "", handle = null } = {}) {
  if (!setPromptText(node, text)) {
    console.warn("[Easy H3] 这个节点上没有 prompt_text 输入，md 没有载入。");
    return false;
  }
  node.properties = node.properties || {};
  node.properties.feihou_md_name = name;
  setPathValue(node, path);
  node.__feihouMdSource = handle ? { handle, name } : null;
  if (handle) {
    handleKey(node);
    void saveHandle(node, handle);
  }
  setButtonLabel(node, name);
  console.info(`[Easy H3] 已载入分镜 md：${path || name}（${text.length} 字符）`);
  return true;
}

// handle: a FileSystemFileHandle when the browser gives one (drop or picker in
// Chromium); without it only this snapshot is loaded and nothing is remembered.
async function loadMarkdown(node, file, handle = null) {
  if (!node || !file) return;
  try {
    applyMarkdown(node, await file.text(), file.name, { handle });
  } catch (error) {
    console.warn("[Easy H3] 读取 md 失败：", error);
  }
}

async function refreshMarkdown(node) {
  const path = String(pathWidget(node)?.value || "").trim();
  let text;
  let name;
  try {
    if (path) {
      const data = await shotMdApi("read", { path });
      [text, name] = [data.text, data.name];
      setPathValue(node, data.path);
    } else {
      const handle = node?.__feihouMdSource?.handle || await savedHandle(node);
      if (!handle) {
        notify("info", "还没有选过 md 文件，请先选择一次；之后点「刷新提示词」就会直接重读。");
        await pickMarkdown(node);
        return;
      }
      [text, name] = [await readHandle(handle), handle.name];
      node.__feihouMdSource = { handle, name };
    }
  } catch (error) {
    console.warn("[Easy H3] 重新读取 md 失败：", error);
    notify("error", `刷新失败：${error.message || error}。请重新选择一次 md 文件。`);
    return;
  }
  setButtonLabel(node, name);
  if (promptWidget(node)?.value === text) {
    notify("info", `${name} 没有变化。`);
    return;
  }
  if (!setPromptText(node, text)) return;
  notify("success", `已刷新：${name}（${text.length} 字符）`);
  console.info(`[Easy H3] 已刷新分镜 md：${path || name}（${text.length} 字符）`);
}

async function pickMarkdown(node) {
  try {
    const data = await shotMdApi("pick", { initial: String(pathWidget(node)?.value || "") });
    if (!data.cancelled && applyMarkdown(node, data.text, data.name, { path: data.path })) {
      notify("success", `已载入：${data.path}（${data.text.length} 字符）`);
    }
    return;
  } catch (error) {
    if (!error.remote) {
      notify("error", `选择 md 失败：${error.message || error}`);
      return;
    }
    // ComfyUI opened from another computer: the server dialog would show up on
    // the wrong screen, so pick in the browser instead.
  }
  if (typeof window.showOpenFilePicker === "function") {
    try {
      const [handle] = await window.showOpenFilePicker({
        multiple: false,
        types: [{ description: "分镜 md", accept: { "text/markdown": [".md", ".markdown"], "text/plain": [".txt"] } }],
      });
      if (handle) await loadMarkdown(node, await handle.getFile(), handle);
      return;
    } catch (error) {
      if (error?.name === "AbortError") return;
      console.warn("[Easy H3] 文件选择器不可用，改用普通选择框：", error);
    }
  }
  const input = document.createElement("input");
  input.type = "file";
  input.accept = ".md,.markdown,.txt,text/markdown,text/plain";
  input.style.display = "none";
  const cleanup = () => input.remove();
  input.addEventListener("change", async () => {
    const file = input.files?.[0];
    if (file) await loadMarkdown(node, file);
    cleanup();
  });
  input.addEventListener("cancel", cleanup, { once: true });
  document.body.append(input);
  input.click();
}

function firstMarkdownFile(event) {
  const files = event?.dataTransfer?.files;
  if (!files || !files.length) return null;
  for (const file of files) {
    if (MARKDOWN_PATTERN.test(file.name || "") || /^text\/(markdown|plain)$/.test(file.type || "")) return file;
  }
  return null;
}

function shotNodeUnder(event) {
  const canvas = app.canvas;
  if (!canvas || typeof canvas.convertEventToCanvasOffset !== "function") return null;
  const offset = canvas.convertEventToCanvasOffset(event);
  if (!offset) return null;
  const [x, y] = offset;
  const nodes = app.graph?._nodes || [];
  for (let index = nodes.length - 1; index >= 0; index -= 1) {
    const node = nodes[index];
    if (node?.type !== SHOT_NODE || node.flags?.collapsed) continue;
    const [left, top] = node.pos || [0, 0];
    const [width, height] = node.size || [0, 0];
    if (x >= left && x <= left + width && y >= top && y <= top + height) return node;
  }
  return null;
}

function installDropTarget() {
  if (window.__feihouMdDropInstalled) return;
  window.__feihouMdDropInstalled = true;
  // Capture phase on the document: runs before ComfyUI's own canvas drop logic.
  document.addEventListener("dragover", (event) => {
    if (!firstMarkdownFile(event) || !shotNodeUnder(event)) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
  }, true);
  document.addEventListener("drop", (event) => {
    const node = shotNodeUnder(event);
    const file = firstMarkdownFile(event);
    if (!node || !file) return;
    event.preventDefault();
    event.stopPropagation();
    // getAsFileSystemHandle only works synchronously inside the drop event.
    const item = [...(event.dataTransfer?.items || [])].find((entry) => entry.kind === "file"
      && entry.getAsFile()?.name === file.name);
    const pending = item?.getAsFileSystemHandle?.() || Promise.resolve(null);
    void pending.catch(() => null).then((handle) => loadMarkdown(node, file, handle?.kind === "file" ? handle : null));
  }, true);
}

function installPicker(node) {
  if (!node || node.type !== SHOT_NODE || node.__feihouMdPicker) return;
  node.__feihouMdPicker = node.addWidget("button", DROP_LABEL, null, () => void pickMarkdown(node)) || true;
  const refresh = node.addWidget("button", REFRESH_LABEL, null, () => void refreshMarkdown(node));
  // Not saved with the graph: older workflows keep their widgets_values layout.
  if (refresh) refresh.serialize = false;
  node.__feihouMdRefresh = refresh || true;
  installShotNumberWidget(node);
}

// 镜号 drawn like a number widget: 0 = 全片, arrows step by one, dragging the
// middle changes it, a click opens a prompt (手动设置 takes lists like 6,9 / 3-5).
// The value stays a string, so the Python input and saved workflows are unchanged.
const SHOT_WIDGET_TYPE = "feihou_shot_number";
const SHOT_ARROW_ZONE = 22;
const SHOT_DRAG_PIXELS = 8;

function shotBlockCount(node) {
  const text = String(promptWidget(node)?.value || "");
  return (text.match(/^=====\s*FEIHOU_SHOT\s+\d+\s*=====/gm) || []).length;
}

function firstShotNumber(value) {
  const match = String(value ?? "").match(/\d+/);
  return match ? Number(match[0]) : 0;
}

function shotNumberLabel(value, total) {
  const text = String(value ?? "").trim();
  if (text === "" || /^0+$/.test(text)) return "0（全片）";
  return /^\d+$/.test(text) && total ? `${Number(text)} / ${total}` : text;
}

function setShotNumber(node, widget, value) {
  widget.value = String(value);
  if (widget._state) widget._state.value = widget.value;
  node.setDirtyCanvas?.(true, true);
  app.graph?.change?.();
}

function stepShotNumber(node, widget, number) {
  const total = shotBlockCount(node);
  setShotNumber(node, widget, Math.max(0, Math.min(total || 9999, Math.round(number))));
}

function promptShotNumber(node, widget, event) {
  app.canvas.prompt("镜号（0 = 全片；手动设置可填 6,9 或 3-5）", widget.value, (input) => {
    const text = String(input ?? "").trim();
    if (!/^[\d\s,，;；~-]*$/.test(text)) {
      notify("warn", `镜号只能填数字、6,9 或 3-5，收到「${text}」`);
      return;
    }
    setShotNumber(node, widget, text || "0");
  }, event);
}

function installShotNumberWidget(node) {
  const widgets = node?.widgets || [];
  const index = widgets.findIndex((widget) => widget?.name === "shot_numbers");
  if (index < 0 || widgets[index].type === SHOT_WIDGET_TYPE || typeof node.addCustomWidget !== "function") return;
  const original = widgets[index];
  const drag = { active: false, zone: "", startX: 0, startValue: 0, moved: false };
  const added = node.addCustomWidget({
    name: "shot_numbers",
    type: SHOT_WIDGET_TYPE,
    value: String(original.value ?? "").trim() || "0",
    options: {},
    draw(ctx, owner, width, y, height) {
      const margin = 15;
      const middle = y + height * 0.7;
      ctx.save();
      ctx.fillStyle = LiteGraph.WIDGET_BGCOLOR;
      ctx.strokeStyle = LiteGraph.WIDGET_OUTLINE_COLOR;
      ctx.beginPath();
      ctx.roundRect(margin, y, width - margin * 2, height, [height * 0.5]);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = LiteGraph.WIDGET_TEXT_COLOR;
      ctx.beginPath();
      ctx.moveTo(margin + 16, y + 5);
      ctx.lineTo(margin + 6, y + height * 0.5);
      ctx.lineTo(margin + 16, y + height - 5);
      ctx.fill();
      ctx.beginPath();
      ctx.moveTo(width - margin - 16, y + 5);
      ctx.lineTo(width - margin - 6, y + height * 0.5);
      ctx.lineTo(width - margin - 16, y + height - 5);
      ctx.fill();
      ctx.fillStyle = LiteGraph.WIDGET_SECONDARY_TEXT_COLOR;
      ctx.textAlign = "left";
      ctx.fillText("镜号", margin + SHOT_ARROW_ZONE + 4, middle);
      ctx.fillStyle = LiteGraph.WIDGET_TEXT_COLOR;
      ctx.textAlign = "right";
      ctx.fillText(shotNumberLabel(this.value, shotBlockCount(owner)), width - margin - SHOT_ARROW_ZONE - 4, middle);
      ctx.restore();
    },
    mouse(event, pos, owner) {
      const type = String(event?.type || "");
      const x = pos[0];
      const width = owner.size[0];
      if (type.endsWith("down")) {
        const zone = x < 15 + SHOT_ARROW_ZONE ? "dec" : x > width - 15 - SHOT_ARROW_ZONE ? "inc" : "mid";
        Object.assign(drag, { active: true, zone, startX: x, startValue: firstShotNumber(this.value), moved: false });
        if (zone === "dec") stepShotNumber(owner, this, drag.startValue - 1);
        if (zone === "inc") stepShotNumber(owner, this, drag.startValue + 1);
        return true;
      }
      if (type.endsWith("move")) {
        if (!drag.active || drag.zone !== "mid") return false;
        const steps = Math.trunc((x - drag.startX) / SHOT_DRAG_PIXELS);
        if (steps !== 0 || drag.moved) {
          drag.moved = true;
          stepShotNumber(owner, this, drag.startValue + steps);
        }
        return true;
      }
      if (type.endsWith("up")) {
        const clicked = drag.active && drag.zone === "mid" && !drag.moved;
        drag.active = false;
        if (clicked) promptShotNumber(owner, this, event);
        return true;
      }
      return false;
    },
    computeSize(width) {
      return [width, LiteGraph.NODE_WIDGET_HEIGHT || 20];
    },
  });
  // addCustomWidget appends: put it into the old slot so widgets_values keep their order.
  const widget = node.widgets.includes(added) ? added : node.widgets[node.widgets.length - 1];
  node.widgets.splice(node.widgets.indexOf(widget), 1);
  node.widgets.splice(index, 1, widget);
}

function showShot(info) {
  if (!info || typeof info.prompt !== "string" || !info.prompt) return;
  const targets = setups();
  if (!targets.length) {
    console.warn("[Easy H3] 没有找到 FeiHou Easy H3 Setup，分镜提示词没有回填。");
    return;
  }
  if (targets.length > 1) {
    console.warn("[Easy H3] 有多个 Setup 节点，分镜提示词只回填第一个；请只保留一个 Setup。");
  }
  // The prompt box is a rich editor: it renders from the node's prompt doc, so
  // it has to go through the Easy H3 UI hook instead of setting the widget value.
  window.dispatchEvent(new CustomEvent("feihou-h3-set-prompt", {
    detail: { nodeId: targets[0].id, text: info.prompt },
  }));
  // Show the block's real duration on Setup: block mode always takes the length
  // from the payload, so the widget is display-only and should not stay stale.
  if (typeof info.seconds === "number" && Number.isFinite(info.seconds)) {
    const seconds = targets[0].widgets?.find((widget) => widget?.name === "seconds");
    if (seconds) {
      const value = Math.round(info.seconds * 100) / 100;
      seconds.value = value;
      if (seconds._state) seconds._state.value = value;
      targets[0].setDirtyCanvas?.(true, true);
    }
  }
  // The 镜号 widget is NOT advanced here: this node runs at the start of every
  // segment, so the block is not finished yet (see setShotProgress).
  const range = info.range ? `（${info.range}）` : "";
  console.info(`[Easy H3] 分镜 ${info.index}/${info.total}${range} 已回填到 Setup 的提示词框`);
}

// 镜号 = the block the next run starts from. Motion Collect reports it once a
// block is really finished; 0 after the last block makes the next click start
// the film over.
function setShotProgress(info) {
  if (!info || !Number.isFinite(Number(info.block))) return;
  const shotNode = (app.graph?._nodes || []).find((node) => node?.type === SHOT_NODE);
  const numberWidget = shotNode?.widgets?.find((widget) => widget?.name === "shot_numbers");
  if (!numberWidget) return;
  // 手动设置: 镜号 is the user's pick list, not a progress display. The server
  // state remembers which pick runs next, so the list stays as typed.
  const startWidget = shotNode.widgets.find((widget) => widget?.name === "start");
  if (startWidget?.value === START_MANUAL) return;
  let next;
  if (!("shot_done" in info)) {
    // A segment inside the block finished: the next run continues this block,
    // so 镜号 must name it (a stale value would jump elsewhere).
    next = String(Math.round(Number(info.block)));
  } else if (info.shot_done) {
    next = "0";  // the film is finished: back to 全片
  } else {
    next = Number.isFinite(Number(info.shot_next)) && Number(info.shot_next) > 0
      ? String(Math.round(Number(info.shot_next))) : numberWidget.value;
  }
  numberWidget.value = next;
  if (numberWidget._state) numberWidget._state.value = next;
  shotNode.setDirtyCanvas?.(true, true);
}

// Motion Segment saved its widgets in this order before layout 2 (each
// version appended to the end). Layout 2 puts first_steps under steps and the
// frame-window fields under segmentation; older saves are restored by name.
const SEGMENT_NODE = "FeiHouEasyH3MotionSegment";
// Layout 3 also saves every widget by name (properties.feihou_segment_widgets),
// so later changes to the field list no longer shift saved values.
const SEGMENT_LAYOUT = 3;
// Layout 2 (positional), saved between the reorder and the removal of max_segments.
const SEGMENT_LAYOUT2_WIDGETS = [
  "sampler_name", "scheduler", "steps", "first_steps", "shift_video", "shift_audio", "cfg", "reference_soundtrack",
  "sampling_mode", "segmentation", "frame_window", "segment_frames", "overlap_frames", "max_segments",
  "context_window_frames", "context_overlap_frames",
];
const SEGMENT_LEGACY_WIDGETS = [
  "sampler_name", "scheduler", "steps", "shift_video", "shift_audio", "cfg", "seed", "control_after_generate",
  "segment_frames", "overlap_frames", "max_segments", "reference_soundtrack", "sampling_mode", "first_steps",
  "segmentation", "context_window_frames", "context_overlap_frames",
];

// segment_frames used to be a number: show it as the option with seconds.
function labelSegmentFrames(node) {
  const widget = node.widgets?.find((item) => item?.name === "segment_frames");
  const options = widget?.options?.values;
  if (!widget || !Array.isArray(options)) return;
  const frames = Number(String(widget.value).split(" ")[0]);
  if (!Number.isFinite(frames) || options.includes(widget.value)) return;
  const grid = Math.max(5, frames) - ((Math.max(5, frames) - 5) % 17);
  const match = options.find((option) => Number(String(option).split(" ")[0]) === grid);
  if (!match) return;
  widget.value = match;
  if (widget._state) widget._state.value = match;
}

// 帧窗口 off: segment_frames and overlap_frames do nothing, show them greyed out.
const FRAME_WINDOW_FIELDS = ["segment_frames", "overlap_frames"];

function syncFrameWindowFields(node) {
  const toggle = node.widgets?.find((item) => item?.name === "frame_window");
  if (!toggle) return;
  const off = toggle.value === false;
  for (const widget of node.widgets || []) {
    if (!FRAME_WINDOW_FIELDS.includes(widget?.name)) continue;
    widget.disabled = off;
    widget.options ||= {};
    widget.options.disabled = off;
  }
  node.setDirtyCanvas?.(true, true);
}

function watchFrameWindow(node) {
  const toggle = node.widgets?.find((item) => item?.name === "frame_window");
  if (!toggle || toggle.__h3FrameWindowWatched) return;
  toggle.__h3FrameWindowWatched = true;
  const callback = toggle.callback;
  toggle.callback = function () {
    const result = callback?.apply(this, arguments);
    syncFrameWindowFields(node);
    return result;
  };
  syncFrameWindowFields(node);
}

function setSegmentWidget(node, name, value) {
  if (value === null || value === undefined) return;
  const widget = node.widgets?.find((item) => item?.name === name);
  if (!widget) return;
  // A value from another layout never lands on the wrong kind of field: a
  // switch takes only true/false, an option list only its own options (the
  // old numeric segment_frames is relabelled afterwards).
  if (typeof widget.value === "boolean" && typeof value !== "boolean") return;
  const options = widget.options?.values;
  if (Array.isArray(options) && !options.includes(value) && !(name === "segment_frames" && Number.isFinite(Number(value)))) return;
  widget.value = value;
  if (widget._state) widget._state.value = value;
}

function restoreSegmentByPosition(node, names, values) {
  if (!Array.isArray(values)) return;
  names.forEach((name, index) => { if (index < values.length) setSegmentWidget(node, name, values[index]); });
}

function segmentRestorer(node, info) {
  const named = info?.properties?.feihou_segment_widgets;
  if (named && typeof named === "object") {
    return () => Object.entries(named).forEach(([name, value]) => setSegmentWidget(node, name, value));
  }
  const layout = Number(info?.properties?.feihou_segment_layout);
  const names = layout === 2 ? SEGMENT_LAYOUT2_WIDGETS : layout >= SEGMENT_LAYOUT ? null : SEGMENT_LEGACY_WIDGETS;
  return names ? () => restoreSegmentByPosition(node, names, info?.widgets_values) : null;
}

// Context windows are switched off for now: hide their fields and keep 帧窗口.
const SEGMENT_HIDDEN_WIDGETS = ["segmentation", "context_window_frames", "context_overlap_frames"];

function hideSegmentContextWidgets(node) {
  for (const widget of node.widgets || []) {
    if (!SEGMENT_HIDDEN_WIDGETS.includes(widget?.name)) continue;
    if (widget.name === "segmentation" && widget.value !== "帧窗口") {
      widget.value = "帧窗口";
      if (widget._state) widget._state.value = widget.value;
    }
    widget.hidden = true;
    widget.options ||= {};
    widget.options.hidden = true;
    widget.type = "hidden";
    widget.computeSize = () => [0, -4];
    if (widget.inputEl) widget.inputEl.style.display = "none";
  }
  const size = node.computeSize?.();
  if (size) node.setSize?.([node.size?.[0] || size[0], size[1]]);
}

function installSegmentLayout(nodeType) {
  const created = nodeType.prototype.onNodeCreated;
  nodeType.prototype.onNodeCreated = function () {
    const result = created?.apply(this, arguments);
    this.properties ||= {};
    this.properties.feihou_segment_layout = SEGMENT_LAYOUT;
    hideSegmentContextWidgets(this);
    watchFrameWindow(this);
    return result;
  };
  const configured = nodeType.prototype.onConfigure;
  nodeType.prototype.onConfigure = function (info) {
    const result = configured?.apply(this, arguments);
    const restore = segmentRestorer(this, info);
    if (restore) {
      restore();
      // Some frontends re-apply widgets_values by position after onConfigure.
      setTimeout(() => { restore(); syncFrameWindowFields(this); this.setDirtyCanvas?.(true, true); }, 0);
    }
    this.properties ||= {};
    this.properties.feihou_segment_layout = SEGMENT_LAYOUT;
    hideSegmentContextWidgets(this);
    labelSegmentFrames(this);
    watchFrameWindow(this);
    setTimeout(() => { hideSegmentContextWidgets(this); labelSegmentFrames(this); syncFrameWindowFields(this); }, 0);
    return result;
  };
  const serialized = nodeType.prototype.onSerialize;
  nodeType.prototype.onSerialize = function (info) {
    const result = serialized?.apply(this, arguments);
    info.properties ||= {};
    info.properties.feihou_segment_layout = SEGMENT_LAYOUT;
    info.properties.feihou_segment_widgets = Object.fromEntries(
      (this.widgets || []).filter((widget) => widget?.name).map((widget) => [widget.name, widget.value]));
    return result;
  };
}

app.registerExtension({
  name: "FeiHou.EasyH3.MotionTransfer",
  setup() {
    installDropTarget();
    api.addEventListener("executed", (event) => {
      const output = event?.detail?.output || {};
      if (output.feihou_shot?.[0]) showShot(output.feihou_shot[0]);
      const info = output.feihou_motion?.[0];
      setShotProgress(info);
      if (!info?.next) return;
      // The next block is queued from this same graph: switch a "从头开始" batch to
      // 自动续接, otherwise it would restart at block 1 forever. 手动设置 stays as
      // it is: the server resumes the same pick list from its state file.
      const shot = (app.graph?._nodes || []).find((node) => node?.type === "FeiHouEasyH3ShotPrompts");
      const startWidget = shot?.widgets?.find((widget) => widget?.name === "start");
      if (startWidget && startWidget.value === START_RESTART) {
        startWidget.value = "自动续接";
        if (startWidget._state) startWidget._state.value = startWidget.value;
        shot.setDirtyCanvas?.(true, true);
        console.info("[Easy H3] 自动续跑下一块：start 已切到「自动续接」");
      }
      const where = info.blocks ? `第 ${info.block}/${info.blocks} 个分镜，第 ${info.done}/${info.count} 段` : `第 ${info.done}/${info.count} 段`;
      console.info(`[Easy H3] 动作迁移：${where}完成，排队下一段`);
      setTimeout(() => app.queuePrompt(0, 1), 0);
    });
  },
  beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name === SEGMENT_NODE) {
      installSegmentLayout(nodeType);
      return;
    }
    if (nodeData?.name !== SHOT_NODE) return;
    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const result = created?.apply(this, arguments);
      installPicker(this);
      return result;
    };
    const configured = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const result = configured?.apply(this, arguments);
      installPicker(this);
      // The button label is not saved: show the remembered file again.
      const remembered = baseName(pathWidget(this)?.value) || this.properties?.feihou_md_name;
      if (remembered) setButtonLabel(this, remembered);
      return result;
    };
  },
});
