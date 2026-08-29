"use strict";

(function () {
  const page = document.body.classList.contains("douyin-page")
    ? "douyin"
    : document.body.classList.contains("publish-page") ? "publish" : "analysis";
  const storageKey = `project024_agent_panel_${page}`;
  const state = {
    open: false,
    configured: false,
    mode: page === "analysis" ? "script" : "strategy",
    drafts: { script: "", strategy: "" },
    contexts: { script: {}, strategy: {} },
    history: { script: [], strategy: [] }
  };

  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = String(text);
    return node;
  }

  function charCount(value) {
    return Array.from(String(value || "")).length;
  }

  function previewText(value) {
    const compact = String(value || "").replace(/\s+/g, " ").trim();
    return compact.length > 96 ? `${compact.slice(0, 96)}…` : compact;
  }

  function contextIdentity(mode, value) {
    const context = value && typeof value === "object" && value.context && typeof value.context === "object"
      ? value.context
      : (value && typeof value === "object" ? value : {});
    if (mode === "strategy") {
      return JSON.stringify({
        account_id: context.account?.id || context.account_id || "",
        topic_id: context.selected_topic?.id || context.topic_id || "",
        source_url: context.source_url || "",
        title: context.title || ""
      });
    }
    return JSON.stringify({
      topic_id: context.topic_id || context.selected_topic?.id || "",
      source_url: context.source_url || "",
      title: context.title || ""
    });
  }

  function restore() {
    try {
      const saved = JSON.parse(sessionStorage.getItem(storageKey) || "null");
      if (!saved || typeof saved !== "object") return;
      ["script", "strategy"].forEach((mode) => {
        if (typeof saved.drafts?.[mode] === "string") state.drafts[mode] = saved.drafts[mode];
        if (Array.isArray(saved.history?.[mode])) state.history[mode] = saved.history[mode].slice(-12);
      });
      if (["script", "strategy"].includes(saved.mode)) state.mode = saved.mode;
    } catch (_error) {
      // Session persistence is optional; the page remains usable without it.
    }
  }

  function persist() {
    try {
      sessionStorage.setItem(storageKey, JSON.stringify({
        mode: state.mode,
        drafts: state.drafts,
        history: state.history
      }));
    } catch (_error) {
      // Session persistence is optional.
    }
  }

  restore();

  const launcher = make("button", "agent-launcher", "Agent");
  launcher.type = "button";
  launcher.setAttribute("aria-expanded", "false");
  launcher.setAttribute("aria-controls", "project024AgentPanel");
  launcher.title = "打开运营 Agent";

  const panel = make("aside", "agent-panel");
  panel.id = "project024AgentPanel";
  panel.hidden = true;
  panel.setAttribute("aria-label", "运营 Agent 对话");

  const header = make("header", "agent-panel__header");
  const heading = make("div");
  heading.append(make("strong", "", "运营 Agent"), make("span", "agent-panel__status", "检查连接中"));
  const close = make("button", "agent-panel__close", "×");
  close.type = "button";
  close.title = "关闭";
  close.setAttribute("aria-label", "关闭运营 Agent");
  header.append(heading, close);

  const dashboard = make("section", "agent-panel__dashboard");
  dashboard.setAttribute("aria-label", "运营 Agent 工作台摘要");
  const hero = make("div", "agent-panel__hero");
  const character = make("div", "agent-character");
  character.classList.add("agent-character--sprite");
  character.setAttribute("aria-hidden", "true");
  const sprite = make("span", "agent-character__sprite");
  sprite.style.backgroundImage = `url("${location.protocol === "file:" ? "./assets/characters/agent-character-female-premium-actions-strip.png" : "/static/assets/characters/agent-character-female-premium-actions-strip.png"}")`;
  character.appendChild(sprite);
  character.append(
    make("span", "agent-character__antenna"),
    make("span", "agent-character__head"),
    make("span", "agent-character__body"),
    make("span", "agent-character__arm agent-character__arm--left"),
    make("span", "agent-character__arm agent-character__arm--right"),
    make("span", "agent-character__foot agent-character__foot--left"),
    make("span", "agent-character__foot agent-character__foot--right")
  );
  const heroCopy = make("div", "agent-panel__hero-copy");
  heroCopy.append(make("span", "agent-panel__hero-kicker", "AGENT WORKBENCH"));
  heroCopy.append(make("strong", "agent-panel__hero-title", "内容协作中"));
  heroCopy.append(make("small", "agent-panel__hero-detail", "先判断，再写回；发布仍由你确认"));
  hero.append(character, heroCopy);
  const metricGrid = make("div", "agent-panel__metric-grid");
  [["草稿字数", "0", "当前可编辑稿"], ["上下文", "待加载", "页面证据"], ["工作模式", "改脚本", "当前 Agent 入口"]].forEach(([label, value, detail], index) => {
    const item = make("div", "agent-panel__metric");
    item.dataset.agentMetric = ["draft", "context", "mode"][index];
    item.append(make("span", "agent-panel__metric-label", label), make("strong", "agent-panel__metric-value", value), make("small", "agent-panel__metric-detail", detail));
    metricGrid.appendChild(item);
  });
  const trend = make("div", "agent-panel__trend");
  const trendHeading = make("div", "agent-panel__trend-heading");
  trendHeading.append(make("span", "", "最近 7 次编辑"), make("strong", "agent-panel__trend-total", "0 次"));
  const bars = make("div", "agent-panel__trend-bars");
  [32, 48, 38, 66, 54, 78, 62].forEach((height) => {
    const bar = make("i", "");
    bar.style.height = `${height}%`;
    bars.appendChild(bar);
  });
  trend.append(trendHeading, bars);
  const quick = make("div", "agent-panel__quick");
  quick.append(make("span", "agent-panel__quick-label", "快捷指令"));
  [["提炼开头", "先给结果，重写前两句并保留事实"], ["补风险提示", "检查发布风险，列出需要人工确认的地方"], ["生成标题", "给出 3 个短标题，分别突出结果、冲突和场景"]].forEach(([label, command]) => {
    const button = make("button", "agent-panel__quick-button", label);
    button.type = "button";
    button.dataset.agentQuick = command;
    quick.appendChild(button);
  });
  dashboard.append(hero, metricGrid, trend, quick);

  const modes = make("div", "agent-panel__modes");
  modes.setAttribute("role", "tablist");
  const modeButtons = {};
  [["script", "改脚本"], ["strategy", "聊运营"]].forEach(([value, label]) => {
    const button = make("button", "agent-panel__mode", label);
    button.type = "button";
    button.dataset.agentMode = value;
    button.setAttribute("role", "tab");
    modeButtons[value] = button;
    modes.appendChild(button);
  });

  const draftLabel = make("label", "agent-panel__draft-label");
  draftLabel.append(
    make("span", "agent-panel__draft-title", "当前可编辑稿"),
    make("small", "agent-panel__draft-hint", "下方输入指令，发送后 Agent 会先判断，再自动写回；不会自动发布。")
  );
  const draft = make("textarea", "agent-panel__draft");
  draft.rows = 6;
  draft.maxLength = 20000;
  draft.placeholder = "选择页面内容，或直接在这里开始一份草稿";
  draftLabel.appendChild(draft);

  const messages = make("div", "agent-panel__messages");
  messages.setAttribute("aria-live", "polite");
  const empty = make("p", "agent-panel__empty", "说出你想改什么，Agent 会保留当前草稿继续迭代。");
  messages.appendChild(empty);

  const composer = make("form", "agent-panel__composer");
  const inputLabel = make("label", "agent-panel__input-label", "本轮指令（发送并执行）");
  const input = make("textarea", "agent-panel__input");
  input.rows = 2;
  input.maxLength = 4000;
  input.placeholder = "例如：开头太慢，改成先给结果";
  inputLabel.appendChild(input);
  const composerActions = make("div", "agent-panel__composer-actions");
  const apply = make("button", "button button--secondary", "只应用草稿");
  apply.type = "button";
  const send = make("button", "button button--primary", "发送并执行");
  send.type = "submit";
  composerActions.append(apply, send);
  const notice = make("p", "agent-panel__notice", "发送会调用一次已配置的内容模型并产生一次 API 用量：先给判断，再写回当前草稿；不会自动发布或提交。");
  composer.append(inputLabel, input, composerActions, notice);
  panel.append(header, dashboard, modes, draftLabel, messages, composer);
  document.body.append(launcher, panel);

  function bridge() {
    return window.project024AgentBridge && typeof window.project024AgentBridge === "object"
      ? window.project024AgentBridge : null;
  }

  function setStatus(text, tone) {
    const node = heading.querySelector(".agent-panel__status");
    node.textContent = text;
    node.dataset.tone = tone || "";
  }

  function setBusy(busy) {
    send.disabled = busy || !state.configured;
    apply.disabled = busy;
    input.disabled = busy;
    draft.disabled = busy;
    Object.values(modeButtons).forEach((button) => { button.disabled = busy; });
    send.textContent = busy ? "处理中…" : "发送并执行";
    panel.setAttribute("aria-busy", busy ? "true" : "false");
  }

  function renderDashboard(mode) {
    const context = state.contexts[mode] || {};
    const draftValue = state.drafts[mode] || draft.value || "";
    const hasContext = Boolean(
      context.source_url || context.topic_id || context.title || context.account || context.selected_topic || context.platform
    );
    const title = context.title || context.selected_topic?.title || context.content_summary || "内容协作中";
    const metricDraft = dashboard.querySelector('[data-agent-metric="draft"] .agent-panel__metric-value');
    const metricContext = dashboard.querySelector('[data-agent-metric="context"] .agent-panel__metric-value');
    const metricMode = dashboard.querySelector('[data-agent-metric="mode"] .agent-panel__metric-value');
    const heroTitle = dashboard.querySelector(".agent-panel__hero-title");
    const heroDetail = dashboard.querySelector(".agent-panel__hero-detail");
    const trendTotal = dashboard.querySelector(".agent-panel__trend-total");
    if (metricDraft) metricDraft.textContent = `${charCount(draftValue).toLocaleString("zh-CN")} 字`;
    if (metricContext) metricContext.textContent = hasContext ? "已就绪" : "待加载";
    if (metricMode) metricMode.textContent = mode === "strategy" ? "聊运营" : "改脚本";
    if (heroTitle) heroTitle.textContent = title === "内容协作中" ? title : previewText(title).slice(0, 20);
    if (heroDetail) heroDetail.textContent = hasContext ? "已读取当前页面证据，等待你的下一步" : "先判断，再写回；发布仍由你确认";
    if (trendTotal) trendTotal.textContent = `${state.history[mode].filter((item) => item.role === "user").length} 次`;
    dashboard.classList.toggle("is-context-ready", hasContext);
  }

  function markDraftUpdated() {
    draft.classList.remove("agent-panel__draft--updated");
    void draft.offsetWidth;
    draft.classList.add("agent-panel__draft--updated");
    window.setTimeout(() => draft.classList.remove("agent-panel__draft--updated"), 1400);
  }

  function renderHistory() {
    messages.textContent = "";
    const items = state.history[state.mode];
    if (!items.length) {
      messages.appendChild(make("p", "agent-panel__empty", "说出你想改什么，Agent 会保留当前草稿继续迭代。"));
      return;
    }
    items.forEach((item) => {
      const row = make("div", `agent-message agent-message--${item.role}`);
      row.appendChild(make("span", "agent-message__role", item.role === "user" ? "你" : "Agent"));
      row.appendChild(make("p", "", item.content));
      if (Array.isArray(item.actions) && item.actions.length) {
        const list = make("ul", "agent-message__actions");
        item.actions.forEach((action) => list.appendChild(make("li", "", action)));
        row.appendChild(list);
      }
      if (item.execution) {
        const execution = make("div", "agent-message__execution", item.execution);
        execution.dataset.agentExecution = "true";
        row.appendChild(execution);
      }
      if (item.preview) row.appendChild(make("div", "agent-message__preview", item.preview));
      messages.appendChild(row);
    });
    messages.scrollTop = messages.scrollHeight;
  }

  async function readBridgeContext(mode, override) {
    if (override && typeof override === "object") {
      if (typeof override.draft === "string") state.drafts[mode] = override.draft;
      if (override.context && typeof override.context === "object") state.contexts[mode] = override.context;
      return;
    }
    const currentBridge = bridge();
    if (!currentBridge || typeof currentBridge.getContext !== "function") return;
    const value = await currentBridge.getContext(mode);
    if (!value || typeof value !== "object") return;
    if (!state.drafts[mode] && typeof value.draft === "string") state.drafts[mode] = value.draft;
    state.contexts[mode] = value.context && typeof value.context === "object" ? value.context : {};
  }

  async function refreshBridgeContext(mode) {
    const currentBridge = bridge();
    if (!currentBridge || typeof currentBridge.getContext !== "function") {
      return {
        draft: null,
        context: state.contexts[mode] || {}
      };
    }
    const value = await currentBridge.getContext(mode);
    if (value && typeof value === "object") {
      state.contexts[mode] = value.context && typeof value.context === "object" ? value.context : {};
    }
    return {
      draft: value && typeof value.draft === "string" ? value.draft : null,
      context: state.contexts[mode] || {}
    };
  }

  async function selectMode(mode, override) {
    if (!["script", "strategy"].includes(mode)) return;
    state.drafts[state.mode] = draft.value;
    state.mode = mode;
    await readBridgeContext(mode, override);
    draft.value = state.drafts[mode];
    renderDashboard(mode);
    Object.entries(modeButtons).forEach(([value, button]) => {
      const active = value === mode;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-selected", active ? "true" : "false");
    });
    renderHistory();
    persist();
  }

  async function openPanel(detail) {
    state.open = true;
    panel.hidden = false;
    launcher.setAttribute("aria-expanded", "true");
    document.body.classList.add("agent-panel-open");
    const mode = detail?.mode || state.mode;
    await selectMode(mode, detail);
    input.focus({ preventScroll: true });
  }

  function closePanel() {
    state.open = false;
    panel.hidden = true;
    launcher.setAttribute("aria-expanded", "false");
    document.body.classList.remove("agent-panel-open");
    launcher.focus();
  }

  async function sendMessage(event) {
    event.preventDefault();
    const message = input.value.trim();
    if (!message || !state.configured) return;
    const requestMode = state.mode;
    let requestPageDraft = null;
    try {
      const snapshot = await refreshBridgeContext(requestMode);
      requestPageDraft = snapshot.draft;
      renderDashboard(requestMode);
    } catch (_error) {
      // Keep the last known context; the model call still reports its result.
    }
    const requestDraft = draft.value;
    const requestContext = { ...(state.contexts[requestMode] || {}) };
    const requestIdentity = contextIdentity(requestMode, requestContext);
    state.drafts[requestMode] = requestDraft;
    const previous = state.history[requestMode].slice(-10);
    state.history[requestMode].push({
      role: "user",
      content: message,
      execution: "已收到指令，正在判断…"
    });
    input.value = "";
    renderHistory();
    setBusy(true);
    setStatus("已收到指令，正在判断", "working");
    try {
      const response = await fetch("/api/agent/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          mode: requestMode,
          page,
          draft: requestDraft,
          context: requestContext,
          history: previous.map((item) => ({ role: item.role, content: item.content })),
          confirm_paid: true
        })
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
      const updatedText = typeof payload.updated_text === "string"
        ? payload.updated_text.trim()
        : requestDraft.trim();
      if (requestDraft.trim() && !updatedText) {
        throw new Error("Agent 返回空稿，未执行写回。");
      }
      const changed = updatedText !== requestDraft.trim();
      state.drafts[requestMode] = updatedText;
      renderDashboard(requestMode);
      if (state.mode === requestMode) {
        draft.value = updatedText;
        if (changed) markDraftUpdated();
      }

      let execution = changed
        ? `判断结果：已识别为修改请求，准备写回（${charCount(requestDraft)} 字 → ${charCount(updatedText)} 字）。`
        : "判断结果：本轮保留原稿，未执行改写。";
      if (changed) {
        try {
          const currentBridge = bridge();
          if (state.mode !== requestMode) throw new Error("编辑模式已变化，请确认后再应用。");
          if (!currentBridge || typeof currentBridge.applyDraft !== "function") {
            throw new Error("当前页面暂不支持自动写回");
          }
          const liveContext = await currentBridge.getContext(requestMode);
          if (contextIdentity(requestMode, liveContext) !== requestIdentity) {
            throw new Error("页面内容已变化，请确认后再应用。");
          }
          if (requestPageDraft !== null
              && String(liveContext?.draft ?? "") !== requestPageDraft) {
            throw new Error("页面草稿已在等待期间变化，已保留你的修改；请确认后再应用。");
          }
          const applied = await currentBridge.applyDraft(requestMode, updatedText);
          execution = `执行结果：${applied?.message || "已写回当前页面，尚未自动发布。"}（${charCount(requestDraft)} 字 → ${charCount(updatedText)} 字）。`;
          setStatus("已判断并写回", "ready");
        } catch (error) {
          execution = `执行结果：已生成新稿，但未自动写回：${error.message || "请点击“只应用草稿”重试。"}`;
          setStatus("已生成，待应用", "working");
        }
      } else {
        setStatus("已判断，未改稿", "ready");
      }
      const latestUser = state.history[requestMode][state.history[requestMode].length - 1];
      if (latestUser && latestUser.role === "user") latestUser.execution = "指令已接收。";
      state.history[requestMode].push({
        role: "assistant",
        content: payload.reply || "已更新当前草稿。",
        actions: Array.isArray(payload.next_actions) ? payload.next_actions : [],
        execution,
        preview: changed ? `新稿开头：${previewText(updatedText)}` : ""
      });
      state.history[requestMode] = state.history[requestMode].slice(-12);
      if (state.mode === requestMode) renderHistory();
      persist();
    } catch (error) {
      const latestUser = state.history[requestMode][state.history[requestMode].length - 1];
      if (latestUser && latestUser.role === "user") latestUser.execution = "指令已接收。";
      state.history[requestMode].push({
        role: "assistant",
        content: `本次没有修改：${error.message}`,
        execution: "执行结果：未写回当前页面。"
      });
      setStatus("请求失败", "error");
      if (state.mode === requestMode) renderHistory();
    } finally {
      setBusy(false);
    }
  }

  async function applyDraft() {
    const mode = state.mode;
    const value = draft.value;
    state.drafts[mode] = value;
    apply.disabled = true;
    try {
      await refreshBridgeContext(mode);
      const currentBridge = bridge();
      if (!currentBridge || typeof currentBridge.applyDraft !== "function") {
        setStatus("草稿已保留，页面暂不支持写回", "ready");
        persist();
        return;
      }
      const expectedIdentity = contextIdentity(mode, state.contexts[mode]);
      const liveContext = await currentBridge.getContext(mode);
      if (contextIdentity(mode, liveContext) !== expectedIdentity) {
        throw new Error("页面内容已变化，请重新确认当前草稿。");
      }
      const applied = await currentBridge.applyDraft(mode, value);
      markDraftUpdated();
      setStatus(applied?.message || "已应用到页面", "ready");
      persist();
    } catch (error) {
      setStatus(error.message || "应用失败", "error");
    } finally {
      apply.disabled = false;
    }
  }

  launcher.addEventListener("click", () => state.open ? closePanel() : openPanel());
  close.addEventListener("click", closePanel);
  Object.values(modeButtons).forEach((button) => {
    button.addEventListener("click", () => selectMode(button.dataset.agentMode));
  });
  draft.addEventListener("input", () => {
    state.drafts[state.mode] = draft.value;
    persist();
  });
  composer.addEventListener("submit", sendMessage);
  apply.addEventListener("click", applyDraft);
  quick.querySelectorAll("[data-agent-quick]").forEach((button) => {
    button.addEventListener("click", () => {
      input.value = button.dataset.agentQuick || "";
      input.focus();
    });
  });
  document.querySelectorAll("[data-agent-action]").forEach((button) => {
    button.addEventListener("click", () => {
      const action = button.dataset.agentAction;
      if (action === "focus-url") document.querySelector("#urlInput")?.focus();
      if (action === "open-supplement") document.querySelector("#supplementTrigger")?.click();
      if (action === "open-agent") openPanel();
    });
  });
  window.addEventListener("project024:agent-open", (event) => openPanel(event.detail || {}));
  window.Project024AgentPanel = { open: openPanel, close: closePanel };

  selectMode(state.mode).catch(() => {});
  fetch("/api/agent/status")
    .then((response) => response.json())
    .then((payload) => {
      state.configured = Boolean(payload.configured);
      setStatus(state.configured ? "已连接" : "未配置", state.configured ? "ready" : "error");
      setBusy(false);
    })
    .catch(() => {
      state.configured = false;
      setStatus("连接失败", "error");
      setBusy(false);
    });
})();
