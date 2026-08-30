(() => {
  const $ = (id) => document.getElementById(id);
  const state = { config: null, session: null, pollTimer: null };
  const terminal = new Set(["completed", "failed"]);
  const notice = (message, error = false) => { const node = $("notice"); node.textContent = message || ""; node.style.color = error ? "#b42318" : "#637089"; };
  const accessToken = () => state.session?.access_token || "";
  async function localAuth(path, body) {
    const response = await fetch(`/api/auth/${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error_description || data.msg || data.message || "登录失败");
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
  function renderTask(task) {
    $("statusSection").classList.remove("hidden");
    $("status").textContent = `任务 ${task.task_id}：${task.status}`;
    $("result").textContent = task.result ? JSON.stringify(task.result, null, 2) : (task.error ? JSON.stringify(task.error, null, 2) : "等待电脑 Worker 领取任务…");
  }
  async function poll(taskId) {
    try {
      const task = await api(`/api/cloud/tasks/${encodeURIComponent(taskId)}`); renderTask(task);
      if (!terminal.has(task.status)) state.pollTimer = setTimeout(() => poll(taskId), 3000); else notice(task.status === "completed" ? "任务已完成，可以查看结果。" : "任务失败，请查看结果区域中的原因。", task.status === "failed");
    } catch (error) { notice(error.message, true); }
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
    } catch (error) { notice(error.message, true); }
  }
  $("loginButton").addEventListener("click", async () => { try { saveSession(await localAuth("login", { email: $("email").value.trim(), password: $("password").value })); notice("登录成功。"); } catch (error) { notice(error.message, true); } });
  $("signupButton").addEventListener("click", async () => { try { saveSession(await localAuth("register", { email: $("email").value.trim(), password: $("password").value })); notice("注册并登录成功。"); } catch (error) { notice(error.message, true); } });
  $("logoutButton").addEventListener("click", () => { clearTimeout(state.pollTimer); saveSession(null); notice("已退出登录。"); });
  $("submitButton").addEventListener("click", submitTask);
  start();
})();
