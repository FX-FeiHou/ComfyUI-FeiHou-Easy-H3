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
const STACK_TITLE = "加载LoRA（旁路，仅模型）（用于调试）";
const STACK_WIDTH = 440;
const BOTTOM_MARGIN = 14;

function isChineseLocale() {
    const locale = app.ui?.settings?.getSettingValue?.("Comfy.Locale") || navigator.language || "en";
    return String(locale).toLowerCase().startsWith("zh");
}

function t(zh, en) {
    return isChineseLocale() ? zh : en;
}

function normalizeLoraValue(value) {
    if (!value || typeof value !== "object" || Array.isArray(value) || !("lora" in value)) return null;
    return {
        on: value.on !== false,
        lora: value.lora && value.lora !== "None" ? String(value.lora) : null,
        strength: Number.isFinite(Number(value.strength)) ? Number(value.strength) : 1,
    };
}

function migrateLoraValues(values) {
    if (!Array.isArray(values)) return [];
    const dynamic = values.map(normalizeLoraValue).filter(Boolean);
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
        });
    }
    return migrated;
}

class FeiHouEasyH3LoraStackNode extends RgthreeBaseServerNode {
    constructor(title = STACK_TITLE) {
        super(title);
        this.loraWidgetsCounter = 0;
        this.widgetButtonSpacer = null;
        rgthreeApi.getLoras();
    }

    configure(info) {
        const values = migrateLoraValues(info?.widgets_values || []);
        while (this.widgets?.length) this.removeWidget(0);
        this.widgetButtonSpacer = null;
        this.loraWidgetsCounter = 0;
        if (info?.id != null) super.configure({...info, widgets_values: []});
        for (const value of values) {
            const row = this.addNewLoraWidget();
            row.value = value;
        }
        this.addNonLoraWidgets();
        this.resizeToContent();
    }

    onNodeCreated() {
        super.onNodeCreated?.();
        this.addNonLoraWidgets();
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

FeiHouEasyH3LoraStackNode.title = STACK_TITLE;
FeiHouEasyH3LoraStackNode.type = STACK_CLASS;
FeiHouEasyH3LoraStackNode.comfyClass = STACK_CLASS;

class FeiHouEasyH3LoraHeaderWidget extends RgthreeBaseWidget {
    constructor() {
        super("FeiHouEasyH3LoraHeaderWidget");
        this.value = {type: "FeiHouEasyH3LoraHeaderWidget"};
        this.options = {serialize: false};
        this.hitAreas = {toggle: {bounds: [0, 0], onDown: this.onToggleDown}};
    }

    draw(ctx, node, width, posY, height) {
        if (!node.hasLoraWidgets()) return;
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
        this._value = {on: true, lora: null, strength: 1};
        this.hitAreas = {
            toggle: {bounds: [0, 0], onDown: this.onToggleDown},
            lora: {bounds: [0, 0], onClick: this.onLoraClick},
            strengthDec: {bounds: [0, 0], onClick: this.onStrengthDecDown},
            strengthVal: {bounds: [0, 0], onClick: this.onStrengthValUp},
            strengthInc: {bounds: [0, 0], onClick: this.onStrengthIncDown},
            strengthAny: {bounds: [0, 0], onMove: this.onStrengthAnyMove},
        };
    }

    set value(value) {
        this._value = normalizeLoraValue(value) || {on: true, lora: null, strength: 1};
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
        let posX = margin;
        drawRoundedRectangle(ctx, {pos: [posX, posY], size: [node.size[0] - margin * 2, height]});
        this.hitAreas.toggle.bounds = drawTogglePart(ctx, {posX, posY, height, value: this.value.on});
        posX += this.hitAreas.toggle.bounds[1] + innerMargin;
        if (isLowQuality()) {
            ctx.restore();
            return;
        }
        if (!this.value.on) ctx.globalAlpha = app.canvas.editor_alpha * 0.4;
        ctx.fillStyle = LiteGraph.WIDGET_TEXT_COLOR;
        const [leftArrow, numberText, rightArrow] = drawNumberWidgetPart(ctx, {
            posX: node.size[0] - margin - innerMargin * 2,
            posY,
            height,
            value: this.value.strength,
            direction: -1,
        });
        this.hitAreas.strengthDec.bounds = leftArrow;
        this.hitAreas.strengthVal.bounds = numberText;
        this.hitAreas.strengthInc.bounds = rightArrow;
        this.hitAreas.strengthAny.bounds = [leftArrow[0], rightArrow[0] + rightArrow[1] - leftArrow[0]];
        const loraWidth = leftArrow[0] - innerMargin - posX;
        ctx.textAlign = "left";
        ctx.textBaseline = "middle";
        ctx.fillText(fitString(ctx, String(this.value.lora || t("无", "None")), loraWidth), posX, midY);
        this.hitAreas.lora.bounds = [posX, loraWidth];
        ctx.globalAlpha = app.canvas.editor_alpha;
        ctx.restore();
    }

    serializeValue() {
        return {...this.value};
    }

    onToggleDown() {
        this.value.on = !this.value.on;
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
