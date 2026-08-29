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
  page.on("requestfailed", (request) => diagnostics.requestFailures.push({ url: request.url(), error: request.failure()?.errorText || "unknown" }));
  page.on("response", (response) => {
    if (response.status() >= 400) diagnostics.badResponses.push({ url: response.url(), status: response.status() });
  });
}

async function noOverflow(page) {
  return page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1 && document.body.scrollWidth <= window.innerWidth + 1);
}

async function capture(page, name) {
  if (!outputDir) return;
  fs.mkdirSync(outputDir, { recursive: true });
  await page.screenshot({ path: path.join(outputDir, `${name}.png`), fullPage: true, animations: "disabled" });
}

(async () => {
  if (!executablePath) throw new Error("找不到可用 Chromium");
  const browser = await chromium.launch({ executablePath, headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1365, height: 900 } });
    const page = await context.newPage();
    wire(page);
    await page.goto(baseUrl, { waitUntil: "networkidle" });
    record("正式入口显示双体验模式", await page.locator(".experience-option[data-experience-mode]").count() === 2);
    record("正式入口不再显示内部版本号", await page.locator(".brand__edition").textContent() === "内容工作台" && await page.locator(".brand__version").count() === 0);
    record("正式入口首页无横向溢出", await noOverflow(page));

    await page.goto(`${baseUrl}/static/douyin.html`, { waitUntil: "networkidle" });
    await page.locator("body[data-douyin-ready='true']").waitFor();
    record("正式入口显示账号连接动作", await page.locator("#openAccountDialog").isVisible());
    record("正式入口显示账号诊断区", await page.locator("#accountTitle").isVisible());
    await page.locator("#openAccountDialog").click();
    await page.locator("#accountDialog").waitFor({ state: "visible" });
    record("正式入口以自动连接为主路径", await page.locator("#browserImport.button--primary").isVisible() && await page.locator("#browserChoice").isVisible());
    record("正式入口文件导入默认折叠", !(await page.locator("#creatorDataFile").isVisible()) && (await page.locator(".account-fallback").textContent()).includes("自动连接失败"));
    const browserCapabilities = await page.request.get(`${baseUrl}/api/douyin/browser-capabilities`);
    const browserCapabilitiesBody = await browserCapabilities.json();
    record("正式入口返回浏览器能力列表", browserCapabilities.ok() && Array.isArray(browserCapabilitiesBody.browsers), JSON.stringify(browserCapabilitiesBody));
    await page.locator("#closeAccountDialog").click();
    const connection = await page.request.get(`${baseUrl}/api/douyin/accounts/connection`);
    const connectionBody = await connection.json();
    record("官方 OAuth 未伪报已连接", connection.ok() && connectionBody.official_oauth.available === false);
    const agentStatus = await page.request.get(`${baseUrl}/api/agent/status`);
    const agentBody = await agentStatus.json();
    record("正式入口 Agent 使用已配置 Provider", agentStatus.ok() && agentBody.configured === true && agentBody.paid_api_called === false, JSON.stringify(agentBody));
    await page.locator(".agent-launcher").click();
    await page.locator("#project024AgentPanel").waitFor({ state: "visible" });
    record("正式入口 Agent 小窗可展开", (await page.locator(".agent-panel__status").textContent()).includes("已连接"));
    record("正式桌面抖音页无横向溢出", await noOverflow(page));
    await capture(page, "formal-douyin-agent-desktop");

    await page.goto(`${baseUrl}/static/publish.html`, { waitUntil: "networkidle" });
    await page.locator("body[data-publish-ready='true']").waitFor();
    record("正式发布页解释实验与复盘基线", (await page.locator(".publish-intro").textContent()).includes("内容实验") && (await page.locator(".publish-intro").textContent()).includes("复盘基线"));
    record("正式发布页高级项默认折叠", !(await page.locator("#advancedScores").evaluate((node) => node.open)) && !(await page.locator("#advancedPredictions").evaluate((node) => node.open)));
    record("正式桌面发布页无横向溢出", await noOverflow(page));
    await context.close();

    const mobileContext = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const mobile = await mobileContext.newPage();
    wire(mobile);
    await mobile.goto(`${baseUrl}/static/douyin.html`, { waitUntil: "networkidle" });
    await mobile.locator("body[data-douyin-ready='true']").waitFor();
    await mobile.locator(".agent-launcher").click();
    const box = await mobile.locator("#project024AgentPanel").boundingBox();
    record("正式 390px Agent 小窗在视口内", box && box.x >= 0 && box.y >= 0 && box.x + box.width <= 390 && box.y + box.height <= 844, JSON.stringify(box));
    record("正式 390px 抖音页无横向溢出", await noOverflow(mobile));
    await capture(mobile, "formal-douyin-agent-mobile-390");
    await mobileContext.close();
  } finally {
    await browser.close();
  }

  record("控制台无错误", diagnostics.consoleErrors.length === 0, diagnostics.consoleErrors.join(" | "));
  record("页面无异常", diagnostics.pageErrors.length === 0, diagnostics.pageErrors.join(" | "));
  record("请求无失败", diagnostics.requestFailures.length === 0, JSON.stringify(diagnostics.requestFailures));
  record("响应无 4xx/5xx", diagnostics.badResponses.length === 0, JSON.stringify(diagnostics.badResponses));
  const result = { baseUrl, passed: checks.filter((item) => item.ok).length, failed: checks.filter((item) => !item.ok).length, checks, diagnostics };
  if (outputDir) fs.writeFileSync(path.join(outputDir, "formal-smoke-results.json"), JSON.stringify(result, null, 2), "utf8");
  process.stdout.write(`${JSON.stringify(result)}\n`);
  process.exitCode = result.failed ? 1 : 0;
})().catch((error) => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exitCode = 1;
});
