"use strict";

const fs = require("fs");
const path = require("path");
const { chromium } = require("G:\\Tools\\gstack\\node_modules\\playwright-core");

const baseUrl = (process.argv[2] || "http://127.0.0.1:8792").replace(/\/$/, "");
const outputDir = process.argv[3] || "";
const executableCandidates = [
  process.env.CHROMIUM_EXECUTABLE,
  "C:\\Users\\Administrator\\AppData\\Local\\ms-playwright\\chromium-1228\\chrome-win64\\chrome.exe",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"
].filter(Boolean);
const executablePath = executableCandidates.find((candidate) => fs.existsSync(candidate));
const checks = [];
const diagnostics = { consoleErrors: [], pageErrors: [], requestFailures: [], badResponses: [] };

function record(name, ok, detail = "") {
  checks.push({ name, ok: Boolean(ok), detail: String(detail || "") });
}

function wire(page) {
  page.on("console", (message) => {
    if (message.type() === "error") diagnostics.consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => diagnostics.pageErrors.push(error.message));
  page.on("requestfailed", (request) => diagnostics.requestFailures.push({
    url: request.url(), error: request.failure()?.errorText || "unknown"
  }));
  page.on("response", (response) => {
    if (response.status() >= 400) diagnostics.badResponses.push({ url: response.url(), status: response.status() });
  });
}

async function installAgentMocks(page, requests) {
  await page.route("**/api/agent/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "ready",
        provider: "mock",
        model: "mock-agent",
        configured: true,
        paid_api_called: false,
        call_count: 0
      })
    });
  });
  await page.route("**/api/agent/chat", async (route) => {
    const body = JSON.parse(route.request().postData() || "{}");
    requests.push(body);
    const preservesManualEdit = body.message.includes("等待期间");
    if (preservesManualEdit) {
      await new Promise((resolve) => setTimeout(resolve, 350));
    }
    const updatedText = preservesManualEdit
      ? "Agent 生成但尚未应用的策略。"
      : body.mode === "script"
      ? "新脚本：先给结论，再用一条真实证据说明原因，最后给出行动。"
      : "新策略：本轮只测试开头钩子，发布后按同一窗口记录播放、完播和互动。";
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        reply: "判断完成，已按指令生成完整替换稿。",
        updated_text: updatedText,
        next_actions: ["核对第一句话"]
      })
    });
  });
}

async function clickAndWait(page, message) {
  await page.locator(".agent-panel__input").fill(message);
  const response = page.waitForResponse((item) =>
    item.request().method() === "POST" && new URL(item.url()).pathname === "/api/agent/chat"
  );
  await page.getByRole("button", { name: "发送并执行" }).click();
  await response;
  await page.locator("[data-agent-execution]").last().waitFor();
}

(async () => {
  if (!executablePath) throw new Error("找不到可用 Chromium");
  const browser = await chromium.launch({ executablePath, headless: true });
  const requests = [];
  try {
    const context = await browser.newContext({ viewport: { width: 1365, height: 900 } });
    const page = await context.newPage();
    wire(page);
    await installAgentMocks(page, requests);
    await page.goto(`${baseUrl}/`, { waitUntil: "networkidle" });
    await page.locator(".agent-launcher").click();
    await page.locator(".agent-panel__draft").fill("原始脚本");
    await clickAndWait(page, "改成直接给结果的开头");
    const analysisState = await page.evaluate(() => ({
      bridgeDraft: window.project024AgentBridge.getContext("script").draft,
      pageDraft: document.querySelector("#fullContentLayer")?.textContent || "",
      status: document.querySelector(".agent-panel__status")?.textContent || "",
      execution: Array.from(document.querySelectorAll("[data-agent-execution]")).at(-1)?.textContent || "",
      preview: document.querySelector(".agent-message__preview")?.textContent || "",
      draftUpdated: document.querySelector(".agent-panel__draft")?.classList.contains("agent-panel__draft--updated") || false
    }));
    record("分析页脚本指令被接收", requests[0]?.message === "改成直接给结果的开头", JSON.stringify(requests[0]));
    record("分析页脚本自动写回页面", analysisState.bridgeDraft.includes("新脚本") && analysisState.pageDraft.includes("新脚本"), JSON.stringify(analysisState));
    record("分析页明确显示判断和执行结果", analysisState.status.includes("已判断并写回") && analysisState.execution.includes("已写回") && analysisState.preview.includes("新稿开头") && analysisState.draftUpdated, JSON.stringify(analysisState));

    await page.goto(`${baseUrl}/static/publish.html`, { waitUntil: "networkidle" });
    await page.locator("#cTitle").fill("测试实验");
    await page.locator("#cPlatform").fill("抖音");
    await page.locator("#cHypothesis").fill("原始策略");
    await page.locator(".agent-launcher").click();
    await clickAndWait(page, "只测试首句，其他变量保持不变");
    const publishState = await page.evaluate(() => ({
      hypothesis: document.querySelector("#cHypothesis")?.value || "",
      status: document.querySelector(".agent-panel__status")?.textContent || "",
      execution: Array.from(document.querySelectorAll("[data-agent-execution]")).at(-1)?.textContent || ""
    }));
    record("发布页运营指令被接收", requests[1]?.mode === "strategy", JSON.stringify(requests[1]));
    record("发布页运营策略自动写回表单", publishState.hypothesis.includes("新策略"), JSON.stringify(publishState));
    record("发布页明确提示提交后才持久化", publishState.execution.includes("发布实验表单") && publishState.execution.includes("提交登记"), JSON.stringify(publishState));

    await page.locator(".agent-panel__input").fill("等待期间如果我手动修改，不要覆盖");
    const delayedResponse = page.waitForResponse((item) =>
      item.request().method() === "POST" && new URL(item.url()).pathname === "/api/agent/chat"
    );
    await page.getByRole("button", { name: "发送并执行" }).click();
    await page.locator("#cHypothesis").fill("用户等待期间的人工修改");
    await delayedResponse;
    await page.locator("[data-agent-execution]").last().waitFor();
    const conflictState = await page.evaluate(() => ({
      hypothesis: document.querySelector("#cHypothesis")?.value || "",
      agentDraft: document.querySelector(".agent-panel__draft")?.value || "",
      status: document.querySelector(".agent-panel__status")?.textContent || "",
      execution: Array.from(document.querySelectorAll("[data-agent-execution]")).at(-1)?.textContent || ""
    }));
    record(
      "等待期间的人工修改不会被 Agent 覆盖",
      conflictState.hypothesis === "用户等待期间的人工修改"
        && conflictState.agentDraft.includes("尚未应用")
        && conflictState.status.includes("已生成，待应用")
        && conflictState.execution.includes("已保留你的修改"),
      JSON.stringify(conflictState)
    );
    await context.close();
  } finally {
    await browser.close();
  }

  record("控制台无错误", diagnostics.consoleErrors.length === 0, diagnostics.consoleErrors.join(" | "));
  record("页面无异常", diagnostics.pageErrors.length === 0, diagnostics.pageErrors.join(" | "));
  record("请求无失败", diagnostics.requestFailures.length === 0, JSON.stringify(diagnostics.requestFailures));
  record("响应无 4xx/5xx", diagnostics.badResponses.length === 0, JSON.stringify(diagnostics.badResponses));
  const result = {
    baseUrl,
    requestCount: requests.length,
    passed: checks.filter((item) => item.ok).length,
    failed: checks.filter((item) => !item.ok).length,
    checks,
    diagnostics
  };
  if (outputDir) {
    fs.mkdirSync(outputDir, { recursive: true });
    fs.writeFileSync(path.join(outputDir, "browser-agent-execution-results.json"), JSON.stringify(result, null, 2), "utf8");
  }
  process.stdout.write(`${JSON.stringify(result)}\n`);
  process.exitCode = result.failed ? 1 : 0;
})().catch((error) => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exitCode = 1;
});
