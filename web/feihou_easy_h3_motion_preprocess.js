// H3 动作迁移预处理: every option stays visible; 模式 lights up its own and
// greys out the rest (关闭 / 自动: all grey; 人物替换: the face blur, whose level and detector also follow the face-blur switch).
// Migrates older saves and shows the blurred preview video.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const NODE = "FeiHouEasyH3MotionPreprocess";
const MODE = "mode";
const TOGGLE = "face_blur_on";
const DEPENDENT = ["face_blur", "face_detector", "face_recognizer"];
const SWAP_WIDGETS = [TOGGLE, ...DEPENDENT];
const SWAP = "人物替换";
const OFF = "关闭";
// widgets_values before 模式: [remake, face_blur_on, face_blur, face_detector] (the RH edition
// also had face_recognizer there);
// then the 模式 layouts that still had 动作迁移 (guide, TARA strength, its LoRAs, LoRA strength)
// or the experimental 动作引导 (its step count); then the layout with the Remake LoRA choice
// (the LoRA now comes from the workflow's LoRA list).
const LEGACY = ["remake", "face_blur_on", "face_blur", "face_detector", "face_recognizer"];
const OLD_LAYOUTS = [
  ["mode", "motion_guide", "tara_strength", "face_blur_on", "face_blur", "face_detector"],
  ["mode", "motion_guide", "tara_strength", "tara_lora", "latent_lora", "face_blur_on", "face_blur", "face_detector"],
  ["mode", "motion_guide", "tara_strength", "tara_lora", "latent_lora", "remake_lora", "face_blur_on", "face_blur",
    "face_detector"],
  ["mode", "motion_guide", "tara_strength", "tara_lora", "latent_lora", "motion_lora_strength", "remake_lora",
    "face_blur_on", "face_blur", "face_detector"],
  ["mode", "remake_lora", "face_blur_on", "face_blur", "face_detector", "guide_steps"],
  ["mode", "remake_lora", "face_blur_on", "face_blur", "face_detector", "face_recognizer"],
];

const widgetOf = (node, name) => node.widgets?.find((w) => w.name === name);

function setDisabled(widget, disabled) {
  if (!widget) return;
  widget.hidden = false;   // an earlier version hid them
  widget.disabled = disabled;
  widget.options = widget.options || {};
  widget.options.hidden = false;
  widget.options.disabled = disabled;
}

function enabledFor(mode, faceBlurOn) {
  if (mode === SWAP) return new Set(faceBlurOn ? SWAP_WIDGETS : [TOGGLE]);
  return new Set();   // 关闭 / 自动: the md or nothing decides
}

function sync(node) {
  const mode = widgetOf(node, MODE)?.value;
  const enabled = enabledFor(mode, !!widgetOf(node, TOGGLE)?.value);
  for (const name of SWAP_WIDGETS) setDisabled(widgetOf(node, name), !enabled.has(name));
  node.setDirtyCanvas?.(true, true);
}

function watch(node) {
  for (const name of [MODE, TOGGLE]) {
    const widget = widgetOf(node, name);
    if (!widget || widget.__h3PreprocessWatched) continue;
    widget.__h3PreprocessWatched = true;
    const callback = widget.callback;
    widget.callback = function (...args) {
      const result = callback?.apply(this, args);
      sync(node);
      return result;
    };
  }
}

function setValue(widget, value) {
  if (!widget || value === undefined) return;
  widget.value = value;
  if (widget._state) widget._state.value = value;
}

function setChoice(node, name, value) {
  const widget = widgetOf(node, name);
  if (widget && (widget.options?.values || []).includes(value)) setValue(widget, value);
}

// Older saves are loaded by position into the current widgets: put their values back by name.
function migrate(node, info) {
  const values = info?.widgets_values;
  if (!Array.isArray(values)) return false;
  let old = null;
  if (typeof values[0] === "boolean") {
    old = Object.fromEntries(LEGACY.map((name, index) => [name, values[index]]));
    old.mode = old.remake ? SWAP : OFF;
  } else {
    // Two 6-value layouts: the guide_steps one has a number in its 6th slot, the Remake LoRA one 人脸识别模型 (text).
    const layout = OLD_LAYOUTS.find((names) => names.length === values.length
      && typeof values[names.indexOf(TOGGLE)] === "boolean"
      && (!names.includes("guide_steps") || typeof values[names.indexOf("guide_steps")] === "number"));
    if (layout) old = Object.fromEntries(layout.map((name, index) => [name, values[index]]));
  }
  if (!old) return false;
  // 动作迁移 was removed: such a save opens as 关闭.
  setChoice(node, MODE, old.mode === SWAP || old.mode === "自动" ? old.mode : OFF);
  if (typeof old.face_blur_on === "boolean") setValue(widgetOf(node, TOGGLE), old.face_blur_on);
  setChoice(node, "face_blur", old.face_blur);
  // The detector list may name the same file with or without its folder (bbox\face_yolov8m.pt / face_yolov8m.pt).
  const detector = widgetOf(node, "face_detector");
  const fileOf = (value) => String(value ?? "").replace(/\\/g, "/").split("/").pop().toLowerCase();
  const sameFile = (detector?.options?.values || []).find((option) => fileOf(option) === fileOf(old.face_detector));
  if (sameFile !== undefined) setValue(detector, sameFile);
  setChoice(node, "face_recognizer", old.face_recognizer);
  return true;
}

function previewWidget(node) {
  if (node.__h3Preview) return node.__h3Preview;
  if (typeof document === "undefined" || typeof node.addDOMWidget !== "function") return null;
  // The box fills exactly the height the node gives the widget; the video takes
  // what is left under the summary and is letterboxed inside it (never overflows).
  const box = document.createElement("div");
  box.style.cssText = "display:flex;flex-direction:column;gap:4px;width:100%;height:100%;box-sizing:border-box;overflow:hidden;";
  const label = document.createElement("div");
  label.style.cssText = "flex:0 0 auto;overflow:hidden;font-size:11px;line-height:15px;color:var(--descrip-text,#aaa);white-space:normal;word-break:break-all;display:none;";
  const video = document.createElement("video");
  video.controls = true;
  video.loop = true;
  video.muted = true;
  video.playsInline = true;
  video.style.cssText = "flex:1 1 auto;min-height:0;width:100%;height:100%;object-fit:contain;background:#000;border-radius:4px;display:none;";
  box.append(label, video);
  const preview = { label, video };
  // Height this widget wants at the node's current width: the video at its own aspect ratio plus the summary.
  const wanted = () => {
    // The whole summary: its wrapped height at the node's width (15 px per line), plus the gap.
    const labelHeight = label.style.display === "none" ? 0
      : Math.max(1, Math.ceil(label.scrollHeight / 15) || Math.ceil(label.textContent.length * 11 / Math.max(120, (node.size?.[0] || 320) - 20))) * 15 + 4;
    if (video.style.display === "none") return labelHeight;
    const width = Math.max(120, (node.size?.[0] || 320) - 20);
    const ratio = video.videoWidth ? video.videoHeight / video.videoWidth : 16 / 9;
    return labelHeight + Math.round(Math.min(900, width * ratio));
  };
  const widget = node.addDOMWidget("blur_preview", "blur_preview", box, {
    serialize: false, hideOnZoom: false,
    getMinHeight: () => wanted(), getMaxHeight: () => wanted(),
  });
  widget.computeSize = (width) => [width, wanted()];
  preview.widget = widget;
  preview.fit = () => fitNode(node);
  video.addEventListener("loadedmetadata", preview.fit);
  node.__h3Preview = preview;
  return preview;
}

// Grow or shrink the node to the widgets' height, keeping the width the user set.
function fitNode(node) {
  setTimeout(() => {
    const size = node.computeSize?.([node.size[0], node.size[1]]);
    if (size) node.setSize([node.size[0], size[1]]);
    node.setDirtyCanvas?.(true, true);
    app.graph?.setDirtyCanvas?.(true, true);
  }, 0);
}

function showPreview(node, info) {
  const preview = previewWidget(node);
  if (!preview || !info) return;
  preview.label.textContent = info.summary || "";
  preview.label.style.display = info.summary ? "block" : "none";
  if (!info.filename) {
    preview.video.pause?.();
    preview.video.removeAttribute("src");
    preview.video.style.display = "none";
  } else {
    const query = new URLSearchParams({ filename: info.filename, subfolder: info.subfolder || "",
      type: info.type || "temp", t: String(Date.now()) });
    preview.video.src = api.apiURL(`/view?${query}`);
    preview.video.style.display = "block";
    preview.video.play?.().catch(() => {});
  }
  preview.fit();
}

app.registerExtension({
  name: "FeiHou.EasyH3.MotionPreprocess",
  setup() {
    api.addEventListener("executed", (event) => {
      const info = event?.detail?.output?.feihou_preprocess?.[0];
      if (!info) return;
      const node = app.graph?.getNodeById?.(event?.detail?.display_node ?? event?.detail?.node);
      if (node?.type === NODE) showPreview(node, info);
    });
  },
  beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== NODE) return;
    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const result = created?.apply(this, arguments);
      watch(this);
      sync(this);
      previewWidget(this);
      return result;
    };
    // Dragging the node wider or narrower keeps the preview at the video's aspect ratio.
    const resized = nodeType.prototype.onResize;
    nodeType.prototype.onResize = function (size) {
      const result = resized?.apply(this, arguments);
      const video = this.__h3Preview?.video;
      const width = Math.round(size?.[0] ?? this.size?.[0] ?? 0);
      if (video && video.style.display !== "none" && width && width !== this.__h3FitWidth) {
        this.__h3FitWidth = width;   // fitting keeps the width, so it does not loop
        fitNode(this);
      }
      return result;
    };
    const configure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function (info) {
      const result = configure?.apply(this, arguments);
      watch(this);
      migrate(this, info);
      sync(this);
      // Some frontends re-apply widgets_values after onConfigure.
      setTimeout(() => { migrate(this, info); sync(this); }, 0);
      return result;
    };
  },
});
