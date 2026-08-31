(() => {
  const $ = (id) => document.getElementById(id);
  const state = { config: null, session: null, pollTimer: null, lastTask: null };
  const terminal = new Set(["completed", "failed"]);
  const taskStorageKey = "project024-cloud-task-id";
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
  function renderTask(task) {
    state.lastTask = task;
    saveTaskId(task.task_id);
    $("statusSection").classList.remove("hidden");
    $("status").textContent = `任务 ${task.task_id}：${task.status}`;
    $("result").textContent = formatTaskResult(task);
  }
  function formatTaskResult(task) {
    if (task.error) return `任务处理失败\n${task.error.message || "请稍后重试。"}`;
    if (!task.result) return "等待电脑 Worker 领取任务…";
    const result = task.result || {};
    const lines = [];
    if (result.message) lines.push(result.message);
    if (result.platform) lines.push(`平台：${result.platform === "douyin" ? "抖音" : result.platform}`);
    if (result.status === "completed") lines.push("采集已完成");
    if (result.cache_hit) lines.push("本次使用了已有结果，未重复下载。");
    const analysis = result.analysis && typeof result.analysis === "object" ? result.analysis : null;
    if (analysis) {
      if (analysis.title) lines.push(`\n标题建议：${analysis.title}`);
      if (analysis.summary) lines.push(`\n内容总结：${analysis.summary}`);
      if (analysis.conclusion) lines.push(`\n结论：${analysis.conclusion}`);
      if (Array.isArray(analysis.next_actions) && analysis.next_actions.length) {
        lines.push(`\n下一步：\n${analysis.next_actions.map((item) => `- ${item}`).join("\n")}`);
      }
    }
    const missing = Array.isArray(result.missing) ? result.missing.filter(Boolean) : [];
    if (missing.length) lines.push(`\n当前未包含：${missing.join("、")}`);
    if (!lines.length) lines.push("任务已完成，暂无可展示的摘要。");
    return lines.join("\n");
  }
  async function poll(taskId) {
    try {
      const task = await api(`/api/cloud/tasks/${encodeURIComponent(taskId)}`); renderTask(task);
      if (!terminal.has(task.status)) state.pollTimer = setTimeout(() => poll(taskId), 3000); else notice(task.status === "completed" ? "任务已完成，可以查看结果。" : "任务失败，请查看结果区域中的原因。", task.status === "failed");
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
      const task = await api("/api/cloud/tasks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ idempotency_key: `mobile-${Date.now()}-${crypto.randomUUID?.() || Math.random().toString(16).slice(2)}`, payload: { url, item_limit: Number($("itemLimit").value || 1), analysis_mode: "quick", analysis_strategy: "multi_agent" } }) });
      notice("已提交，正在等待电脑 Worker。请不要关闭此页面。"); renderTask(task); clearTimeout(state.pollTimer); poll(task.task_id);
    } catch (error) { notice(error.message, true); } finally { $("submitButton").disabled = false; }
  }
  async function start() {
    try {
      state.config = await api("/api/cloud/config");
      const saved = sessionStorage.getItem("project024-cloud-session"); if (saved) saveSession(JSON.parse(saved)); else renderAuth();
      if (state.config.mode !== "domestic") notice("当前不是国内控制面模式。", true);
      if (accessToken()) restoreLatestTask();
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
