// Motion Collect: queue the next motion-transfer segment until the last one is done.
// Shot Prompts: drop a markdown shot list on the node (or click its button), and
// show the running shot inside the connected Setup node.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const SHOT_NODE = "FeiHouEasyH3ShotPrompts";
const MARKDOWN_PATTERN = /\.(md|markdown|txt)$/i;
const DROP_LABEL = "选择 md 文件";
const REFRESH_LABEL = "刷新提示词";
const START_SEQUENCE = "顺序生成";
const START_MANUAL = "手动设置";
// Saved before 顺序生成 (both became it).
const LEGACY_STARTS = { "自动续接": START_SEQUENCE, "从头开始": START_SEQUENCE };

function setups() {
  return (app.graph?._nodes || []).filter((node) => node.type === "FeiHouEasyH3Setup");
}

// @aspect of a shot md (header, else the first block that writes one): the same
// spellings the backend reads (9:16, 9x16, 9比16; aspect / aspect_ratio / 画幅 / 画幅比例 / 比例).
const MD_ASPECTS = ["1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9"];
const MD_ASPECT_LINE = /^\s*@(?:aspect_ratio|aspect|画幅比例|画幅|比例)(?:\s+|\s*[:：=]\s*)(.+?)\s*$/i;

function mdAspect(text) {
  let header = "";
  let first = "";
  let inBlock = false;
  for (const line of String(text || "").split(/\r?\n/)) {
    if (/^\s*=+\s*FEIHOU_SHOT\s+\d+\s*=+\s*$/i.test(line)) {
      inBlock = true;
      continue;
    }
    const match = line.match(MD_ASPECT_LINE);
    if (!match) continue;
    const value = match[1].replace(/\s+/g, "").replace(/[：xX比]/g, ":");
    if (!MD_ASPECTS.includes(value)) continue;
    if (!inBlock && !header) header = value;
    if (inBlock && !first) first = value;
  }
  return header || first;
}

// Put the md's aspect ratio on Setup's 画幅比例, so the node shows what the run
// will use (the backend applies @aspect anyway).  Returns the value it set.
function showSetupAspect(aspect, { quiet = false } = {}) {
  if (!MD_ASPECTS.includes(aspect)) return "";
  let changed = "";
  for (const setup of setups()) {
    const widget = setup.widgets?.find((item) => item?.name === "aspect_ratio");
    if (!widget || widget.value === aspect) continue;
    const options = widget.options?.values;
    if (Array.isArray(options) && !options.includes(aspect)) continue;
    const before = widget.value;
    widget.value = aspect;
    if (widget._state) widget._state.value = aspect;
    try {
      widget.callback?.(aspect, app.canvas, setup);
    } catch (error) {
      console.warn("[Easy H3] 更新 Setup 画幅比例时出错：", error);
    }
    setup.setDirtyCanvas?.(true, true);
    changed = aspect;
    console.info(`[Easy H3] Setup 画幅比例按分镜 md 的 @aspect 从 ${before} 改成 ${aspect}`);
  }
  if (changed && !quiet) notify("info", `Setup 的画幅比例已按 md 的 @aspect 改成 ${changed}`);
  return changed;
}

// The 分镜提示词 node a run belongs to.  A graph may keep a second, bypassed
// copy (another md): only the node that ran, else the first active one, counts.
function activeShotNode(nodeId) {
  const owner = nodeId == null ? null : app.graph?.getNodeById?.(nodeId);
  if (owner?.type === SHOT_NODE) return owner;
  const shots = (app.graph?._nodes || []).filter((node) => node?.type === SHOT_NODE);
  return shots.find((node) => !node.mode) || shots[0] || null;
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
// 2. A dropped file only gives its name, size, time and content: the server
//    checks md_path's folder and the folders md files were picked from before
//    (no disk search), and md_path gets the full path when it is there.
// 3. When it is not found (or ComfyUI is opened from another computer) the
//    browser file handle is kept in IndexedDB under a key saved in the node's
//    properties, and the browser may ask once to allow reading it again.
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
  showSetupAspect(mdAspect(text));
  console.info(`[Easy H3] 已载入分镜 md：${path || name}（${text.length} 字符）`);
  return true;
}

async function sha256Hex(file) {
  if (!globalThis.crypto?.subtle) return "";   // plain http on a LAN address: name, size and time only
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

// The full path of a dropped file when it sits in md_path's folder or a folder
// md files were picked from before ("" otherwise; nothing is searched).
async function locateMarkdown(node, file, hint) {
  const body = { name: file.name, size: file.size, mtime: file.lastModified, sha256: await sha256Hex(file), hint };
  try {
    return (await shotMdApi("locate", body)).path || "";
  } catch (error) {
    if (!error.remote) console.warn("[Easy H3] 查找拖入 md 的路径失败：", error);
    return "";
  }
}

// handle: a FileSystemFileHandle when the browser gives one (drop or picker in
// Chromium).  The server then looks for the file's path for md_path.
async function loadMarkdown(node, file, handle = null) {
  if (!node || !file) return;
  const hint = String(pathWidget(node)?.value || "");
  try {
    if (!applyMarkdown(node, await file.text(), file.name, { handle })) return;
  } catch (error) {
    console.warn("[Easy H3] 读取 md 失败：", error);
    return;
  }
  const path = await locateMarkdown(node, file, hint);
  if (path) {
    setPathValue(node, path);
    app.graph?.change?.();
    notify("success", `已载入并记住路径：${path}（改了 md 后点「刷新提示词」即可）`);
    console.info(`[Easy H3] 拖入的 md 路径：${path}`);
  } else {
    notify(handle ? "info" : "warn", `已载入 ${file.name}。浏览器不告诉网页拖入文件的路径，md_path 没有填`
      + (handle ? "（这个浏览器记住了文件，「刷新提示词」仍能重读）。" : "。")
      + "想记住路径：用「选择 md 文件」选一次；以后从同一个文件夹拖入的 md 会自动填好路径。");
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
  showSetupAspect(mdAspect(text));
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

// During dragover the browser hides file names (dataTransfer.files is empty);
// only each item's kind and MIME type are readable.  A .md file is usually
// text/markdown, or "" on Windows; images, videos and workflows are not taken.
function mayBeMarkdownDrag(event) {
  const items = event?.dataTransfer?.items;
  if (!items || !items.length) return false;
  return [...items].some((item) => item.kind === "file"
    && (item.type === "" || /^text\/(markdown|x-markdown|plain)$/.test(item.type)));
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
  // Accepting the dragover here also lets a drop land on the node's prompt
  // box (a textarea refuses file drags by default, so no drop event came).
  document.addEventListener("dragover", (event) => {
    if (!mayBeMarkdownDrag(event) || !shotNodeUnder(event)) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
  }, true);
  document.addEventListener("drop", (event) => {
    const node = shotNodeUnder(event);
    const file = firstMarkdownFile(event);
    if (node && !file && event.dataTransfer?.files?.length && /^(TEXTAREA|INPUT)$/.test(event.target?.tagName || "")) {
      // A non-md file let through by the dragover above: don't let the browser open it.
      event.preventDefault();
      return;
    }
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

// 顺序生成: 镜号 0 (or empty) = run the whole film again from block 1. Once the
// run is going, 镜号 follows the progress like any number.
function isZeroShotNumber(value) {
  const text = String(value ?? "").trim();
  return text === "" || /^0+$/.test(text);
}

function shotNumberLabel(value, total, node) {
  const text = String(value ?? "").trim();
  if (isZeroShotNumber(text)) return startValue(node) === START_MANUAL ? "0（全片）" : "0（全片重跑）";
  const queued = text.match(/^0@(\d+)$/);   // saved by an older version: continue at that block
  const number = queued ? queued[1] : text;
  return /^\d+$/.test(number) && total ? `${Number(number)}/${total}` : text;
}

function startValue(node) {
  const value = node?.widgets?.find((widget) => widget?.name === "start")?.value;
  return LEGACY_STARTS[value] || value;
}

// Older workflows: 自动续接 / 从头开始 become 顺序生成.
function migrateStart(node) {
  const widget = node?.widgets?.find((item) => item?.name === "start");
  if (widget && LEGACY_STARTS[widget.value]) {
    widget.value = LEGACY_STARTS[widget.value];
    if (widget._state) widget._state.value = widget.value;
  }
}

// The user changed 镜号 (arrows, drag, typing): the run starts at that block
// afresh, even when it is the block an interrupted run stopped in. Sent as "N!"
// (see serializeShotNumber); progress updates clear the mark.
function setShotNumber(node, widget, value) {
  widget.__h3Edited = true;
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
  app.canvas.prompt("镜号（顺序生成：不改 = 接着跑，0 或 1 = 全片重跑，其他块号 = 从那块开始往下跑；手动设置可填 6,9 或 3-5）",
    isZeroShotNumber(widget.value) ? "0" : widget.value, (input) => {
    const text = String(input ?? "").trim();
    if (!/^[\d\s,，;；~-]*$/.test(text)) {
      notify("warn", `镜号只能填数字、6,9 或 3-5，收到「${text}」`);
      return;
    }
    setShotNumber(node, widget, text || "0");
  }, event);
}

// What the run gets: "N!" when the user changed 镜号 in 顺序生成, else the value.
function serializeShotNumber(node, widget) {
  const text = String(widget.value ?? "").trim();
  return widget.__h3Edited && startValue(node) !== START_MANUAL && /^\d+$/.test(text) ? `${text}!` : widget.value;
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
      ctx.fillText(shotNumberLabel(this.value, shotBlockCount(owner), owner), width - margin - SHOT_ARROW_ZONE - 4, middle);
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
    serializeValue(owner) {
      return serializeShotNumber(owner || node, this);
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
  // The block's @aspect (the backend already used it for this run).
  if (info.aspect) showSetupAspect(String(info.aspect), { quiet: true });
  // The 镜号 widget is NOT advanced here: this node runs at the start of every
  // segment, so the block is not finished yet (see setShotProgress).
  const range = info.range ? `（${info.range}）` : "";
  console.info(`[Easy H3] 分镜 ${info.index}/${info.total}${range} 已回填到 Setup 的提示词框`);
}

// 镜号 = the block the next run starts from. Motion Collect reports it once a
// block is really finished; 0 after the last block makes the next click start
// the film over.
function setShotProgress(info, nodeId) {
  if (!info || !Number.isFinite(Number(info.block))) return;
  const shotNode = activeShotNode(nodeId);
  const numberWidget = shotNode?.widgets?.find((widget) => widget?.name === "shot_numbers");
  if (!numberWidget) return;
  // 手动设置: 镜号 is the user's pick list, not a progress display. The server
  // state remembers which pick runs next, so the list stays as typed.
  const startWidget = shotNode.widgets.find((widget) => widget?.name === "start");
  if (startWidget?.value === START_MANUAL) return;
  // 顺序生成 (also after a 0 = 全片重跑 start): 镜号 follows the film, so an
  // interrupted run queued again unchanged continues where it stopped.
  let next;
  if (!("shot_done" in info)) {
    // A segment inside the block finished: the next run continues this block,
    // so 镜号 must name it (a stale value would jump elsewhere).
    next = String(Math.round(Number(info.block)));
  } else if (info.shot_done) {
    next = "1";  // the film is finished: the next run makes it again from block 1
  } else {
    next = Number.isFinite(Number(info.shot_next)) && Number(info.shot_next) > 0
      ? String(Math.round(Number(info.shot_next))) : numberWidget.value;
  }
  numberWidget.value = next;
  numberWidget.__h3Edited = false;
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

// 分段方式 picks what segment_frames / overlap_frames mean: segment length and
// tail overlap (帧窗口 / 不分段) or window length and window overlap (上下文窗口).
// Each mode shows only its own options and remembers its own values
// (properties.feihou_segment_modes), so switching back restores them; a mode
// never set before starts from its recommended values.
const SEG_AUTO = "自动";
const SEG_WHOLE = "不分段";
const SEG_CHAIN = "帧窗口";
const SEG_CONTEXT = "上下文窗口";
const SEGMENT_MODE_FIELDS = ["segment_frames", "overlap_frames"];
// 自动 follows the md (@segment); like 不分段 it greys out every field below.
const SEGMENT_MODES = {
  [SEG_AUTO]: { frames: [39, 497], overlap: ["22", "39", "5"], segment_frames: 124, overlap_frames: "22" },
  [SEG_WHOLE]: { frames: [39, 497], overlap: ["22", "39", "5"], segment_frames: 124, overlap_frames: "22" },
  [SEG_CHAIN]: { frames: [39, 497], overlap: ["22", "39", "5"], segment_frames: 124, overlap_frames: "22" },
  [SEG_CONTEXT]: { frames: [124, 362], overlap: ["22", "39", "56", "73", "90", "107"], segment_frames: 345, overlap_frames: "73" },
};
// Window overlaps are 5 + 17k frames; saves from before 2026-11-13 wrote 34 / 68 / 102 for 39 / 73 / 107.
const LEGACY_CONTEXT_OVERLAPS = { "34": "39", "68": "73", "102": "107" };

const frameCount = (value) => Number(String(value).split(" ")[0]);

function segmentWidget(node, name) {
  return node.widgets?.find((item) => item?.name === name);
}

function setWidgetValue(widget, value) {
  widget.value = value;
  if (widget._state) widget._state.value = value;
}

// The backend lists every option of both modes; keep that full list once.
function allOptions(widget) {
  if (!widget.__h3AllOptions) widget.__h3AllOptions = [...(widget.options?.values || [])];
  return widget.__h3AllOptions;
}

function segmentModeOf(value) {
  const mode = value === "接续" ? SEG_CHAIN : value;
  return SEGMENT_MODES[mode] ? mode : SEG_CHAIN;
}

function modeOptions(widget, name, mode) {
  const spec = SEGMENT_MODES[mode];
  return allOptions(widget).filter((option) => {
    if (name === "overlap_frames") return spec.overlap.includes(String(option));
    const frames = frameCount(option);
    return frames >= spec.frames[0] && frames <= spec.frames[1];
  });
}

// An option of this mode for a saved value: the same option, or a bare frame
// count (segment_frames used to be a number) aligned down to 5+17N.
function matchOption(options, name, value) {
  if (value === null || value === undefined) return undefined;
  if (options.includes(value)) return value;
  if (name === "overlap_frames") {
    const legacy = LEGACY_CONTEXT_OVERLAPS[String(value)];
    return options.find((option) => String(option) === String(value))
      ?? (legacy === undefined ? undefined : options.find((option) => String(option) === legacy));
  }
  const frames = frameCount(value);
  if (!Number.isFinite(frames)) return undefined;
  const grid = Math.max(5, frames) - ((Math.max(5, frames) - 5) % 17);
  return options.find((option) => frameCount(option) === grid);
}

function recommendedOption(options, name, mode) {
  const want = SEGMENT_MODES[mode][name];
  return matchOption(options, name, want) ?? options[0];
}

function segmentMemory(node) {
  node.properties ||= {};
  if (!node.properties.feihou_segment_modes || typeof node.properties.feihou_segment_modes !== "object") {
    node.properties.feihou_segment_modes = {};
  }
  return node.properties.feihou_segment_modes;
}

function rememberSegmentMode(node, mode) {
  const entry = {};
  for (const name of SEGMENT_MODE_FIELDS) {
    const widget = segmentWidget(node, name);
    if (widget) entry[name] = widget.value;
  }
  segmentMemory(node)[mode] = entry;
}

// Show `mode`'s options.  switching: the mode's remembered values, else its
// recommended ones.  Otherwise (load / create): keep the current value when
// it belongs to the mode.  不分段 greys out segment_frames (overlap_frames
// still serves @split segments and @continue).
function applySegmentMode(node, mode, switching) {
  const memory = segmentMemory(node)[mode] || {};
  for (const name of SEGMENT_MODE_FIELDS) {
    const widget = segmentWidget(node, name);
    if (!widget) continue;
    const options = modeOptions(widget, name, mode);
    if (!options.length) continue;
    widget.options ||= {};
    widget.options.values = options;
    const value = (switching ? undefined : matchOption(options, name, widget.value))
      ?? matchOption(options, name, memory[name])
      ?? recommendedOption(options, name, mode);
    setWidgetValue(widget, value);
    const off = mode === SEG_AUTO || mode === SEG_WHOLE;
    widget.disabled = off;
    widget.options.disabled = off;
  }
  // 每段上限 only splits context-window blocks.
  for (const name of ["context_segment", "crossfade_frames"]) {
    const segmentCap = segmentWidget(node, name);
    if (!segmentCap) continue;
    const off = mode !== SEG_CONTEXT;
    segmentCap.disabled = off;
    segmentCap.options ||= {};
    segmentCap.options.disabled = off;
  }
  node.__h3SegmentMode = mode;
  rememberSegmentMode(node, mode);
  node.setDirtyCanvas?.(true, true);
}

function syncSegmentMode(node) {
  const widget = segmentWidget(node, "segmentation");
  if (!widget) return;
  const mode = segmentModeOf(widget.value);
  if (widget.value !== mode) setWidgetValue(widget, mode);
  applySegmentMode(node, mode, false);
}

function watchSegmentation(node) {
  const widget = segmentWidget(node, "segmentation");
  if (!widget || widget.__h3SegmentWatched) return;
  widget.__h3SegmentWatched = true;
  for (const name of SEGMENT_MODE_FIELDS) {
    const field = segmentWidget(node, name);
    if (field) allOptions(field);
  }
  const callback = widget.callback;
  widget.callback = function () {
    const result = callback?.apply(this, arguments);
    const mode = segmentModeOf(widget.value);
    const previous = node.__h3SegmentMode;
    if (previous && previous !== mode) {
      // The fields still hold the previous mode's values: keep them for it.
      rememberSegmentMode(node, previous);
      applySegmentMode(node, mode, true);
    } else {
      applySegmentMode(node, mode, false);
    }
    return result;
  };
  syncSegmentMode(node);
}

// Saves from before the shared fields: frame_window off meant 不分段, and
// context_window_frames / context_overlap_frames were the window fields.  They
// become each mode's remembered values.
function migrateSegmentValues(node, values) {
  const migrated = { ...values };
  const hasFrameWindow = "frame_window" in values;
  const hasContext = "context_window_frames" in values || "context_overlap_frames" in values;
  if (!hasFrameWindow && !hasContext) return migrated;
  const memory = segmentMemory(node);
  let mode = segmentModeOf(values.segmentation);
  if (mode === SEG_CHAIN && values.frame_window === false) mode = SEG_WHOLE;
  // An old save has no memory of its own (only defaults from node creation).
  const chain = { segment_frames: values.segment_frames, overlap_frames: values.overlap_frames };
  memory[SEG_CHAIN] = chain;
  memory[SEG_WHOLE] = { ...chain };
  if (hasContext) {
    memory[SEG_CONTEXT] = {
      segment_frames: values.context_window_frames ?? "345",
      overlap_frames: LEGACY_CONTEXT_OVERLAPS[String(values.context_overlap_frames)] ?? values.context_overlap_frames ?? "73",
    };
  }
  migrated.segmentation = mode;
  if (mode === SEG_CONTEXT && memory[SEG_CONTEXT]) Object.assign(migrated, memory[SEG_CONTEXT]);
  delete migrated.frame_window;
  delete migrated.context_window_frames;
  delete migrated.context_overlap_frames;
  return migrated;
}

function setSegmentWidget(node, name, value) {
  if (value === null || value === undefined) return;
  // 34 / 68 / 102 were only ever window overlaps: the real 39 / 73 / 107.
  if (name === "overlap_frames" && LEGACY_CONTEXT_OVERLAPS[String(value)]) value = LEGACY_CONTEXT_OVERLAPS[String(value)];
  const widget = segmentWidget(node, name);
  if (!widget) return;
  // A value from another layout never lands on the wrong kind of field: a
  // switch takes only true/false, an option list only its own options (the
  // old numeric segment_frames is relabelled by applySegmentMode).
  if (typeof widget.value === "boolean" && typeof value !== "boolean") return;
  const options = SEGMENT_MODE_FIELDS.includes(name) ? allOptions(widget) : widget.options?.values;
  if (Array.isArray(options) && !options.includes(value) && !(name === "segment_frames" && Number.isFinite(frameCount(value)))
      && !(name === "segmentation" && value === "接续")) return;
  setWidgetValue(widget, value);
}

// Choices a save from before them could not hold: 上下文每段上限 came after
// frame_window / context_window_frames, so positional loading of such a save
// put another field's value there (e.g. the overlap "22") and the run failed
// validation.  Its saved name list has no entry for it either.
const SEGMENT_CHOICE_DEFAULTS = { context_segment: "30 秒", crossfade_frames: "12" };

function repairSegmentChoices(node) {
  for (const [name, fallback] of Object.entries(SEGMENT_CHOICE_DEFAULTS)) {
    const widget = segmentWidget(node, name);
    const options = widget?.options?.values;
    if (!widget || !Array.isArray(options) || !options.length || options.includes(widget.value)) continue;
    setWidgetValue(widget, options.includes(fallback) ? fallback : options[0]);
  }
}

function applySegmentValues(node, values) {
  const migrated = migrateSegmentValues(node, values);
  // segmentation first: the shared fields are then checked against its options.
  setSegmentWidget(node, "segmentation", migrated.segmentation);
  Object.entries(migrated).forEach(([name, value]) => { if (name !== "segmentation") setSegmentWidget(node, name, value); });
  repairSegmentChoices(node);
  syncSegmentMode(node);
}

function segmentValuesByPosition(names, values) {
  if (!Array.isArray(values)) return null;
  const named = {};
  names.forEach((name, index) => { if (index < values.length) named[name] = values[index]; });
  return named;
}

function segmentRestorer(node, info) {
  let values = info?.properties?.feihou_segment_widgets;
  if (!values || typeof values !== "object") {
    const layout = Number(info?.properties?.feihou_segment_layout);
    const names = layout === 2 ? SEGMENT_LAYOUT2_WIDGETS : layout >= SEGMENT_LAYOUT ? null : SEGMENT_LEGACY_WIDGETS;
    values = names ? segmentValuesByPosition(names, info?.widgets_values) : null;
  }
  return values ? () => applySegmentValues(node, values) : null;
}

function installSegmentLayout(nodeType) {
  const created = nodeType.prototype.onNodeCreated;
  nodeType.prototype.onNodeCreated = function () {
    const result = created?.apply(this, arguments);
    this.properties ||= {};
    this.properties.feihou_segment_layout = SEGMENT_LAYOUT;
    watchSegmentation(this);
    return result;
  };
  const configured = nodeType.prototype.onConfigure;
  nodeType.prototype.onConfigure = function (info) {
    const result = configured?.apply(this, arguments);
    this.properties ||= {};
    this.properties.feihou_segment_layout = SEGMENT_LAYOUT;
    watchSegmentation(this);
    const restore = segmentRestorer(this, info);
    const settle = () => {
      if (restore) restore();
      else { repairSegmentChoices(this); syncSegmentMode(this); }
    };
    settle();
    // Some frontends re-apply widgets_values by position after onConfigure.
    setTimeout(settle, 0);
    return result;
  };
  const serialized = nodeType.prototype.onSerialize;
  nodeType.prototype.onSerialize = function (info) {
    const result = serialized?.apply(this, arguments);
    if (this.__h3SegmentMode) rememberSegmentMode(this, this.__h3SegmentMode);
    info.properties ||= {};
    info.properties.feihou_segment_layout = SEGMENT_LAYOUT;
    info.properties.feihou_segment_modes = { ...segmentMemory(this) };
    info.properties.feihou_segment_widgets = Object.fromEntries(
      (this.widgets || []).filter((widget) => widget?.name).map((widget) => [widget.name, widget.value]));
    return result;
  };
}

// 分镜提示词 in a Sample Enhancer graph (v8 style, no Motion Collect): the
// server finishes the block when the whole run succeeded and puts the next one
// at the front of the queue itself (another tab's task waits); the browser only
// shows the new 镜号.
// Queue the film's next run from this graph: 镜号 names the block to continue
// (a 0 = 全片重跑 start would otherwise start the film over again).
function queueNextRun(shot, block) {
  const widget = shot?.widgets?.find((item) => item?.name === "shot_numbers");
  if (widget && startValue(shot) !== START_MANUAL && block) {
    widget.value = String(Math.round(Number(block)));
    widget.__h3Edited = false;
    if (widget._state) widget._state.value = widget.value;
    shot.setDirtyCanvas?.(true, true);
  }
  setTimeout(() => void app.queuePrompt(0, 1), 0);
}

function showStandaloneProgress(info) {
  if (!info) return;
  const owner = info.node == null ? null : app.graph?.getNodeById?.(info.node);
  if (owner?.type !== SHOT_NODE) return;  // another workflow tab is open
  setShotProgress(info, info.node);
  console.info(info.shot_done ? `[Easy H3] 分镜提示词：全片已跑完`
    : `[Easy H3] 分镜 ${info.block} 完成，下一块 ${info.shot_next} 已由后台排在队列最前面`);
}

// save_segments: show each saved segment in the video node(s) fed by Motion
// Collect's images output (e.g. Video Combine V2), which itself only runs on
// the block's last segment.
function showSegmentVideo(collect, video) {
  const links = collect?.outputs?.[0]?.links || [];
  for (const id of links) {
    const link = app.graph?.links?.[id] ?? app.graph?.links?.get?.(id);
    const target = link ? app.graph.getNodeById(link.target_id) : null;
    if (typeof target?.updateParameters !== "function") continue;
    target.updateParameters({ ...video }, true);
    const preview = target.widgets?.find((widget) => widget?.name === "videopreview");
    if (preview?.value) preview.value.hidden = false;
    target.setDirtyCanvas?.(true, true);
  }
}

// Motion Collect saved before filename_prefix was removed (2026-11-09):
// [audio_output, auto_queue, color_match, save_segments, filename_prefix,
// color_match_strength].  Positional loading put the prefix text into
// color_match_strength; take the strength from its old slot.
function installCollectLayout(nodeType) {
  const configured = nodeType.prototype.onConfigure;
  nodeType.prototype.onConfigure = function (info) {
    const result = configured?.apply(this, arguments);
    const values = info?.widgets_values;
    if (Array.isArray(values) && values.length === 6 && typeof values[4] === "string") {
      const strength = this.widgets?.find((widget) => widget?.name === "color_match_strength");
      if (strength) {
        strength.value = typeof values[5] === "number" ? values[5] : 1;
        if (strength._state) strength._state.value = strength.value;
      }
    }
    return result;
  };
}

app.registerExtension({
  name: "FeiHou.EasyH3.MotionTransfer",
  setup() {
    installDropTarget();
    api.addEventListener("feihou_shot_progress", (event) => showStandaloneProgress(event?.detail));
    api.addEventListener("executed", (event) => {
      const output = event?.detail?.output || {};
      // Another workflow tab may be open now: only touch the graph the run belongs to.
      const ownerId = event?.detail?.display_node ?? event?.detail?.node;
      const owner = app.graph?.getNodeById?.(ownerId);
      const ownedBy = (type) => owner?.type === type;
      if (output.feihou_shot?.[0] && ownedBy(SHOT_NODE)) showShot(output.feihou_shot[0]);
      const info = output.feihou_motion?.[0];
      if (ownedBy("FeiHouEasyH3MotionCollect")) setShotProgress(info);
      if (info?.segment_video && ownedBy("FeiHouEasyH3MotionCollect")) showSegmentVideo(owner, info.segment_video);
      if (!info?.next) return;
      const mine = ownedBy("FeiHouEasyH3MotionCollect");
      const shot = mine ? activeShotNode() : null;
      if (info.queued) {
        // The server already queued this film's next run, at the front of the queue.
        console.info("[Easy H3] 动作迁移：下一段已由后台排在队列最前面");
        return;
      }
      if (!mine) return;  // another workflow is open: queueing it would run the wrong graph
      const where = info.blocks ? `第 ${info.block}/${info.blocks} 个分镜，第 ${info.done}/${info.count} 段` : `第 ${info.done}/${info.count} 段`;
      console.info(`[Easy H3] 动作迁移：${where}完成，排队下一段`);
      queueNextRun(shot, "shot_done" in info ? info.shot_next : info.block);
    });
  },
  beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name === SEGMENT_NODE) {
      installSegmentLayout(nodeType);
      return;
    }
    if (nodeData?.name === "FeiHouEasyH3MotionCollect") {
      installCollectLayout(nodeType);
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
      migrateStart(this);
      // The button label is not saved: show the remembered file again.
      const remembered = baseName(pathWidget(this)?.value) || this.properties?.feihou_md_name;
      if (remembered) setButtonLabel(this, remembered);
      return result;
    };
  },
});
