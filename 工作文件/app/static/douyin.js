"use strict";

(function () {
  const STATUS_LABELS = { idea: "待整理", draft: "脚本草稿", ready: "可进实验" };
  let topics = [];
  let accounts = [];
  let activeAccount = null;
  let activeAccountAnalysis = null;
  let activeTopic = null;
  let accountAnalysisRequestId = 0;
  let browserCapabilities = [];
  let downloadWatchTimer = null;
  let downloadWatchSince = 0;

  function el(id) { return document.getElementById(id); }
  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = String(text);
    return node;
  }
  function setMessage(message, tone) {
    const node = el("douyinMessage");
    node.textContent = message || "";
    node.className = `douyin-message${tone ? ` douyin-message--${tone}` : ""}`;
  }
  function setAccountMessage(message, tone) {
    const node = el("accountMessage");
    node.textContent = message || "";
    node.className = `douyin-message${tone ? ` douyin-message--${tone}` : ""}`;
  }
  function setAccountFormMessage(message, tone) {
    const node = el("accountFormMessage");
    node.textContent = message || "";
    node.className = `publish-message${tone ? ` publish-message--${tone}` : ""}`;
  }
  async function api(url, options) {
    const response = await fetch(url, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
    return payload;
  }
  function formatDate(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "时间未知" : new Intl.DateTimeFormat("zh-CN", {
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"
    }).format(date);
  }
  function updateStats() {
    el("statAll").textContent = String(topics.length);
    ["idea", "draft", "ready"].forEach((status) => {
      const target = status === "idea" ? "statIdea" : status === "draft" ? "statDraft" : "statReady";
      el(target).textContent = String(topics.filter((topic) => topic.status === status).length);
    });
  }
  function filteredTopics() {
    const status = el("statusFilter").value;
    const query = el("topicSearch").value.trim().toLowerCase();
    return topics.filter((topic) => {
      if (status !== "all" && topic.status !== status) return false;
      return !query || `${topic.id} ${topic.title} ${topic.content_summary || ""}`.toLowerCase().includes(query);
    });
  }
  function openPublish(topic) {
    const draft = {
      title: topic.title,
      platform: "抖音",
      source_topic_id: topic.id,
      source_url: topic.source_url,
      analysis_ref: topic.analysis_ref || topic.source_url,
      content_summary: topic.content_summary || "",
      hypothesis: topic.hypothesis || "记录发布前判断，并在 72 小时后用真实指标复盘。"
    };
    try {
      sessionStorage.setItem("project024_publish_draft", JSON.stringify(draft));
    } catch (_error) {
      setMessage("浏览器未能保存预填内容，请稍后重试。", "error");
      return;
    }
    window.location.assign("/static/publish.html");
  }

  const METRIC_LABELS = {
    views: ["播放量", "次"],
    views_per_post: ["单条平均播放", "次"],
    five_second_completion: ["5 秒完播率", "%"],
    two_second_bounce: ["2 秒跳出率", "%"],
    cover_click_rate: ["封面点击率", "%"],
    avg_watch_seconds: ["平均播放时长", "秒"],
    interaction_rate: ["互动率", "%"],
    follower_net: ["粉丝净增", "人"]
  };

  function formatMetric(value, unit) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    const formatted = new Intl.NumberFormat("zh-CN", {
      maximumFractionDigits: unit === "%" || unit === "秒" ? 1 : 0
    }).format(number);
    return `${formatted}${unit || ""}`;
  }

  function renderAccountAnalysis(payload) {
    const host = el("accountOverview");
    host.textContent = "";
    const account = payload.account || activeAccount;
    if (!account) {
      const empty = make("div", "douyin-account__empty");
      empty.append(make("h3", "", "先连接账号，再看改进重点"));
      empty.append(make("p", "", "系统会优先自动取得账号数据；连接不可用时仍可使用文件兜底。"));
      host.appendChild(empty);
      return;
    }

    const meta = make("div", "account-overview__meta");
    meta.append(make("strong", "", account.display_name));
    meta.append(make("span", "", account.douyin_id ? `抖音号 ${account.douyin_id}` : "本地运营档案"));
    if (payload.latest_import?.imported_at) {
      meta.append(make("span", "", `数据更新于 ${formatDate(payload.latest_import.imported_at)}`));
    }
    host.appendChild(meta);

    const analysis = payload.analysis;
    if (!analysis) {
      const empty = make("div", "douyin-account__empty");
      empty.append(make("h3", "", "账号已建立，等待创作者中心数据"));
      empty.append(make("p", "", "点击“同步数据”后选择自动连接；连接不可用时再使用文件兜底。"));
      host.appendChild(empty);
      return;
    }

    const metrics = make("div", "account-metrics");
    const current = analysis.current || {};
    const preferred = [
      "views_per_post", "five_second_completion", "two_second_bounce",
      "avg_watch_seconds", "cover_click_rate", "interaction_rate", "follower_net", "views"
    ];
    const selected = preferred.filter((key) => current[key] != null).slice(0, 4);
    while (selected.length < 4) selected.push("");
    selected.forEach((key) => {
      const item = make("div", "account-metric");
      const label = METRIC_LABELS[key] || ["未提供", ""];
      item.append(make("span", "", label[0]), make("strong", "", key ? formatMetric(current[key], label[1]) : "—"));
      metrics.appendChild(item);
    });
    host.appendChild(metrics);

    const recommendations = make("section", "account-recommendations");
    const headingRow = make("div", "account-recommendations__heading");
    headingRow.appendChild(make("h3", "", "下一轮改进重点"));
    const discuss = make("button", "button button--secondary", "和 Agent 讨论运营");
    discuss.type = "button";
    discuss.addEventListener("click", () => {
      window.dispatchEvent(new CustomEvent("project024:agent-open", {
        detail: {
          mode: "strategy",
          draft: account.strategy_notes || "",
          context: { account, account_analysis: analysis, latest_import: payload.latest_import }
        }
      }));
    });
    headingRow.appendChild(discuss);
    recommendations.appendChild(headingRow);
    const list = make("ol");
    (analysis.recommendations || []).forEach((recommendation) => {
      const item = make("li");
      item.appendChild(make("strong", "", recommendation.area || "改进项"));
      const detail = make("div");
      detail.appendChild(make("p", "", `${recommendation.finding || ""} ${recommendation.action || ""}`.trim()));
      if (recommendation.evidence) detail.appendChild(make("small", "", recommendation.evidence));
      item.appendChild(detail);
      list.appendChild(item);
    });
    recommendations.appendChild(list);
    recommendations.appendChild(make("p", "account-evidence-boundary", analysis.evidence_boundary || ""));
    host.appendChild(recommendations);
  }

  async function loadAccountAnalysis(accountId) {
    const requestId = ++accountAnalysisRequestId;
    if (!accountId) {
      renderAccountAnalysis({ account: null });
      return;
    }
    setAccountMessage("正在读取账号数据…", "info");
    try {
      const payload = await api(`/api/douyin/accounts/${encodeURIComponent(accountId)}/analysis`);
      if (requestId !== accountAnalysisRequestId || el("accountSelect").value !== accountId) return;
      activeAccount = payload.account;
      activeAccountAnalysis = payload.analysis || null;
      renderAccountAnalysis(payload);
      setAccountMessage(payload.message || "", payload.status === "completed" ? "success" : "info");
    } catch (error) {
      if (requestId !== accountAnalysisRequestId || el("accountSelect").value !== accountId) return;
      setAccountMessage(`账号诊断读取失败：${error.message}`, "error");
    }
  }

  async function loadAccounts(preferredId) {
    ++accountAnalysisRequestId;
    const payload = await api("/api/douyin/accounts");
    accounts = Array.isArray(payload.accounts) ? payload.accounts : [];
    const select = el("accountSelect");
    select.textContent = "";
    if (!accounts.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "尚未连接账号";
      select.appendChild(option);
      activeAccount = null;
      activeAccountAnalysis = null;
      el("updateAccountData").disabled = true;
      renderAccountAnalysis({ account: null });
      return;
    }
    accounts.forEach((account) => {
      const option = document.createElement("option");
      option.value = account.id;
      option.textContent = account.display_name;
      select.appendChild(option);
    });
    const selectedId = accounts.some((item) => item.id === preferredId)
      ? preferredId : (activeAccount?.id && accounts.some((item) => item.id === activeAccount.id) ? activeAccount.id : accounts[0].id);
    select.value = selectedId;
    activeAccount = accounts.find((item) => item.id === selectedId) || accounts[0];
    el("updateAccountData").disabled = false;
    await loadAccountAnalysis(activeAccount.id);
  }

  function openAccountEditor() {
    el("accountDisplayName").value = activeAccount?.display_name || "";
    el("accountDouyinId").value = activeAccount?.douyin_id || "";
    el("accountStrategy").value = activeAccount?.strategy_notes || "";
    el("creatorDataFile").value = "";
    updateBrowserImportState();
    updateAccountSaveLabel();
    setAccountFormMessage("", "");
    if (activeAccount) armDownloadWatcher(activeAccount.id);
    const dialog = el("accountDialog");
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
    el("accountDisplayName").focus();
  }

  function stopDownloadWatcher() {
    if (downloadWatchTimer) window.clearInterval(downloadWatchTimer);
    downloadWatchTimer = null;
  }

  function armDownloadWatcher(accountId) {
    stopDownloadWatcher();
    // Include a recently completed export from before the dialog opened, so
    // a user who already clicked "导出数据" does not have to repeat it.
    downloadWatchSince = Math.max(0, Date.now() - 30 * 60 * 1000);
    downloadWatchTimer = window.setInterval(async () => {
      if (!activeAccount || activeAccount.id !== accountId) {
        stopDownloadWatcher();
        return;
      }
      try {
        const payload = await api(`/api/douyin/accounts/${encodeURIComponent(accountId)}/download-import`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ since_epoch_ms: downloadWatchSince })
        });
        downloadWatchSince = Number(payload.modified_epoch_ms || Date.now()) + 1;
        await loadAccounts(accountId);
        setAccountFormMessage(payload.message || "已自动识别并导入下载文件。", "success");
        stopDownloadWatcher();
      } catch (error) {
        if (!String(error.message || "").startsWith("尚未发现新的")) {
          setAccountFormMessage(`自动识别下载失败：${error.message}`, "error");
        }
      }
    }, 2000);
  }

  function updateBrowserImportState() {
    const button = el("browserImport");
    if (!button) return;
    button.disabled = !activeAccount && !el("accountDisplayName").value.trim();
  }

  function updateAccountSaveLabel() {
    const button = el("saveAccount");
    if (!button || button.disabled) return;
    button.textContent = el("creatorDataFile").files[0] ? "保存并导入文件" : "保存账号设置";
  }

  async function loadBrowserCapabilities() {
    try {
      const payload = await api("/api/douyin/browser-capabilities");
      browserCapabilities = Array.isArray(payload.browsers) ? payload.browsers : [];
      const select = el("browserChoice");
      select.textContent = "";
      const automatic = document.createElement("option");
      automatic.value = "";
      automatic.textContent = browserCapabilities.length ? "自动选择可用浏览器" : "未发现可用浏览器";
      select.appendChild(automatic);
      browserCapabilities.forEach((browser) => {
        const option = document.createElement("option");
        option.value = browser.id;
        option.textContent = `${browser.label}${browser.profile_available ? "（检测到本机配置）" : ""}`;
        select.appendChild(option);
      });
    } catch (error) {
      browserCapabilities = [];
      el("browserChoice").textContent = "";
      const option = document.createElement("option");
      option.value = "";
      option.textContent = `浏览器检测失败：${error.message}`;
      el("browserChoice").appendChild(option);
    }
  }

  async function importFromBrowser() {
    const button = el("browserImport");
    button.disabled = true;
    button.textContent = "正在连接…";
    setAccountFormMessage("正在打开安全登录窗口；完成扫码后，系统会自动寻找并同步创作者中心数据。", "info");
    try {
      if (!activeAccount) {
        const displayName = el("accountDisplayName").value.trim();
        if (!displayName) throw new Error("请先填写账号名称。");
        activeAccount = await api("/api/douyin/accounts", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            display_name: displayName,
            douyin_id: el("accountDouyinId").value.trim() || null,
            strategy_notes: el("accountStrategy").value.trim()
          })
        });
      }
      const payload = await api(`/api/douyin/accounts/${encodeURIComponent(activeAccount.id)}/browser-import`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          browser_id: el("browserChoice").value || null,
          profile_mode: "existing",
          timeout_seconds: 180
        })
      });
      await loadAccounts(activeAccount.id);
      const successMessage = payload.message || "账号数据已自动同步。";
      setAccountMessage(successMessage, "success");
      setAccountFormMessage(successMessage, "success");
      closeAccountEditor();
    } catch (error) {
      setAccountFormMessage(`自动连接失败：${error.message}`, "error");
    } finally {
      button.disabled = !activeAccount && !el("accountDisplayName").value.trim();
      button.textContent = "自动连接账号";
    }
  }

  function closeAccountEditor() {
    stopDownloadWatcher();
    const dialog = el("accountDialog");
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
  }

  async function decodeCreatorFile(file) {
    const buffer = await file.arrayBuffer();
    const utf8 = new TextDecoder("utf-8").decode(buffer);
    if (!utf8.includes("\ufffd")) return utf8;
    try {
      const gb18030 = new TextDecoder("gb18030").decode(buffer);
      return (gb18030.match(/\ufffd/g) || []).length < (utf8.match(/\ufffd/g) || []).length ? gb18030 : utf8;
    } catch (_error) {
      return utf8;
    }
  }

  function arrayBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    for (let offset = 0; offset < bytes.length; offset += 32768) {
      binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
    }
    return btoa(binary);
  }

  async function saveAccountForm(event) {
    event.preventDefault();
    const displayName = el("accountDisplayName").value.trim();
    if (!displayName) {
      setAccountFormMessage("请填写账号名称。", "error");
      el("accountDisplayName").focus();
      return;
    }
    const button = el("saveAccount");
    button.disabled = true;
      button.textContent = "正在保存…";
    setAccountFormMessage("正在保存账号档案…", "info");
    try {
      const body = {
        display_name: displayName,
        douyin_id: el("accountDouyinId").value.trim() || null,
        strategy_notes: el("accountStrategy").value.trim()
      };
      const account = activeAccount
        ? await api(`/api/douyin/accounts/${encodeURIComponent(activeAccount.id)}`, {
          method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
        })
        : await api("/api/douyin/accounts", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
        });
      activeAccount = account;
      armDownloadWatcher(account.id);
      const file = el("creatorDataFile").files[0];
      if (file) {
        setAccountFormMessage("正在读取创作者中心数据…", "info");
        const isWorkbook = /\.xlsx$/i.test(file.name);
        const importPayload = isWorkbook
          ? { filename: file.name, file_base64: arrayBufferToBase64(await file.arrayBuffer()) }
          : { filename: file.name, csv_text: await decodeCreatorFile(file) };
        await api(`/api/douyin/accounts/${encodeURIComponent(account.id)}/imports`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(importPayload)
        });
      }
      await loadAccounts(account.id);
      setAccountFormMessage(
        file ? "账号与文件数据已保存。" : "账号设置已保存；可以继续使用上方自动连接。",
        "success"
      );
      if (file) closeAccountEditor();
    } catch (error) {
      setAccountFormMessage(`保存失败：${error.message}`, "error");
    } finally {
      button.disabled = false;
      updateAccountSaveLabel();
    }
  }

  async function loadConnectionStatus() {
    try {
      const payload = await api("/api/douyin/accounts/connection");
      const official = payload.official_oauth || {};
      const host = el("officialConnectionStatus");
      const detail = host.querySelector("span");
      detail.textContent = official.available ? "官方授权已可用。" : (official.reason || "官方授权尚未接通。");
      if (!official.available && typeof official.application_guide === "string") {
        const link = document.createElement("a");
        link.href = official.application_guide;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "查看官方接入条件";
        detail.append(" ", link);
      }
    } catch (error) {
      el("officialConnectionStatus").querySelector("span").textContent = `接入状态读取失败：${error.message}`;
    }
  }
  async function updateStatus(topic, select) {
    select.disabled = true;
    setMessage("正在更新选题状态…", "info");
    try {
      const updated = await api(`/api/douyin/topics/${encodeURIComponent(topic.id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: select.value })
      });
      topics = topics.map((item) => item.id === updated.id ? updated : item);
      setMessage(`已更新 ${updated.id}：${STATUS_LABELS[updated.status]}`, "success");
      updateStats();
      renderTopics();
    } catch (error) {
      select.disabled = false;
      select.value = topic.status;
      setMessage(`更新失败：${error.message}`, "error");
    }
  }
  function topicRow(topic) {
    const article = make("article", "douyin-topic");
    article.dataset.topicId = topic.id;

    const identity = make("div", "douyin-topic__identity");
    identity.appendChild(make("code", "douyin-topic__id", topic.id));
    identity.appendChild(make("h3", "douyin-topic__title", topic.title));
    identity.appendChild(make("p", "douyin-topic__meta", `保存于 ${formatDate(topic.created_at)}`));

    const summary = make("p", "douyin-topic__summary", topic.content_summary || "尚无脚本摘要，请回到内容分析页补齐。 ");

    const source = document.createElement("a");
    source.className = "douyin-topic__source";
    source.href = topic.source_url;
    source.target = "_blank";
    source.rel = "noopener noreferrer";
    source.textContent = "查看来源";

    const status = document.createElement("select");
    status.className = "douyin-topic__status";
    status.setAttribute("aria-label", `${topic.title}的状态`);
    Object.entries(STATUS_LABELS).forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      option.selected = topic.status === value;
      status.appendChild(option);
    });
    status.addEventListener("change", () => updateStatus(topic, status));

    const action = make("button", "button button--primary douyin-topic__action", "带入发布实验");
    action.type = "button";
    action.addEventListener("click", () => openPublish(topic));

    const discuss = make("button", "button button--secondary douyin-topic__agent", "讨论脚本");
    discuss.type = "button";
    discuss.addEventListener("click", () => {
      activeTopic = topic;
      window.dispatchEvent(new CustomEvent("project024:agent-open", {
        detail: {
          mode: "script",
          draft: topic.content_summary || "",
          context: {
            topic_id: topic.id,
            title: topic.title,
            hypothesis: topic.hypothesis,
            source_url: topic.source_url
          }
        }
      }));
    });

    const controls = make("div", "douyin-topic__controls");
    controls.append(status, source, discuss, action);
    article.append(identity, summary, controls);
    return article;
  }
  function renderTopics() {
    const container = el("topicList");
    container.textContent = "";
    const list = filteredTopics();
    if (!list.length) {
      const empty = make("div", "douyin-empty");
      empty.appendChild(make("h3", "", topics.length ? "没有符合筛选条件的选题" : "还没有抖音选题"));
      empty.appendChild(make("p", "", topics.length ? "调整搜索或状态筛选。" : "先分析一个抖音链接，再把结果保存到这里。"));
      if (!topics.length) {
        const link = make("a", "button button--primary", "分析一个抖音链接");
        link.href = "/";
        empty.appendChild(link);
      }
      container.appendChild(empty);
      return;
    }
    const header = make("div", "douyin-topic douyin-topic--header");
    header.append(
      make("span", "", "选题与内容编号"),
      make("span", "", "脚本摘要"),
      make("span", "", "状态与下一步")
    );
    container.appendChild(header);
    list.forEach((topic) => container.appendChild(topicRow(topic)));
  }
  async function loadTopics() {
    el("reloadTopics").disabled = true;
    setMessage("正在读取抖音选题…", "info");
    try {
      const payload = await api("/api/douyin/topics");
      topics = Array.isArray(payload.topics) ? payload.topics : [];
      updateStats();
      renderTopics();
      setMessage(topics.length ? `已读取 ${topics.length} 条抖音选题。` : "", "success");
    } catch (error) {
      topics = [];
      updateStats();
      el("topicList").textContent = "";
      el("topicList").appendChild(make("p", "publish-empty publish-empty--error", `读取失败：${error.message}`));
      setMessage("抖音选题暂时无法读取。", "error");
    } finally {
      el("reloadTopics").disabled = false;
    }
  }

  window.project024AgentBridge = {
    getContext(mode) {
      if (mode === "script") {
        return {
          draft: activeTopic?.content_summary || "",
          context: activeTopic ? {
            topic_id: activeTopic.id,
            title: activeTopic.title,
            hypothesis: activeTopic.hypothesis,
            source_url: activeTopic.source_url
          } : { note: "请先从选题列表选择一条脚本。" }
        };
      }
      return {
        draft: activeAccount?.strategy_notes || "",
        context: {
          account: activeAccount,
          account_analysis: activeAccountAnalysis,
          selected_topic: activeTopic
        }
      };
    },
    async applyDraft(mode, value) {
      if (mode === "script") {
        if (!activeTopic) throw new Error("请先在选题列表点“讨论脚本”。");
        const updated = await api(`/api/douyin/topics/${encodeURIComponent(activeTopic.id)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content_summary: value })
        });
        activeTopic = updated;
        topics = topics.map((item) => item.id === updated.id ? updated : item);
        renderTopics();
        setMessage(`已把 Agent 草稿保存到 ${updated.id}。`, "success");
        return { persisted: true, message: `已保存到抖音选题 ${updated.id}。` };
      }
      if (!activeAccount) throw new Error("请先连接一个抖音账号。 ");
      activeAccount = await api(`/api/douyin/accounts/${encodeURIComponent(activeAccount.id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ strategy_notes: value })
      });
      accounts = accounts.map((item) => item.id === activeAccount.id ? activeAccount : item);
      setAccountMessage("Agent 运营策略已保存到账号档案。", "success");
      return { persisted: true, message: "已保存到当前抖音账号档案。" };
    }
  };

  el("reloadTopics").addEventListener("click", loadTopics);
  el("topicSearch").addEventListener("input", renderTopics);
  el("statusFilter").addEventListener("change", renderTopics);
  el("openAccountDialog").addEventListener("click", openAccountEditor);
  el("updateAccountData").addEventListener("click", openAccountEditor);
  el("closeAccountDialog").addEventListener("click", closeAccountEditor);
  el("accountForm").addEventListener("submit", saveAccountForm);
  el("browserImport").addEventListener("click", importFromBrowser);
  el("accountDisplayName").addEventListener("input", updateBrowserImportState);
  el("creatorDataFile").addEventListener("change", updateAccountSaveLabel);
  el("accountSelect").addEventListener("change", async (event) => {
    activeAccount = accounts.find((item) => item.id === event.target.value) || null;
    activeAccountAnalysis = null;
    await loadAccountAnalysis(activeAccount?.id || "");
  });
  el("accountDialog").addEventListener("click", (event) => {
    if (event.target === el("accountDialog")) closeAccountEditor();
  });

  Promise.all([loadTopics(), loadAccounts(), loadConnectionStatus(), loadBrowserCapabilities()]).finally(() => {
    document.body.dataset.douyinReady = "true";
  });
})();
