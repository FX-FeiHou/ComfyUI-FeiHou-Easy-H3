// Grey out the semantic-bridge options while the bridge switch is off.
import { app } from "../../scripts/app.js";

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
