// Same canvas-native stack UI as FeiHou LoRA Stack (Merge/Extract).
// Adapted from rgthree-comfy's Power Lora Loader (MIT License).
import { app } from "../../scripts/app.js";
import { RgthreeBaseServerNode } from "../rgthree-comfy/base_node.js";
import { rgthree } from "../rgthree-comfy/rgthree.js";
import {
    drawNumberWidgetPart,
    drawRoundedRectangle,
    drawTogglePart,
    fitString,
    isLowQuality,
} from "../rgthree-comfy/utils_canvas.js";
import {
    RgthreeBaseWidget,
    RgthreeBetterButtonWidget,
    RgthreeDividerWidget,
} from "../rgthree-comfy/utils_widgets.js";
import { showLoraChooser } from "../rgthree-comfy/utils_menu.js";
import { rgthreeApi } from "../../rgthree/common/rgthree_api.js";
import { moveArrayItem, removeArrayItem } from "../../rgthree/common/shared_utils.js";

const STACK_CLASS = "FeiHouEasyH3LoraStack";
const STACK_WIDTH = 440;
const BOTTOM_MARGIN = 14;
const LORA_TEXT_NORMAL = "#f5f5f5";
const LORA_TEXT_BYPASS = "#b8d8ff";
const LORA_TEXT_DISABLED = "#777777";

function drawLoraToggle(ctx, {posX, posY, height, value, activeColor}) {
    ctx.save();
    ctx.fillStyle = "rgba(255,255,255,0.12)";
    ctx.beginPath();
    ctx.roundRect(posX + 4, posY + 4, height * 1.5 - 8, height - 8, height * 0.5);
    ctx.fill();
    ctx.fillStyle = value ? activeColor : LORA_TEXT_DISABLED;
    ctx.beginPath();
    ctx.arc(posX + height * (value ? 1 : 0.5), posY + height * 0.5, height * 0.36, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
    return [posX, height * 1.5];
}

function isChineseLocale() {
    const locale = app.ui?.settings?.getSettingValue?.("Comfy.Locale") || navigator.language || "en";
    return String(locale).toLowerCase().startsWith("zh");
}

function t(zh, en) {
    return isChineseLocale() ? zh : en;
}

function stackTitle() {
    return t("加载LoRA（仅模型）", "Load LoRA (Model Only)");
}

function localizeLoraSlots(node) {
    for (const input of node?.inputs || []) {
        if (input?.name === "optional_lora_stack") {
            input.label = t("可选 LoRA 堆栈", "Optional LoRA stack");
            input.localized_name = input.label;
        }
    }
    for (const output of node?.outputs || []) {
        if (output?.name === "lora_stack") {
            output.label = t("LoRA 堆栈", "LoRA stack");
            output.localized_name = output.label;
        }
    }
}

function normalizeLoraValue(value, defaultBypass = true) {
    if (!value || typeof value !== "object" || Array.isArray(value) || !("lora" in value)) return null;
    return {
        on: value.on !== false,
        lora: value.lora && value.lora !== "None" ? String(value.lora) : null,
        strength: Number.isFinite(Number(value.strength)) ? Number(value.strength) : 1,
        bypass: "bypass" in value ? value.bypass !== false : defaultBypass,
    };
}

function migrateLoraValues(values, defaultBypass = true) {
    if (!Array.isArray(values)) return [];
    const dynamic = values.map((value) => normalizeLoraValue(value, defaultBypass)).filter(Boolean);
    if (dynamic.length) return dynamic;
    if (values.length < 5 || typeof values[0] !== "boolean") return [];
    const masterEnabled = values[0] !== false;
    const count = Math.max(0, Math.min(50, Number(values[1]) || 0));
    const migrated = [];
    for (let index = 0; index < count; index++) {
        const offset = 2 + index * 3;
        const name = values[offset + 1];
        migrated.push({
            on: masterEnabled && values[offset] !== false,
            lora: name && name !== "None" ? String(name) : null,
            strength: Number.isFinite(Number(values[offset + 2])) ? Number(values[offset + 2]) : 1,
            bypass: defaultBypass,
        });
    }
    return migrated;
}

class FeiHouEasyH3LoraStackNode extends RgthreeBaseServerNode {
    constructor(title = stackTitle()) {
        super(title);
        this.loraWidgetsCounter = 0;
        this.widgetButtonSpacer = null;
        rgthreeApi.getLoras();
    }

    configure(info) {
        const legacyBypass = typeof info?.properties?.feihou_lora_bypass === "boolean"
            ? info.properties.feihou_lora_bypass
            : true;
        const values = migrateLoraValues(info?.widgets_values || [], legacyBypass);
        // Mute state is mirrored in properties: some frontend restore paths
        // (workflow tab switch) re-create this node and lose node.mode.
        const savedMode = Number.isInteger(info?.properties?.feihou_mode) ? info.properties.feihou_mode : info?.mode;
        this.title = stackTitle();
        while (this.widgets?.length) this.removeWidget(0);
        this.widgetButtonSpacer = null;
        this.loraWidgetsCounter = 0;
        if (info?.id != null) super.configure({...info, widgets_values: []});
        // The old node-level bypass property is consumed only as a migration
        // hint above and is not written back to the workflow.
        if (this.properties) delete this.properties.feihou_lora_bypass;
        this.title = stackTitle();
        for (const value of values) {
            const row = this.addNewLoraWidget();
            row.value = value;
        }
        this.addNonLoraWidgets();
        localizeLoraSlots(this);
        this.resizeToContent();
        if (Number.isInteger(savedMode)) {
            this.mode = savedMode;
            // Re-assert after the frontend finishes its own restore pass.
            setTimeout(() => {
                if (this.mode !== savedMode && this.properties?.feihou_mode === savedMode) {
                    this.mode = savedMode;
                    this.setDirtyCanvas?.(true, true);
                }
            }, 0);
        }
    }

    onModeChange(from, to) {
        super.onModeChange?.(from, to);
        if (this.configuring) return;
        this.properties ||= {};
        if (Number.isInteger(to)) this.properties.feihou_mode = to;
    }

    onNodeCreated() {
        super.onNodeCreated?.();
        this.title = stackTitle();
        this.addNonLoraWidgets();
        localizeLoraSlots(this);
        this.resizeToContent();
        this.setDirtyCanvas(true, true);
    }

    resizeToContent() {
        const computed = this.computeSize();
        this.size = [STACK_WIDTH, Math.ceil(computed[1]) + BOTTOM_MARGIN];
        this.setDirtyCanvas(true, true);
    }

    addNewLoraWidget(lora) {
        this.loraWidgetsCounter++;
        const row = this.addCustomWidget(new FeiHouEasyH3LoraWidget(`lora_${this.loraWidgetsCounter}`));
        if (lora) row.setLora(lora);
        if (this.widgetButtonSpacer) {
            moveArrayItem(this.widgets, row, this.widgets.indexOf(this.widgetButtonSpacer));
        }
        return row;
    }

    addNonLoraWidgets() {
        moveArrayItem(
            this.widgets,
            this.addCustomWidget(new RgthreeDividerWidget({marginTop: 4, marginBottom: 0, thickness: 0})),
            0,
        );
        moveArrayItem(this.widgets, this.addCustomWidget(new FeiHouEasyH3LoraHeaderWidget()), 1);
        this.widgetButtonSpacer = this.addCustomWidget(
            new RgthreeDividerWidget({marginTop: 4, marginBottom: 0, thickness: 0}),
        );
        this.addCustomWidget(
            new RgthreeBetterButtonWidget(t("+ 添加 LoRA", "+ Add LoRA"), (event) => {
                this.showLoraChooser(event, (value) => {
                    if (value && value !== "NONE" && value !== "None") {
                        this.addNewLoraWidget(value);
                        this.resizeToContent();
                    }
                });
                return true;
            }),
        );
    }

    async showLoraChooser(event, onChoose) {
        const details = await rgthreeApi.getLoras();
        const loras = details.map((item) => item.file);
        showLoraChooser(
            event,
            (value) => {
                if (typeof value === "string") onChoose(value.toUpperCase() === "NONE" ? null : value);
                this.setDirtyCanvas(true, true);
            },
            null,
            ["None", ...loras],
        );
    }

    getSlotInPosition(canvasX, canvasY) {
        const slot = super.getSlotInPosition(canvasX, canvasY);
        if (slot) return slot;
        let lastWidget = null;
        for (const current of this.widgets || []) {
            if (current.last_y == null) break;
            if (canvasY > this.pos[1] + current.last_y) {
                lastWidget = current;
                continue;
            }
            break;
        }
        if (lastWidget?.name?.startsWith("lora_")) {
            return {widget: lastWidget, output: {type: "LORA WIDGET"}};
        }
        return undefined;
    }

    getSlotMenuOptions(slot) {
        if (slot?.widget?.name?.startsWith("lora_")) {
            const row = slot.widget;
            const index = this.widgets.indexOf(row);
            const canMoveUp = Boolean(this.widgets[index - 1]?.name?.startsWith("lora_"));
            const canMoveDown = Boolean(this.widgets[index + 1]?.name?.startsWith("lora_"));
            const menuItems = [
                {
                    content: row.value.on ? t("禁用", "Disable") : t("启用", "Enable"),
                    callback: () => {
                        row.value.on = !row.value.on;
                        this.setDirtyCanvas(true, true);
                    },
                },
                {
                    content: row.value.bypass ? t("改为正常加载", "Use regular loading") : t("改为旁路加载", "Use bypass loading"),
                    callback: () => {
                        row.value.bypass = !row.value.bypass;
                        this.setDirtyCanvas(true, true);
                    },
                },
                {
                    content: t("清空 LoRA", "Clear LoRA"),
                    disabled: !row.value.lora,
                    callback: () => {
                        row.value.lora = null;
                        this.setDirtyCanvas(true, true);
                    },
                },
                {content: t("上移", "Move Up"), disabled: !canMoveUp, callback: () => moveArrayItem(this.widgets, row, index - 1)},
                {content: t("下移", "Move Down"), disabled: !canMoveDown, callback: () => moveArrayItem(this.widgets, row, index + 1)},
                null,
                {
                    content: t("移除", "Remove"),
                    callback: () => {
                        removeArrayItem(this.widgets, row);
                        this.resizeToContent();
                    },
                },
            ];
            new LiteGraph.ContextMenu(menuItems, {
                title: t("LoRA 控件", "LORA WIDGET"),
                event: rgthree.lastCanvasMouseEvent,
            });
            return undefined;
        }
        return this.defaultGetSlotMenuOptions(slot);
    }

    refreshComboInNode() {
        rgthreeApi.getLoras(true);
    }

    loraWidgets() {
        return (this.widgets || []).filter((item) => item.name?.startsWith("lora_"));
    }

    hasLoraWidgets() {
        return this.loraWidgets().length > 0;
    }

    allLorasState() {
        const rows = this.loraWidgets();
        if (!rows.length) return false;
        const allOn = rows.every((row) => row.value.on === true);
        const allOff = rows.every((row) => row.value.on === false);
        return allOn ? true : allOff ? false : null;
    }

    toggleAllLoras() {
        const value = this.allLorasState() === true ? false : true;
        for (const row of this.loraWidgets()) row.value.on = value;
        this.setDirtyCanvas(true, true);
    }

    static setUp(comfyClass, nodeData) {
        RgthreeBaseServerNode.registerForOverride(comfyClass, nodeData, FeiHouEasyH3LoraStackNode);
    }

    static onRegisteredForOverride(comfyClass) {
        setTimeout(() => { FeiHouEasyH3LoraStackNode.category = comfyClass.category; });
    }
}

FeiHouEasyH3LoraStackNode.title = stackTitle();
FeiHouEasyH3LoraStackNode.type = STACK_CLASS;
FeiHouEasyH3LoraStackNode.comfyClass = STACK_CLASS;

class FeiHouEasyH3LoraHeaderWidget extends RgthreeBaseWidget {
    constructor() {
        super("FeiHouEasyH3LoraHeaderWidget");
        this.value = {type: "FeiHouEasyH3LoraHeaderWidget"};
        this.options = {serialize: false};
        this.hitAreas = {
            toggle: {bounds: [0, 0], onDown: this.onToggleDown},
        };
    }

    draw(ctx, node, width, posY, height) {
        const margin = 10;
        const innerMargin = margin * 0.33;
        const midY = posY + height * 0.5;
        let posX = margin;
        ctx.save();
        this.hitAreas.toggle.bounds = drawTogglePart(ctx, {posX, posY, height, value: node.allLorasState()});
        if (!isLowQuality()) {
            posX += this.hitAreas.toggle.bounds[1] + innerMargin;
            ctx.globalAlpha = app.canvas.editor_alpha * 0.55;
            ctx.fillStyle = LiteGraph.WIDGET_TEXT_COLOR;
            ctx.textAlign = "left";
            ctx.textBaseline = "middle";
            ctx.fillText(t("全部开关", "Toggle All"), posX, midY);
            ctx.textAlign = "center";
            const right = node.size[0] - margin - innerMargin * 2;
            ctx.fillText(t("强度", "Strength"), right - drawNumberWidgetPart.WIDTH_TOTAL / 2, midY);
        }
        ctx.restore();
    }

    onToggleDown(event, pos, node) {
        node.toggleAllLoras();
        this.cancelMouseDown();
        return true;
    }
}

class FeiHouEasyH3LoraWidget extends RgthreeBaseWidget {
    constructor(name) {
        super(name);
        this.haveMouseMovedStrength = false;
        this._value = {on: true, lora: null, strength: 1, bypass: true};
        this.hitAreas = {
            toggle: {bounds: [0, 0], onDown: this.onToggleDown},
            bypass: {bounds: [0, 0], onDown: this.onBypassDown},
            lora: {bounds: [0, 0], onClick: this.onLoraClick},
            strengthDec: {bounds: [0, 0], onClick: this.onStrengthDecDown},
            strengthVal: {bounds: [0, 0], onClick: this.onStrengthValUp},
            strengthInc: {bounds: [0, 0], onClick: this.onStrengthIncDown},
            strengthAny: {bounds: [0, 0], onMove: this.onStrengthAnyMove},
        };
    }

    set value(value) {
        this._value = normalizeLoraValue(value) || {on: true, lora: null, strength: 1, bypass: true};
    }

    get value() {
        return this._value;
    }

    setLora(lora) {
        this._value.lora = lora;
    }

    draw(ctx, node, width, posY, height) {
        ctx.save();
        const margin = 10;
        const innerMargin = margin * 0.33;
        const midY = posY + height * 0.5;
        const lowQuality = isLowQuality();
        const rowColor = !this.value.on ? LORA_TEXT_DISABLED
            : this.value.bypass ? LORA_TEXT_BYPASS : LORA_TEXT_NORMAL;
        let posX = margin;
        ctx.globalAlpha = app.canvas.editor_alpha;
        drawRoundedRectangle(ctx, {pos: [posX, posY], size: [node.size[0] - margin * 2, height]});
        this.hitAreas.toggle.bounds = drawLoraToggle(ctx, {
            posX, posY, height, value: this.value.on, activeColor: LORA_TEXT_NORMAL,
        });
        posX += this.hitAreas.toggle.bounds[1] + innerMargin;
        ctx.fillStyle = rowColor;
        ctx.textAlign = "left";
        ctx.textBaseline = "middle";
        const enabledLabel = t("启用", "On");
        if (!lowQuality) ctx.fillText(enabledLabel, posX, midY);
        posX += ctx.measureText(enabledLabel).width + innerMargin;
        this.hitAreas.bypass.bounds = drawLoraToggle(ctx, {
            posX, posY, height, value: this.value.bypass,
            activeColor: this.value.on ? LORA_TEXT_BYPASS : LORA_TEXT_DISABLED,
        });
        posX += this.hitAreas.bypass.bounds[1] + innerMargin;
        const bypassLabel = t("旁路", "Bypass");
        if (!lowQuality) ctx.fillText(bypassLabel, posX, midY);
        posX += ctx.measureText(bypassLabel).width + innerMargin;
        ctx.strokeStyle = rowColor;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(posX, posY + height * 0.2);
        ctx.lineTo(posX, posY + height * 0.8);
        ctx.stroke();
        posX += innerMargin * 2 + 1;
        // Invisible text/numeric controls must not retain stale click targets
        // when zooming out. The two switches keep the same positions.
        for (const name of ["lora", "strengthDec", "strengthVal", "strengthInc", "strengthAny"]) {
            this.hitAreas[name].bounds = [0, 0];
        }
        if (lowQuality) {
            ctx.restore();
            return;
        }
        ctx.fillStyle = rowColor;
        const [leftArrow, numberText, rightArrow] = drawNumberWidgetPart(ctx, {
            posX: node.size[0] - margin - innerMargin * 2,
            posY,
            height,
            value: this.value.strength,
            direction: -1,
            textColor: rowColor,
        });
        this.hitAreas.strengthDec.bounds = leftArrow;
        this.hitAreas.strengthVal.bounds = numberText;
        this.hitAreas.strengthInc.bounds = rightArrow;
        this.hitAreas.strengthAny.bounds = [leftArrow[0], rightArrow[0] + rightArrow[1] - leftArrow[0]];
        const loraWidth = Math.max(0, leftArrow[0] - innerMargin - posX);
        ctx.textAlign = "left";
        ctx.textBaseline = "middle";
        if (loraWidth > 0) ctx.fillText(fitString(ctx, String(this.value.lora || t("无", "None")), loraWidth), posX, midY);
        this.hitAreas.lora.bounds = [posX, loraWidth];
        ctx.globalAlpha = app.canvas.editor_alpha;
        ctx.restore();
    }

    serializeValue() {
        return {...this.value};
    }

    onToggleDown(event, pos, node) {
        this.value.on = !this.value.on;
        node?.setDirtyCanvas?.(true, true);
        this.cancelMouseDown();
        return true;
    }

    onBypassDown(event, pos, node) {
        this.value.bypass = !this.value.bypass;
        node?.setDirtyCanvas?.(true, true);
        this.cancelMouseDown();
        return true;
    }

    onLoraClick(event, pos, node) {
        node.showLoraChooser(event, (value) => { this.value.lora = value || null; });
        this.cancelMouseDown();
    }

    onStrengthDecDown() {
        this.stepStrength(-1);
    }

    onStrengthIncDown() {
        this.stepStrength(1);
    }

    onStrengthAnyMove(event) {
        if (event.deltaX) {
            this.haveMouseMovedStrength = true;
            this.value.strength += event.deltaX * 0.05;
        }
    }

    onStrengthValUp(event) {
        if (this.haveMouseMovedStrength) return;
        app.canvas.prompt(t("强度", "Value"), this.value.strength, (value) => {
            const number = Number(value);
            if (Number.isFinite(number)) this.value.strength = number;
        }, event);
    }

    onMouseUp(event, pos, node) {
        super.onMouseUp(event, pos, node);
        this.haveMouseMovedStrength = false;
    }

    stepStrength(direction) {
        const strength = this.value.strength + 0.05 * direction;
        this.value.strength = Math.round(strength * 100) / 100;
    }
}

app.registerExtension({
    name: "FeiHouEasyH3.NativeLoraStack",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name === STACK_CLASS) FeiHouEasyH3LoraStackNode.setUp(nodeType, nodeData);
    },
});
