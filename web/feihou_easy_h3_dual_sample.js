// Grey out the semantic-bridge options while the bridge switch is off, and
// read an old saved dual_clock_euler as euler where it is no longer offered.
import { app } from "../../scripts/app.js";

// dual_clock_euler made a steady music-like bed in talk audio; the server only
// offers it on ComfyUI builds without native H3 sampling.
const SAMPLER_NODES = new Set(["FeiHouEasyH3SampleEnhancer1st", "FeiHouEasyH3DualSample1st", "FeiHouEasyH3MotionSegment"]);
const OLD_SAMPLER = "dual_clock_euler";

function migrateSampler(node) {
  const widget = node.widgets?.find((w) => w.name === "sampler_name");
  const offered = widget?.options?.values;
  if (!widget || widget.value !== OLD_SAMPLER || !Array.isArray(offered) || offered.includes(OLD_SAMPLER)) return false;
  widget.value = offered.includes("euler") ? "euler" : offered[0];
  // Motion Segment restores widgets by name from this memory: keep it in step.
  const remembered = node.properties?.feihou_segment_widgets;
  if (remembered?.sampler_name === OLD_SAMPLER) remembered.sampler_name = widget.value;
  node.setDirtyCanvas?.(true, true);
  return true;
}

const NODES = new Set(["FeiHouEasyH3SampleEnhancer1st"]);
const DEPENDENT = ["semantic_bridge", "bridge_strength"];

function sync(node) {
  const toggle = node.widgets?.find((w) => w.name === "semantic_bridge_on");
  if (!toggle) return;
  const off = !toggle.value;
  for (const name of DEPENDENT) {
    const widget = node.widgets.find((w) => w.name === name);
    if (!widget) continue;
    widget.disabled = off;
    widget.options = widget.options || {};
    widget.options.disabled = off;
  }
  node.setDirtyCanvas?.(true, true);
}

app.registerExtension({
  name: "FeiHou.EasyH3.SampleEnhancer",
  loadedGraphNode(node) {
    if (!SAMPLER_NODES.has(node?.type)) return;
    migrateSampler(node);
    // Some layouts re-apply saved values by position right after loading.
    setTimeout(() => {
      if (migrateSampler(node)) console.info("[Easy H3] 采样器 dual_clock_euler 已换成 euler（它会让口播音频带背景音乐）");
    }, 0);
  },
  beforeRegisterNodeDef(nodeType, nodeData) {
    if (!NODES.has(nodeData?.name)) return;
    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const result = created?.apply(this, arguments);
      const toggle = this.widgets?.find((w) => w.name === "semantic_bridge_on");
      if (toggle) {
        const callback = toggle.callback;
        toggle.callback = (...args) => {
          const r = callback?.apply(toggle, args);
          sync(this);
          return r;
        };
      }
      sync(this);
      return result;
    };
    const configure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const result = configure?.apply(this, arguments);
      sync(this);
      return result;
    };
  },
});
