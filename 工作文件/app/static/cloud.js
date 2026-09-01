(() => {
  const $ = (id) => document.getElementById(id);
  const state = { config: null, session: null, pollTimer: null, lastTask: null };
  const terminal = new Set(["completed", "failed"]);
  const taskStorageKey = "project024-cloud-task-id";
  const statusLabels = { queued: "排队中", processing: "处理中", retryable: "等待重试", completed: "已完成", failed: "失败" };
  const notice = (message, error = false) => { const node = $("notice"); node.textContent = message || ""; node.style.color = error ? "#b42318" : "#637089"; };
  const accessToken = () => state.session?.access_token || "";
  window.project024CloudAuthHeaders = () => accessToken() ? { Authorization: `Bearer ${accessToken()}` } : {};
  window.project024AgentBridge = {
    async getContext() {
      const task = state.lastTask;
      const result = task?.result && typeof task.result === "object" ? task.result : {};
      return {
        draft: "",
        context: {
          task_id: task?.task_id || "",
          task_status: task?.status || "",
          source_url: task?.payload?.url || result?.source_url || "",
          analysis: JSON.stringify(result).slice(0, 30000),
        },
      };
    },
  };
  async function localAuth(path, body) {
    const response = await fetch(`/api/auth/${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || data.error_description || data.msg || data.message || "登录失败");
    return data;
  }
  async function api(path, options = {}) {
    const headers = { Accept: "application/json", ...(options.headers || {}) };
    if (accessToken()) headers.Authorization = `Bearer ${accessToken()}`;
    const response = await fetch(path, { ...options, headers });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || data.message || `请求失败（${response.status}）`);
    return data;
  }
  function renderAuth() {
    const signedIn = Boolean(accessToken());
    $("authSection").classList.toggle("hidden", signedIn);
    $("taskSection").classList.toggle("hidden", !signedIn);
    if (signedIn) $("signedInAs").textContent = state.session.user?.email || "已登录";
  }
  function saveSession(session) {
    state.session = session;
    if (session) sessionStorage.setItem("project024-cloud-session", JSON.stringify(session)); else sessionStorage.removeItem("project024-cloud-session");
    renderAuth();
  }
  function saveTaskId(taskId) {
    if (taskId) sessionStorage.setItem(taskStorageKey, taskId); else sessionStorage.removeItem(taskStorageKey);
  }
  const esc = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
  const present = (value) => value !== null && value !== undefined && value !== "" && (!Array.isArray(value) || value.length > 0);
  const text = (value) => {
    if (!present(value)) return "";
    if (Array.isArray(value)) return value.map(text).filter(Boolean).join("\n");
    if (typeof value === "object") return Object.entries(value).map(([key, item]) => `${key}: ${text(item)}`).filter(Boolean).join("\n");
    return esc(value);
  };
  const card = (title, body, wide = false) => `<section class="result-card${wide ? " result-card--wide" : ""}"><h3>${title}</h3>${body}</section>`;
  const paragraph = (value, cls = "") => present(value) ? `<p class="${cls}">${esc(value)}</p>` : "";
  const list = (values, ordered = false) => Array.isArray(values) && values.length ? `<${ordered ? "ol" : "ul"}>${values.map((item) => `<li>${esc(text(item))}</li>`).join("")}</${ordered ? "ol" : "ul"}>` : "";
  function renderFullReport(analysis) {
    const report = analysis?.report && typeof analysis.report === "object" ? analysis.report : {};
    const quick = report.quick_result || {};
    const distillation = report.distillation || {};
    const script = report.recommended_script || report.content_package?.script || {};
    const shooting = report.shooting_table || report.shooting_plan || {};
    const publishing = report.publishing_package || {};
    const productRelevance = report.product_relevance || {};
    const traffic = report.traffic_assessment || {};
    const audience = report.audience_insights || {};
    const calibration = report.calibration || {};
    const visual = report.visual_analysis || {};
    const transcript = report.material?.transcript_excerpt || report.source?.transcript?.text || "";
    let html = "<div class=\"result-grid\">";
    html += card("内容结论", paragraph(quick.summary || distillation.topic || analysis.message) + (quick.what_happens ? "<h4>内容结构</h4>" + list(quick.what_happens, true) : "") + (quick.why_it_works ? "<h4>为什么有效</h4>" + list(quick.why_it_works) : ""));
    html += card("产品适配", paragraph(productRelevance.status) + paragraph(productRelevance.reason) + (productRelevance.follow_up ? list(productRelevance.follow_up) : ""));
    html += card("可借鉴与原创方向", (distillation.transferable_patterns ? list(distillation.transferable_patterns) : "") + paragraph(quick.transferable ? text(quick.transferable) : "") + paragraph(quick.original_angle, "script-block"));
    html += card("推荐原创脚本", paragraph(script.title, "result-status") + paragraph(script.full_text || script.script_text || script.full_script, "script-block") + paragraph(script.selection_reason));
    const rows = Array.isArray(shooting) ? shooting : (shooting.rows || shooting.segments || []);
    const columns = Array.isArray(shooting.columns) && shooting.columns.length ? shooting.columns : ["time", "visual", "voiceover", "subtitle", "sound"];
    const labels = { time: "时间", visual: "画面", voiceover: "口播", subtitle: "字幕", sound: "声音", purpose: "目的", product_proof: "产品证明" };
    const table = rows.length ? `<div class=\"shooting-table-wrap\"><table class=\"shooting-table\"><thead><tr>${columns.map((key) => `<th>${labels[key] || key}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${columns.map((key) => `<td>${esc(text(row?.[key]))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : paragraph("暂无可执行拍摄表");
    html += card("拍摄执行表", table, true);
    html += card("发布配套", (publishing.titles ? "<h4>标题</h4>" + list(publishing.titles) : "") + paragraph(publishing.post_copy || publishing.caption, "script-block") + paragraph(publishing.cta || publishing.call_to_action) + (publishing.tags ? "<h4>标签</h4>" + list(publishing.tags) : "") + (publishing.comment_replies ? "<h4>评论回复</h4>" + list(publishing.comment_replies) : ""));
    html += card("受众与流量判断", paragraph(text(audience)) + paragraph(text(traffic)));
    html += card("验证与复盘计划", paragraph(text(calibration)));
    html += card("完整口播全文", paragraph(transcript, "script-block"), true);
    const scene = visual.scene_structure || {};
    const ocr = visual.ocr || {};
    html += card("画面与镜头摘要", paragraph(`代表帧：${visual.frame_count || 0} 张`) + paragraph(`镜头结构：${scene.pace || scene.status || "未返回"}`) + paragraph(`画面文字：${ocr.status === "completed" ? `${ocr.block_count || 0} 个文字块` : "未返回"}`));
    html += "</div>";
    return html;
  }
  function renderTask(task) {
    state.lastTask = task;
    saveTaskId(task.task_id);
    $("statusSection").classList.remove("hidden");
    $("status").textContent = statusLabels[task.status] || task.status;
    $("result").innerHTML = formatTaskResult(task);
  }
  function formatTaskResult(task) {
    if (task.error) return `<p>${esc(task.error.message || "请稍后重试。")}</p>`;
    if (!task.result) return "<p>等待电脑 Worker 领取任务…</p>";
    const result = task.result || {};
    const lines = [];
    if (result.message) lines.push(`<p>${esc(result.message)}</p>`);
    if (result.platform) lines.push(`<p class="result-status">平台：${esc(result.platform === "douyin" ? "抖音" : result.platform)}</p>`);
    if (result.status === "completed") lines.push("<p>采集已完成</p>");
    if (result.cache_hit) lines.push("<p>本次使用了已有结果，未重复下载。</p>");
    const analysis = result.analysis && typeof result.analysis === "object" ? result.analysis : null;
    if (analysis) {
      const report = analysis.report && typeof analysis.report === "object" ? analysis.report : {};
      const quick = report.quick_result && typeof report.quick_result === "object" ? report.quick_result : {};
      const recommended = report.recommended_script && typeof report.recommended_script === "object" ? report.recommended_script : {};
      const packagedScript = report.content_package?.script && typeof report.content_package.script === "object" ? report.content_package.script : {};
      lines.push(renderFullReport(analysis));
    }
    if (!lines.length) lines.push("<p>任务已完成，暂无可展示的内容。</p>");
    return lines.join("");
  }
  function renderHistory(tasks) {
    const list = $("historyList");
    list.textContent = "";
    $("historySection").classList.toggle("hidden", !tasks.length);
    tasks.forEach((task) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "history-item";
      const title = document.createElement("strong");
      title.textContent = `${statusLabels[task.status] || task.status} · ${new Date(task.created_at * 1000).toLocaleString("zh-CN")}`;
      const detail = document.createElement("span");
      detail.textContent = task.payload?.url || "历史分析任务";
      button.append(title, detail);
      button.addEventListener("click", () => { clearTimeout(state.pollTimer); poll(task.task_id); });
      list.appendChild(button);
    });
  }
  async function loadHistory() {
    if (!accessToken()) return;
    const recent = await api("/api/cloud/tasks?limit=20");
    renderHistory(Array.isArray(recent.tasks) ? recent.tasks : []);
  }
  async function poll(taskId) {
    try {
      const task = await api(`/api/cloud/tasks/${encodeURIComponent(taskId)}`); renderTask(task);
      if (!terminal.has(task.status)) state.pollTimer = setTimeout(() => poll(taskId), 3000); else {
        notice(task.status === "completed" ? "任务已完成，可以查看结果。" : "任务失败，请查看结果区域中的原因。", task.status === "failed");
        loadHistory().catch(() => {});
      }
    } catch (error) {
      if (error.message === "任务不存在") saveTaskId(null);
      notice(error.message, true);
    }
  }
  async function restoreLatestTask() {
    const savedTaskId = sessionStorage.getItem(taskStorageKey);
    if (savedTaskId) return poll(savedTaskId);
    try {
      const recent = await api("/api/cloud/tasks?limit=1");
      const latest = Array.isArray(recent.tasks) ? recent.tasks[0] : null;
      if (latest?.task_id) {
        saveTaskId(latest.task_id);
        poll(latest.task_id);
      }
    } catch (error) {
      notice(error.message, true);
    }
  }
  async function submitTask() {
    const url = $("url").value.trim(); if (!url) return notice("请先粘贴公开链接。", true);
    $("submitButton").disabled = true;
    try {
      if (!$("paidConsent").checked) throw new Error("请先确认允许使用内容模型生成完整结果；这可能产生 API 费用。");
      const task = await api("/api/cloud/tasks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ idempotency_key: `mobile-${Date.now()}-${crypto.randomUUID?.() || Math.random().toString(16).slice(2)}`, payload: { url, item_limit: Number($("itemLimit").value || 1), product_context: $("productContext").value.trim() || null, analysis_mode: "full", analysis_strategy: "multi_agent" } }) });
      notice("已提交，正在等待电脑 Worker。请不要关闭此页面。"); renderTask(task); clearTimeout(state.pollTimer); poll(task.task_id);
    } catch (error) { notice(error.message, true); } finally { $("submitButton").disabled = false; }
  }
  async function start() {
    try {
      state.config = await api("/api/cloud/config");
      const saved = sessionStorage.getItem("project024-cloud-session"); if (saved) saveSession(JSON.parse(saved)); else renderAuth();
      if (state.config.mode !== "domestic") notice("当前不是国内控制面模式。", true);
      if (accessToken()) {
        await loadHistory();
        restoreLatestTask();
      }
    } catch (error) { notice(error.message, true); }
  }
  function credentials() {
    const email = $("email").value.trim();
    const password = $("password").value;
    if (!email) throw new Error("请先输入邮箱。");
    if (!email.includes("@")) throw new Error("邮箱格式不正确。");
    if (password.length < 8) throw new Error("密码至少需要 8 位。");
    return { email, password };
  }
  $("loginButton").addEventListener("click", async () => { try { saveSession(await localAuth("login", credentials())); notice("登录成功。"); } catch (error) { notice(error.message, true); } });
  $("signupButton").addEventListener("click", async () => { try { saveSession(await localAuth("register", credentials())); notice("注册并登录成功。"); } catch (error) { notice(error.message, true); } });
  $("logoutButton").addEventListener("click", () => { clearTimeout(state.pollTimer); saveTaskId(null); saveSession(null); notice("已退出登录。"); });
  $("submitButton").addEventListener("click", submitTask);
  start();
})();
