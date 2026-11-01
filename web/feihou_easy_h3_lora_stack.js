// Same canvas-native stack UI as FeiHou LoRA Stack (Merge/Extract).
// Adapted from rgthree-comfy's Power Lora Loader and canvas widgets (MIT License,
// Copyright (c) 2023 Regis Gaughan, III). Works without rgthree-comfy installed.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const STACK_CLASS = "FeiHouEasyH3LoraStack";
const STACK_WIDTH = 440;
const BOTTOM_MARGIN = 14;
const LORA_TEXT_NORMAL = "#f5f5f5";
const LORA_TEXT_BYPASS = "#b8d8ff";
const LORA_TEXT_DISABLED = "#777777";

let lorasPromise = null;
let lastPointerEvent = null;
document.addEventListener("pointerdown", (event) => { lastPointerEvent = event; }, true);

function moveArrayItem(array, item, to) {
    array.splice(to, 0, array.splice(array.indexOf(item), 1)[0]);
}

function isLowQuality() {
    return (app.canvas?.ds?.scale || 1) <= 0.5;
}

function fitString(ctx, text, maxWidth) {
    if (ctx.measureText(text).width <= maxWidth) return text;
    const room = maxWidth - ctx.measureText("…").width;
    let low = 0;
    let high = text.length;
    while (low < high) {
        const middle = Math.ceil((low + high) / 2);
        if (ctx.measureText(text.substring(0, middle)).width <= room) low = middle;
        else high = middle - 1;
    }
    return text.substring(0, low) + "…";
}

function drawRoundedRectangle(ctx, {pos, size}) {
    const lowQuality = isLowQuality();
    ctx.save();
    ctx.strokeStyle = LiteGraph.WIDGET_OUTLINE_COLOR;
    ctx.fillStyle = LiteGraph.WIDGET_BGCOLOR;
    ctx.beginPath();
    ctx.roundRect(...pos, ...size, lowQuality ? [0] : [size[1] * 0.5]);
    ctx.fill();
    if (!lowQuality) ctx.stroke();
    ctx.restore();
}

function drawNumberWidgetPart(ctx, {posX, posY, height, value, textColor}) {
    const arrowWidth = 9;
    const arrowHeight = 10;
    const innerMargin = 3;
    const numberWidth = 32;
    const midY = posY + height / 2;
    let x = posX - drawNumberWidgetPart.WIDTH_TOTAL;
    const less = [x, arrowWidth];
    ctx.save();
    ctx.fill(new Path2D(`M ${x} ${midY} l ${arrowWidth} ${arrowHeight / 2} l 0 -${arrowHeight} L ${x} ${midY} z`));
    x += arrowWidth + innerMargin;
    const number = [x, numberWidth];
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    const arrowColor = ctx.fillStyle;
    if (textColor) ctx.fillStyle = textColor;
    ctx.fillText(fitString(ctx, value.toFixed(2), numberWidth), x + numberWidth / 2, midY);
    ctx.fillStyle = arrowColor;
    x += numberWidth + innerMargin;
    const more = [x, arrowWidth];
    ctx.fill(new Path2D(`M ${x} ${midY - arrowHeight / 2} l ${arrowWidth} ${arrowHeight / 2} l -${arrowWidth} ${arrowHeight / 2} v -${arrowHeight} z`));
    ctx.restore();
    return [less, number, more];
}
drawNumberWidgetPart.WIDTH_TOTAL = 9 + 3 + 32 + 3 + 9;

// Tri-state: true right, false left, null (some rows on) in the middle.
function drawTogglePart(ctx, {posX, posY, height, value}) {
    const lowQuality = isLowQuality();
    const width = height * 1.5;
    ctx.save();
    if (!lowQuality) {
        ctx.beginPath();
        ctx.roundRect(posX + 4, posY + 4, width - 8, height - 8, [height * 0.5]);
        ctx.globalAlpha = app.canvas.editor_alpha * 0.25;
        ctx.fillStyle = "rgba(255,255,255,0.45)";
        ctx.fill();
        ctx.globalAlpha = app.canvas.editor_alpha;
    }
    ctx.fillStyle = value === true ? "#89B" : "#888";
    const x = lowQuality || value === false ? posX + height * 0.5 : value === true ? posX + height : posX + height * 0.75;
    ctx.beginPath();
    ctx.arc(x, posY + height * 0.5, height * 0.36, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
    return [posX, width];
}

// LoraLoaderModelOnly is a core node, and object_info also answers remote browsers.
function getLoras(force = false) {
    if (!lorasPromise || force) {
        lorasPromise = api.fetchApi("/object_info/LoraLoaderModelOnly", {cache: "no-store"})
            .then((response) => response.json())
            .then((info) => {
                const spec = info?.LoraLoaderModelOnly?.input?.required?.lora_name;
                const list = Array.isArray(spec?.[0]) ? spec[0] : spec?.[1]?.options;
                return Array.isArray(list) ? list : [];
            })
            .catch((error) => {
                lorasPromise = null;
                console.warn("[FeiHou Easy H3] could not list LoRAs", error);
                return [];
            });
    }
    return lorasPromise;
}

class CanvasWidget {
    constructor(name) {
        this.name = name;
        this.type = "custom";
        this.options = {};
        this.y = 0;
        this.last_y = 0;
        this.mouseDowned = null;
        this.isMouseDownedAndOver = false;
        this.hitAreas = {};
        this.downedHitAreasForMove = [];
        this.downedHitAreasForClick = [];
    }

    serializeValue() {
        return this.value;
    }

    withinBounds(pos, bounds) {
        return pos[0] >= bounds[0] && pos[0] <= bounds[0] + bounds[1];
    }

    mouse(event, pos, node) {
        if (event.type === "pointerdown") {
            this.mouseDowned = [...pos];
            this.isMouseDownedAndOver = true;
            this.downedHitAreasForMove.length = 0;
            this.downedHitAreasForClick.length = 0;
            let handled = false;
            for (const part of Object.values(this.hitAreas)) {
                if (!this.withinBounds(pos, part.bounds)) continue;
                if (part.onMove) this.downedHitAreasForMove.push(part);
                if (part.onClick) this.downedHitAreasForClick.push(part);
                if (part.onDown) handled = part.onDown.apply(this, [event, pos, node, part]) === true || handled;
            }
            return handled;
        }
        if (event.type === "pointerup") {
            if (!this.mouseDowned) return true;
            const over = this.isMouseDownedAndOver;
            this.cancelMouseDown();
            let handled = false;
            for (const part of this.downedHitAreasForClick) {
                if (this.withinBounds(pos, part.bounds)) {
                    handled = part.onClick.apply(this, [event, pos, node, part]) === true || handled;
                }
            }
            this.downedHitAreasForClick.length = 0;
            if (over) handled = this.onMouseClick(event, pos, node) === true || handled;
            return this.onMouseUp(event, pos, node) ?? handled;
        }
        if (event.type === "pointermove") {
            this.isMouseDownedAndOver = Boolean(this.mouseDowned) && !(pos[0] < 15 || pos[0] > node.size[0] - 15
                || pos[1] < this.last_y || pos[1] > this.last_y + LiteGraph.NODE_WIDGET_HEIGHT);
            for (const part of this.downedHitAreasForMove) part.onMove.apply(this, [event, pos, node, part]);
            return true;
        }
        return false;
    }

    cancelMouseDown() {
        this.mouseDowned = null;
        this.isMouseDownedAndOver = false;
        this.downedHitAreasForMove.length = 0;
    }

    onMouseClick() {}

    onMouseUp() {}
}

class ButtonWidget extends CanvasWidget {
    constructor(label, onClick) {
        super(label);
        this.value = "";
        this.label = label;
        this.onClickCallback = onClick;
    }

    draw(ctx, node, width, y, height) {
        const lowQuality = isLowQuality();
        ctx.save();
        ctx.fillStyle = this.isMouseDownedAndOver ? "#444" : LiteGraph.WIDGET_BGCOLOR;
        ctx.strokeStyle = LiteGraph.WIDGET_OUTLINE_COLOR;
        ctx.beginPath();
        ctx.roundRect(15, y, width - 30, height, lowQuality ? [0] : [4]);
        ctx.fill();
        if (!lowQuality) {
            ctx.stroke();
            ctx.fillStyle = LiteGraph.WIDGET_TEXT_COLOR;
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            ctx.fillText(this.label, width / 2, y + height / 2);
        }
        ctx.restore();
    }

    onMouseClick(event, pos, node) {
        return this.onClickCallback(event, pos, node);
    }
}

class SpacerWidget extends CanvasWidget {
    constructor() {
        super("divider");
        this.value = {};
        this.options = {serialize: false};
    }

    draw() {}

    computeSize(width) {
        return [width, 4];
    }
}

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

// Methods put on the node's own ComfyUI class; ``base`` holds the ones they replace.
const base = {};

const stackMethods = {
    configure(info) {
        const legacyBypass = typeof info?.properties?.feihou_lora_bypass === "boolean"
            ? info.properties.feihou_lora_bypass
            : true;
        const values = migrateLoraValues(info?.widgets_values || [], legacyBypass);
        // Mute state is mirrored in properties: some frontend restore paths
        // (workflow tab switch) re-create this node and lose node.mode.
        const savedMode = Number.isInteger(info?.properties?.feihou_mode) ? info.properties.feihou_mode : info?.mode;
        this.title = stackTitle();
        if (this.widgets) this.widgets.length = 0;
        this.widgetButtonSpacer = null;
        this.loraWidgetsCounter = 0;
        if (info?.id != null) base.configure.call(this, {...info, widgets_values: []});
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
    },

    onSerialize(info) {
        base.onSerialize?.call(this, info);
        if (Number.isInteger(this.mode)) (info.properties ||= {}).feihou_mode = this.mode;
    },

    onNodeCreated() {
        base.onNodeCreated?.call(this);
        this.serialize_widgets = true;
        this.loraWidgetsCounter = 0;
        this.widgetButtonSpacer = null;
        this.title = stackTitle();
        this.addNonLoraWidgets();
        localizeLoraSlots(this);
        this.resizeToContent();
        this.setDirtyCanvas(true, true);
    },

    resizeToContent() {
        const computed = this.computeSize();
        this.size = [STACK_WIDTH, Math.ceil(computed[1]) + BOTTOM_MARGIN];
        this.setDirtyCanvas(true, true);
    },

    addNewLoraWidget(lora) {
        this.loraWidgetsCounter = (this.loraWidgetsCounter || 0) + 1;
        const row = this.addCustomWidget(new FeiHouEasyH3LoraWidget(`lora_${this.loraWidgetsCounter}`));
        if (lora) row.setLora(lora);
        if (this.widgetButtonSpacer) {
            moveArrayItem(this.widgets, row, this.widgets.indexOf(this.widgetButtonSpacer));
        }
        return row;
    },

    addNonLoraWidgets() {
        this.widgets ||= [];
        moveArrayItem(this.widgets, this.addCustomWidget(new SpacerWidget()), 0);
        moveArrayItem(this.widgets, this.addCustomWidget(new FeiHouEasyH3LoraHeaderWidget()), 1);
        this.widgetButtonSpacer = this.addCustomWidget(new SpacerWidget());
        this.addCustomWidget(
            new ButtonWidget(t("+ 添加 LoRA", "+ Add LoRA"), (event) => {
                this.showLoraChooser(event, (value) => {
                    if (value && value !== "NONE" && value !== "None") {
                        this.addNewLoraWidget(value);
                        this.resizeToContent();
                    }
                });
                return true;
            }),
        );
    },

    async showLoraChooser(event, onChoose) {
        const loras = await getLoras();
        new LiteGraph.ContextMenu(["None", ...loras], {
            event,
            title: "Choose a lora",
            scale: Math.max(1, app.canvas.ds?.scale ?? 1),
            className: "dark",
            callback: (value) => {
                if (typeof value === "string") onChoose(value.toUpperCase() === "NONE" ? null : value);
                this.setDirtyCanvas(true, true);
            },
        });
    },

    getSlotInPosition(canvasX, canvasY) {
        const slot = base.getSlotInPosition?.call(this, canvasX, canvasY);
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
    },

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
                        this.widgets.splice(this.widgets.indexOf(row), 1);
                        this.resizeToContent();
                    },
                },
            ];
            new LiteGraph.ContextMenu(menuItems, {
                title: t("LoRA 控件", "LORA WIDGET"),
                event: lastPointerEvent,
            });
            return undefined;
        }
        return base.getSlotMenuOptions?.call(this, slot) ?? defaultSlotMenu(slot);
    },

    refreshComboInNode() {
        base.refreshComboInNode?.call(this);
        getLoras(true);
    },

    loraWidgets() {
        return (this.widgets || []).filter((item) => item.name?.startsWith("lora_"));
    },

    hasLoraWidgets() {
        return this.loraWidgets().length > 0;
    },

    allLorasState() {
        const rows = this.loraWidgets();
        if (!rows.length) return false;
        const allOn = rows.every((row) => row.value.on === true);
        const allOff = rows.every((row) => row.value.on === false);
        return allOn ? true : allOff ? false : null;
    },

    toggleAllLoras() {
        const value = this.allLorasState() === true ? false : true;
        for (const row of this.loraWidgets()) row.value.on = value;
        this.setDirtyCanvas(true, true);
    },
};

function defaultSlotMenu(slot) {
    const menu = [];
    if (slot?.output?.links?.length) menu.push({content: "Disconnect Links", slot});
    const socket = slot?.input || slot?.output;
    if (socket) {
        if (socket.removable) menu.push(socket.locked ? {content: "Cannot remove"} : {content: "Remove Slot", slot});
        if (!socket.nameLocked) menu.push({content: "Rename Slot", slot});
    }
    return menu;
}

class FeiHouEasyH3LoraHeaderWidget extends CanvasWidget {
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

class FeiHouEasyH3LoraWidget extends CanvasWidget {
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

    onMouseUp() {
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
        if (nodeData.name !== STACK_CLASS) return;
        const proto = nodeType.prototype;
        for (const name of Object.keys(stackMethods)) base[name] = proto[name];
        Object.assign(proto, stackMethods);
        nodeType.title = stackTitle();
        getLoras();
    },
});
