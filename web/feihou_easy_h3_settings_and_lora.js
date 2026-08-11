import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const SETTINGS_ENDPOINT = "/feihou_easy_h3/prompt_optimizer_settings";
const ZH = /^zh(?:[-_]|$)/i.test(String(navigator.language || ""));
const API_KEY_MASK = "••••••••••••••••";

function t(zh, en) {
    return ZH ? zh : en;
}

function notify(summary, detail = "", severity = "info") {
    app.extensionManager?.toast?.add?.({ severity, summary, detail, life: 3600 });
}

function numberValue(value, fallback, min, max) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? Math.min(max, Math.max(min, parsed)) : fallback;
}

function modelNames(value) {
    const seen = new Set();
    return (Array.isArray(value) ? value : [])
        .map((item) => String(item?.name ?? item ?? "").trim())
        .filter((item) => item && !seen.has(item) && seen.add(item));
}

function normalizeSettings(value) {
    const source = value && typeof value === "object" ? value : {};
    const providers = (Array.isArray(source.providers) ? source.providers : []).map((item, index) => ({
        id: String(item?.id || `provider_${index + 1}`),
        name: String(item?.name || item?.id || t("自定义 API", "Custom API")),
        description: String(item?.description || ""),
        api_format: ["openai", "gemini", "ollama"].includes(String(item?.api_format || ""))
            ? String(item.api_format)
            : "openai",
        api_url: String(item?.api_url || ""),
        api_key: "",
        api_key_exists: Boolean(item?.api_key_exists),
        api_key_masked: String(item?.api_key_masked || ""),
        llm_models: modelNames(item?.llm_models),
        vlm_models: modelNames(item?.vlm_models),
        llm_model: String(item?.llm_model || ""),
        vlm_model: String(item?.vlm_model || ""),
        temperature: numberValue(item?.temperature, 0.7, 0, 2),
        max_tokens: numberValue(item?.max_tokens, 4096, 1, 50000),
        top_p: numberValue(item?.top_p, 0.9, 0, 1),
        builtin: Boolean(item?.builtin),
    })).filter((item) => item.id);
    const schemes = (Array.isArray(source.schemes) ? source.schemes : []).map((item) => ({
        id: String(item?.id || ""),
        name: String((ZH ? item?.name_zh : item?.name) || item?.name || item?.id || ""),
        name_zh: String(item?.name_zh || item?.name || item?.id || ""),
        prompt: String(item?.prompt || ""),
        editable: Boolean(item?.editable),
    })).filter((item) => item.id);
    return {
        active_provider: String(source.active_provider || providers[0]?.id || "zhipu"),
        providers,
        active_scheme: String(source.active_scheme || "none"),
        schemes,
        read_media: Boolean(source.read_media),
    };
}

async function loadSettings() {
    const response = await api.fetchApi(SETTINGS_ENDPOINT);
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data?.ok) throw new Error(data?.error || `HTTP ${response.status}`);
    return normalizeSettings(data.settings);
}

function settingsPayload(state) {
    return {
        active_provider: state.active_provider,
        active_scheme: state.active_scheme,
        read_media: state.read_media,
        providers: state.providers.map((provider) => {
            const item = {
                id: provider.id,
                name: provider.name,
                description: provider.description,
                api_format: provider.api_format,
                api_url: provider.api_url,
                llm_models: provider.llm_models,
                vlm_models: provider.vlm_models,
                llm_model: provider.llm_model,
                vlm_model: provider.vlm_model,
                temperature: provider.temperature,
                max_tokens: provider.max_tokens,
                top_p: provider.top_p,
                builtin: provider.builtin,
            };
            if (provider.api_key) item.api_key = provider.api_key;
            return item;
        }),
        custom_schemes: state.schemes.filter((scheme) => scheme.editable).map((scheme) => ({
            id: scheme.id,
            name: scheme.name,
            prompt: scheme.prompt,
        })),
    };
}

async function saveSettings(state) {
    const response = await api.fetchApi(SETTINGS_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settingsPayload(state)),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data?.ok) throw new Error(data?.error || `HTTP ${response.status}`);
    window.dispatchEvent(new CustomEvent("feihou-h3-settings-updated"));
    return normalizeSettings(data.settings);
}

function el(tag, className = "", text = "") {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text) element.textContent = text;
    return element;
}

function button(text, className = "") {
    const element = el("button", className, text);
    element.type = "button";
    return element;
}

function input(type, value = "") {
    const element = document.createElement("input");
    element.type = type;
    if (type !== "checkbox" && type !== "radio") element.value = String(value ?? "");
    element.autocomplete = type === "password" ? "new-password" : "off";
    element.spellcheck = false;
    return element;
}

function field(labelText, control) {
    const label = el("label", "fh-inline-field");
    label.append(el("span", "", labelText), control);
    return label;
}

function mergeSavedState(state, saved) {
    state.active_provider = saved.active_provider;
    state.active_scheme = saved.active_scheme;
    state.read_media = saved.read_media;
    for (const provider of state.providers) {
        const updated = saved.providers.find((item) => item.id === provider.id);
        if (!updated) continue;
        provider.api_key_exists = updated.api_key_exists;
        provider.api_key_masked = updated.api_key_masked;
        provider.api_key = "";
    }
}

function modelSection(provider, kind, persist, rerender) {
    const listKey = `${kind}_models`;
    const activeKey = `${kind}_model`;
    const isVlm = kind === "vlm";
    const section = el("section", "fh-inline-model-section");
    const heading = el("div", "fh-inline-section-heading");
    heading.append(el("h4", "", isVlm
        ? t("3️⃣ 添加图像、视频反推的视觉模型 (VLM)", "3️⃣ Vision models (VLM)")
        : t("2️⃣ 添加提示词优化的大语言模型 (LLM)", "2️⃣ Language models (LLM)")));
    const add = button(t("＋ 添加模型", "+ Add model"), "fh-inline-primary");
    heading.append(add);

    const chips = el("div", "fh-inline-model-chips");
    if (!provider[listKey].length) {
        chips.append(el("span", "fh-inline-empty", t("暂未选择模型，点击“添加模型”后才会显示。", "No model selected. Add one to display it here.")));
    }
    for (const name of provider[listKey]) {
        const active = provider[activeKey] === name;
        const chip = button("", `fh-inline-model-chip${active ? " is-active" : ""}`);
        const label = el("span", "", `✣ ${name}`);
        if (active) label.append(el("small", "", t("默认", "Default")));
        const remove = el("span", "fh-inline-chip-remove", "×");
        chip.append(label, remove);
        chip.addEventListener("click", async (event) => {
            if (event.target === remove) {
                event.stopPropagation();
                provider[listKey] = provider[listKey].filter((item) => item !== name);
                if (provider[activeKey] === name) provider[activeKey] = "";
            } else {
                provider[activeKey] = active ? "" : name;
            }
            await persist();
            rerender();
        });
        chips.append(chip);
    }
    section.append(heading, chips);

    add.addEventListener("click", async () => {
        section.querySelector(".fh-inline-model-picker")?.remove();
        add.disabled = true;
        const originalText = add.textContent;
        add.textContent = t("正在获取模型…", "Loading models…");
        try {
            const response = await api.fetchApi(`/feihou_easy_h3/providers/${encodeURIComponent(provider.id)}/models?model_type=${kind}`);
            const data = await response.json().catch(() => ({}));
            if (!response.ok || !data?.ok) throw new Error(data?.error || `HTTP ${response.status}`);
            const available = modelNames(data.models).filter((name) => !provider[listKey].includes(name));
            const picker = el("div", "fh-inline-model-picker");
            const search = input("search", "");
            search.placeholder = t(`搜索${isVlm ? "VLM" : "LLM"}模型…`, `Search ${isVlm ? "VLM" : "LLM"} models…`);
            const list = el("div", "fh-inline-model-picker-list");
            const selected = new Set();
            const renderList = () => {
                list.replaceChildren();
                const keyword = search.value.trim().toLowerCase();
                const filtered = available.filter((name) => !keyword || name.toLowerCase().includes(keyword));
                if (!filtered.length) {
                    list.append(el("div", "fh-inline-empty", t("没有可添加的匹配模型", "No matching models available")));
                    return;
                }
                for (const name of filtered) {
                    const row = el("label", "fh-inline-model-option");
                    const checkbox = input("checkbox");
                    checkbox.checked = selected.has(name);
                    checkbox.addEventListener("change", () => checkbox.checked ? selected.add(name) : selected.delete(name));
                    row.append(checkbox, el("span", "", name));
                    list.append(row);
                }
            };
            search.addEventListener("input", renderList);
            const controls = el("div", "fh-inline-model-picker-actions");
            const cancel = button(t("取消", "Cancel"), "fh-inline-secondary");
            cancel.addEventListener("click", () => picker.remove());
            const confirm = button(t("添加已选模型", "Add selected models"), "fh-inline-primary");
            confirm.addEventListener("click", async () => {
                if (!selected.size) {
                    notify(t("请先勾选要添加的模型", "Select at least one model"), "", "warn");
                    return;
                }
                confirm.disabled = true;
                for (const name of selected) {
                    if (!provider[listKey].includes(name)) provider[listKey].push(name);
                }
                if (!provider[activeKey]) provider[activeKey] = [...selected][0] || "";
                await persist(t("模型已添加", "Models added"));
                rerender();
            });
            controls.append(el("span", "fh-inline-model-count", t(`接口返回 ${available.length} 个可添加模型`, `${available.length} models available`)), cancel, confirm);
            picker.append(search, list, controls);
            section.append(picker);
            renderList();
            search.focus();
        } catch (error) {
            notify(t("获取模型列表失败", "Unable to load model list"), String(error?.message || error), "error");
        } finally {
            add.disabled = false;
            add.textContent = originalText;
        }
    });
    return section;
}

async function mountInlineSettings(root) {
    root.replaceChildren(el("div", "fh-inline-loading", t("正在读取 Easy H3 设置…", "Loading Easy H3 settings…")));
    let state;
    try {
        state = await loadSettings();
    } catch (error) {
        root.replaceChildren(el("div", "fh-inline-error", `${t("设置读取失败", "Unable to load settings")}: ${String(error?.message || error)}`));
        return;
    }

    let selectedProviderId = state.active_provider || state.providers[0]?.id || "";
    let editingSchemeId = "";
    let saveChain = Promise.resolve();
    const status = el("span", "fh-inline-save-status", t("已读取本地配置", "Local settings loaded"));

    const persist = (successText = t("已自动保存", "Saved automatically"), showStatus = true) => {
        if (showStatus) {
            status.className = "fh-inline-save-status is-saving";
            status.textContent = t("正在保存…", "Saving…");
        }
        saveChain = saveChain.catch(() => {}).then(async () => {
            const saved = await saveSettings(state);
            mergeSavedState(state, saved);
            if (showStatus) {
                status.className = "fh-inline-save-status is-saved";
                status.textContent = successText;
            }
            return saved;
        }).catch((error) => {
            status.className = "fh-inline-save-status is-error";
            status.textContent = t("保存失败", "Save failed");
            notify(t("Easy H3 设置保存失败", "Unable to save Easy H3 settings"), String(error?.message || error), "error");
            throw error;
        });
        return saveChain;
    };

    const renderProvider = (container) => {
        container.replaceChildren();
        const tabs = el("nav", "fh-inline-provider-tabs");
        for (const provider of state.providers) {
            const tab = button("", `fh-inline-provider-tab${provider.id === selectedProviderId ? " is-active" : ""}`);
            tab.append(el("strong", "", provider.id === "ollama" ? `🦆 ${provider.name}` : provider.name));
            if (provider.description) tab.append(el("small", "", provider.description));
            tab.addEventListener("click", () => {
                selectedProviderId = provider.id;
                state.active_provider = provider.id;
                renderProvider(container);
            });
            tabs.append(tab);
        }
        const addProvider = button("＋", "fh-inline-provider-add");
        addProvider.title = t("添加自定义 API", "Add custom API");
        addProvider.addEventListener("click", async () => {
            const name = window.prompt(t("请输入自定义 API 名称", "Custom API name"), t("自定义 API", "Custom API"))?.trim();
            if (!name) return;
            const id = `custom_${Date.now().toString(36)}`;
            state.providers.push({
                id, name, description: t("自定义 OpenAI 兼容接口", "Custom OpenAI-compatible endpoint"),
                api_format: "openai", api_url: "", api_key: "", api_key_exists: false, api_key_masked: "",
                llm_models: [], vlm_models: [], llm_model: "", vlm_model: "",
                temperature: 0.7, max_tokens: 4096, top_p: 0.9, builtin: false,
            });
            selectedProviderId = id;
            state.active_provider = id;
            await persist(t("自定义 API 已添加", "Custom API added"));
            renderProvider(container);
        });
        tabs.append(addProvider);
        container.append(tabs);

        const provider = state.providers.find((item) => item.id === selectedProviderId) || state.providers[0];
        if (!provider) return;
        const card = el("section", "fh-inline-provider-card");
        const heading = el("div", "fh-inline-section-heading");
        heading.append(el("h3", "", `1️⃣ ${provider.name} ${t("信息配置", "configuration")}`));
        if (!provider.builtin) {
            const remove = button(t("删除服务", "Delete service"), "fh-inline-danger");
            remove.addEventListener("click", async () => {
                if (!window.confirm(t(`确定删除“${provider.name}”吗？`, `Delete “${provider.name}”?`))) return;
                state.providers = state.providers.filter((item) => item !== provider);
                selectedProviderId = state.providers[0]?.id || "";
                state.active_provider = selectedProviderId;
                await persist(t("自定义 API 已删除", "Custom API deleted"));
                renderProvider(container);
            });
            heading.append(remove);
        }
        card.append(heading);

        const baseUrl = input("text", provider.api_url);
        baseUrl.placeholder = "https://api.example.com/v1";
        let urlTimer = 0;
        const saveUrl = () => {
            window.clearTimeout(urlTimer);
            provider.api_url = baseUrl.value.trim();
            void persist(t("Base URL 已自动保存", "Base URL saved"));
        };
        baseUrl.addEventListener("input", () => {
            provider.api_url = baseUrl.value;
            window.clearTimeout(urlTimer);
            urlTimer = window.setTimeout(saveUrl, 650);
        });
        baseUrl.addEventListener("change", saveUrl);
        baseUrl.addEventListener("blur", saveUrl);

        const apiKey = input("password", provider.api_key_exists ? API_KEY_MASK : "");
        apiKey.placeholder = "API Key";
        apiKey.addEventListener("focus", () => {
            if (provider.api_key_exists && apiKey.value === API_KEY_MASK) apiKey.select();
        });
        let keyTimer = 0;
        let keySaving = false;
        const saveKey = async () => {
            window.clearTimeout(keyTimer);
            const key = apiKey.value.trim();
            if (!key || key === API_KEY_MASK || keySaving) return;
            keySaving = true;
            apiKey.disabled = true;
            provider.api_key = key;
            try {
                await persist("", false);
                apiKey.value = API_KEY_MASK;
                notify(t("API Key 已保存", "API key saved"), provider.name, "success");
            } finally {
                provider.api_key = "";
                apiKey.disabled = false;
                keySaving = false;
            }
        };
        apiKey.addEventListener("input", () => {
            window.clearTimeout(keyTimer);
            if (apiKey.value.trim()) keyTimer = window.setTimeout(() => void saveKey(), 450);
        });
        apiKey.addEventListener("paste", () => window.setTimeout(() => void saveKey(), 0));
        apiKey.addEventListener("change", () => void saveKey());
        apiKey.addEventListener("blur", () => void saveKey());

        const credentials = el("div", "fh-inline-credentials");
        credentials.append(field("Base URL", baseUrl), field("API Key", apiKey));
        card.append(credentials);

        const switches = el("div", "fh-inline-switches");
        const readMedia = input("checkbox");
        readMedia.checked = state.read_media;
        readMedia.addEventListener("change", () => {
            state.read_media = readMedia.checked;
            void persist();
        });
        const readMediaLabel = el("label", "fh-inline-switch");
        readMediaLabel.append(el("span", "", t("允许视觉模型读取节点媒体", "Allow vision model to read node media")), readMedia);
        const advanced = input("checkbox");
        const advancedLabel = el("label", "fh-inline-switch");
        advancedLabel.append(el("span", "", t("启用高级参数", "Advanced parameters")), advanced);
        switches.append(readMediaLabel, advancedLabel);
        card.append(switches);

        const advancedGrid = el("div", "fh-inline-advanced");
        advancedGrid.hidden = true;
        const temperature = input("number", provider.temperature);
        temperature.min = "0"; temperature.max = "2"; temperature.step = "0.05";
        const maxTokens = input("number", provider.max_tokens);
        maxTokens.min = "1"; maxTokens.max = "50000"; maxTokens.step = "1";
        const topP = input("number", provider.top_p);
        topP.min = "0"; topP.max = "1"; topP.step = "0.05";
        const saveAdvanced = () => {
            provider.temperature = numberValue(temperature.value, 0.7, 0, 2);
            provider.max_tokens = numberValue(maxTokens.value, 4096, 1, 50000);
            provider.top_p = numberValue(topP.value, 0.9, 0, 1);
            void persist();
        };
        for (const control of [temperature, maxTokens, topP]) control.addEventListener("change", saveAdvanced);
        advanced.addEventListener("change", () => { advancedGrid.hidden = !advanced.checked; });
        advancedGrid.append(field("Temperature", temperature), field("Max Tokens", maxTokens), field("Top P", topP));
        card.append(advancedGrid);

        const rerender = () => renderProvider(container);
        card.append(modelSection(provider, "llm", persist, rerender));
        card.append(modelSection(provider, "vlm", persist, rerender));
        container.append(card);
    };

    const renderRules = (container) => {
        container.replaceChildren();
        const heading = el("div", "fh-inline-main-heading");
        const title = el("div", "");
        title.append(el("h2", "", t("☷ 提示词优化规则", "☷ Prompt optimization rules")), el("p", "", t("内置规则只读；自定义规则保存后会出现在 Easy H3 节点中。", "Built-in rules are read-only. Custom rules appear in the Easy H3 node after saving.")));
        const add = button(t("＋ 添加提示词优化规则", "+ Add prompt optimization rule"), "fh-inline-primary");
        add.addEventListener("click", () => {
            const id = `custom_${Date.now().toString(36)}`;
            state.schemes.push({ id, name: t("新提示词优化规则", "New optimization rule"), name_zh: t("新提示词优化规则", "New optimization rule"), prompt: "", editable: true });
            editingSchemeId = id;
            renderRules(container);
        });
        heading.append(title, add);
        container.append(heading);

        const table = el("div", "fh-inline-rule-table");
        const tableHeader = el("div", "fh-inline-rule-row is-header");
        for (const label of [t("状态", "Status"), t("规则名称", "Rule name"), t("规则内容", "Rule content"), t("操作", "Actions")]) tableHeader.append(el("div", "", label));
        table.append(tableHeader);
        for (const scheme of state.schemes) {
            const row = el("div", "fh-inline-rule-row");
            const radioCell = el("div", "fh-inline-rule-status");
            const radio = input("radio");
            radio.name = "fh-easy-h3-active-rule";
            radio.checked = state.active_scheme === scheme.id;
            radio.addEventListener("change", () => {
                state.active_scheme = scheme.id;
                void persist(t("当前提示词规则已保存", "Active prompt rule saved"));
            });
            radioCell.append(radio);
            row.append(radioCell, el("div", "fh-inline-rule-name", scheme.name), el("div", "fh-inline-rule-content", scheme.prompt || t("（空规则）", "(empty rule)")));
            const actions = el("div", "fh-inline-rule-actions");
            const edit = button("✎", "fh-inline-icon-button");
            edit.title = scheme.editable ? t("编辑", "Edit") : t("查看内置规则", "View built-in rule");
            edit.addEventListener("click", () => {
                editingSchemeId = editingSchemeId === scheme.id ? "" : scheme.id;
                renderRules(container);
            });
            actions.append(edit);
            if (scheme.editable) {
                const remove = button("♲", "fh-inline-icon-button");
                remove.title = t("删除", "Delete");
                remove.addEventListener("click", async () => {
                    if (!window.confirm(t(`确定删除“${scheme.name}”吗？`, `Delete “${scheme.name}”?`))) return;
                    state.schemes = state.schemes.filter((item) => item !== scheme);
                    if (state.active_scheme === scheme.id) state.active_scheme = "none";
                    editingSchemeId = "";
                    await persist(t("提示词规则已删除", "Prompt rule deleted"));
                    renderRules(container);
                });
                actions.append(remove);
            }
            row.append(actions);
            table.append(row);
        }
        container.append(table);

        const editing = state.schemes.find((item) => item.id === editingSchemeId);
        if (!editing) return;
        const editor = el("section", "fh-inline-rule-editor");
        editor.append(el("h3", "", editing.editable ? t("编辑提示词优化规则", "Edit prompt optimization rule") : t("查看内置提示词优化规则", "View built-in prompt optimization rule")));
        const name = input("text", editing.name);
        name.readOnly = !editing.editable;
        const prompt = document.createElement("textarea");
        prompt.value = editing.prompt;
        prompt.readOnly = !editing.editable;
        prompt.placeholder = t("请输入完整的提示词优化规则", "Enter the complete prompt optimization rule");
        if (editing.editable) {
            name.addEventListener("input", () => { editing.name = name.value; editing.name_zh = name.value; });
            prompt.addEventListener("input", () => { editing.prompt = prompt.value; });
        }
        editor.append(field(t("规则名称", "Rule name"), name), field(t("规则内容", "Rule content"), prompt));
        const controls = el("div", "fh-inline-rule-editor-actions");
        const close = button(t("收起", "Collapse"), "fh-inline-secondary");
        close.addEventListener("click", () => { editingSchemeId = ""; renderRules(container); });
        controls.append(close);
        if (editing.editable) {
            const save = button(t("保存提示词优化规则", "Save prompt optimization rule"), "fh-inline-primary");
            save.addEventListener("click", async () => {
                if (!editing.name.trim() || !editing.prompt.trim()) {
                    notify(t("规则名称和内容不能为空", "Rule name and content are required"), "", "warn");
                    return;
                }
                save.disabled = true;
                try {
                    await persist(t("提示词优化规则已保存", "Prompt optimization rule saved"));
                    editingSchemeId = "";
                    renderRules(container);
                } finally {
                    save.disabled = false;
                }
            });
            controls.append(save);
        }
        editor.append(controls);
        container.append(editor);
    };

    root.replaceChildren();
    const top = el("div", "fh-inline-topbar");
    const title = el("div", "");
    title.append(el("h2", "", t("⚙ API 设置", "⚙ API settings")), el("p", "", t("Base URL 和 API Key 修改后自动保存在本机；API Key 不提供任何默认值。", "Base URL and API key are saved locally. No API key is preconfigured.")));
    top.append(title, status);
    const providerContainer = el("div", "fh-inline-provider-container");
    const divider = el("hr", "fh-inline-divider");
    const rulesContainer = el("div", "fh-inline-rules-container");
    root.append(top, providerContainer, divider, rulesContainer);
    renderProvider(providerContainer);
    renderRules(rulesContainer);
}

function settingsPanel() {
    const root = el("div", "fh-inline-settings");
    void mountInlineSettings(root);
    return root;
}

function installStyles() {
    if (document.getElementById("feihou-easy-h3-inline-settings-style")) return;
    const style = document.createElement("style");
    style.id = "feihou-easy-h3-inline-settings-style";
    style.textContent = `
      .fh-inline-settings{display:grid;gap:18px;width:100%;min-width:0;padding:2px 0 24px;color:var(--input-text,#eee);font:14px system-ui,sans-serif}.fh-inline-loading,.fh-inline-error{padding:26px;border:1px solid #3c3f48;border-radius:10px;background:#1b1c20}.fh-inline-error{color:#ff9fa6}.fh-inline-topbar,.fh-inline-main-heading,.fh-inline-section-heading{display:flex;align-items:center;justify-content:space-between;gap:16px}.fh-inline-topbar h2,.fh-inline-main-heading h2{margin:0 0 4px;font-size:20px}.fh-inline-topbar p,.fh-inline-main-heading p{margin:0;color:#8e929d;font-size:12px}.fh-inline-save-status{flex:none;padding:5px 9px;border-radius:999px;background:#282a30;color:#aeb4c0;font-size:11px}.fh-inline-save-status.is-saving{color:#ffd37a}.fh-inline-save-status.is-saved{color:#78df9d}.fh-inline-save-status.is-error{color:#ff9299}.fh-inline-provider-container,.fh-inline-rules-container{display:grid;gap:12px}.fh-inline-provider-tabs{display:flex;flex-wrap:wrap;align-items:stretch;gap:5px}.fh-inline-provider-tab{display:flex;min-width:115px;max-width:190px;flex-direction:column;align-items:flex-start;gap:2px;padding:10px 14px;border:0;border-radius:5px;background:transparent;color:#eee;cursor:pointer}.fh-inline-provider-tab strong{font-size:15px}.fh-inline-provider-tab small{width:100%;overflow:hidden;color:#8e929d;font-size:11px;text-overflow:ellipsis;white-space:nowrap}.fh-inline-provider-tab.is-active{background:#2f83ef;color:#fff}.fh-inline-provider-tab.is-active small{color:#d8e9ff}.fh-inline-provider-add{width:48px;border:0;background:transparent;color:#fff;font-size:27px;cursor:pointer}.fh-inline-provider-card{display:grid;gap:16px;padding:18px;border:1px solid #373a43;border-radius:10px;background:#191a1e}.fh-inline-section-heading h3,.fh-inline-section-heading h4{margin:0;color:#c4c8d1;font-size:16px}.fh-inline-credentials{display:grid;grid-template-columns:1fr;gap:12px}.fh-inline-field{display:grid;gap:5px;min-width:0;color:#b8bdc8;font-size:12px}.fh-inline-field input,.fh-inline-field textarea{box-sizing:border-box;width:100%;min-width:0;padding:11px 12px;border:1px solid #494d58;border-radius:7px;background:#111216;color:#eee;font:inherit}.fh-inline-field input:focus,.fh-inline-field textarea:focus{border-color:#3d8ef5;outline:none}.fh-inline-field input:disabled{opacity:.7}.fh-inline-switches{display:flex;justify-content:flex-end;flex-wrap:wrap;gap:28px;padding:4px 0 12px;border-bottom:1px solid #34363d}.fh-inline-switch{display:flex;align-items:center;gap:10px}.fh-inline-switch input{width:44px;height:23px;accent-color:#2f83ef}.fh-inline-advanced{display:grid;grid-template-columns:repeat(3,minmax(140px,1fr));gap:12px}.fh-inline-advanced[hidden]{display:none}.fh-inline-model-section{display:grid;gap:10px}.fh-inline-model-chips{display:flex;flex-wrap:wrap;gap:8px;min-height:52px;padding:10px;border-radius:11px;background:#292a2e}.fh-inline-model-chip{display:flex;align-items:center;gap:12px;min-height:36px;padding:0 10px;border:0;border-radius:9px;background:#44454d;color:#eee;cursor:pointer}.fh-inline-model-chip.is-active{background:#3285f2}.fh-inline-model-chip span:first-child{display:flex;align-items:center;gap:7px}.fh-inline-model-chip small{padding:1px 5px;background:rgba(255,255,255,.22);font-size:10px}.fh-inline-chip-remove{font-size:18px;opacity:.85}.fh-inline-empty{align-self:center;color:#8d929c;font-size:12px}.fh-inline-model-picker{display:grid;gap:9px;padding:12px;border:1px solid #434853;border-radius:9px;background:#222329}.fh-inline-model-picker>input{box-sizing:border-box;width:100%;padding:9px 11px;border:1px solid #4a4f5b;border-radius:7px;background:#111216;color:#eee}.fh-inline-model-picker-list{display:grid;gap:2px;max-height:260px;overflow-y:auto}.fh-inline-model-option{display:flex;align-items:center;gap:9px;padding:7px 9px;border-radius:5px;cursor:pointer}.fh-inline-model-option:hover{background:#30333a}.fh-inline-model-option input{width:17px;height:17px;accent-color:#2f83ef}.fh-inline-model-picker-actions{display:flex;align-items:center;justify-content:flex-end;gap:9px}.fh-inline-model-count{margin-right:auto;color:#8d929c;font-size:11px}.fh-inline-primary,.fh-inline-secondary,.fh-inline-danger{min-height:34px;padding:0 13px;border:1px solid rgba(255,255,255,.14);border-radius:7px;background:#2f83ef;color:#fff;cursor:pointer}.fh-inline-secondary{background:#292a2f}.fh-inline-danger{background:#44262b;color:#ffb7bc}.fh-inline-primary:disabled{opacity:.55;cursor:wait}.fh-inline-divider{width:100%;margin:10px 0;border:0;border-top:1px solid #3a3d45}.fh-inline-rule-table{display:grid;border:1px solid #40434c;border-radius:7px;overflow:hidden}.fh-inline-rule-row{display:grid;grid-template-columns:72px 160px minmax(260px,1fr) 92px;min-height:43px;border-top:1px solid #3b3e46}.fh-inline-rule-row:first-child{border-top:0}.fh-inline-rule-row>div{display:flex;align-items:center;min-width:0;padding:8px 11px;border-left:1px solid #3b3e46}.fh-inline-rule-row>div:first-child{border-left:0}.fh-inline-rule-row.is-header{min-height:38px;background:#292a2f;font-weight:700}.fh-inline-rule-status,.fh-inline-rule-actions{justify-content:center}.fh-inline-rule-status input{width:18px;height:18px;accent-color:#20cf78}.fh-inline-rule-name,.fh-inline-rule-content{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.fh-inline-rule-actions{gap:5px}.fh-inline-icon-button{border:0;background:transparent;color:#a9c8ff;font-size:21px;cursor:pointer}.fh-inline-rule-editor{display:grid;gap:12px;padding:16px;border:1px solid #40434c;border-radius:8px;background:#202126}.fh-inline-rule-editor h3{margin:0}.fh-inline-rule-editor textarea{min-height:230px;resize:vertical;line-height:1.5}.fh-inline-rule-editor-actions{display:flex;justify-content:flex-end;gap:10px}@media(max-width:780px){.fh-inline-provider-tab{min-width:100px}.fh-inline-advanced{grid-template-columns:1fr}.fh-inline-rule-row{grid-template-columns:52px 110px minmax(150px,1fr) 72px}.fh-inline-topbar,.fh-inline-main-heading{align-items:flex-start;flex-direction:column}}
    `;
    document.head.append(style);
}

app.registerExtension({
    name: "FeiHouEasyH3.InlinePromptSettings",
    settings: [{
        id: "FeiHouEasyH3.Settings.PromptManagers",
        name: "\u200b",
        category: ["🐵Easy H3", t("API 与提示词设置", "API and prompt settings")],
        tooltip: t("配置 API 接口并管理提示词优化规则", "Configure API providers and prompt optimization rules"),
        type: settingsPanel,
    }],
    setup() {
        installStyles();
    },
});
