"use strict";

const fs = require("fs");
const path = require("path");
const { chromium } = require("G:\\Tools\\gstack\\node_modules\\playwright-core");

const baseUrl = (process.env.BASE_URL || process.argv[2] || "http://127.0.0.1:8794").replace(/\/$/, "");
const outputDir = process.env.BROWSER_OUTPUT_DIR || "";
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

function wireDiagnostics(page) {
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

async function capture(page, name) {
  if (!outputDir) return;
  fs.mkdirSync(outputDir, { recursive: true });
  await page.screenshot({ path: path.join(outputDir, `${name}.png`), fullPage: true, animations: "disabled" });
}

async function layout(page) {
  return page.evaluate(() => ({
    viewport: window.innerWidth,
    rootWidth: document.documentElement.scrollWidth,
    bodyWidth: document.body.scrollWidth
  }));
}

(async () => {
  if (!executablePath) throw new Error("找不到可用 Chromium");
  const browser = await chromium.launch({ executablePath, headless: true });
  let topicId = "";
  try {
    const context = await browser.newContext({ viewport: { width: 1365, height: 900 } });
    const page = await context.newPage();
    wireDiagnostics(page);
    await page.goto(baseUrl, { waitUntil: "networkidle" });

    await page.locator("#demoButton").click();
    await page.locator("#supplementDetails").evaluate((node) => { node.open = true; });
    await page.locator("#transcriptInput").fill("抖音运营板块浏览器验收字幕：前三秒先给结果，再展示三个可核验步骤。");
    await page.locator("#analyzeButton").click();
    await page.locator("#reportLayout").waitFor({ state: "visible", timeout: 15000 });
    await page.locator("#saveDouyinTopicButton").waitFor({ state: "visible", timeout: 10000 });
    record("抖音结果显示保存选题动作", await page.locator("#saveDouyinTopicButton").isVisible());

    const saveResponsePromise = page.waitForResponse((response) =>
      response.request().method() === "POST" && new URL(response.url()).pathname === "/api/douyin/topics"
    );
    await page.locator("#saveDouyinTopicButton").click();
    const saveResponse = await saveResponsePromise;
    const saved = await saveResponse.json();
    topicId = saved.id;
    record("保存抖音选题返回 201", saveResponse.status() === 201, String(saveResponse.status()));
    record("生成稳定抖音内容编号", /^dy_[A-Za-z0-9_]+$/.test(topicId), topicId);
    record("保存按钮显示内容编号", (await page.locator("#saveDouyinTopicButton").textContent()).includes(topicId));

    await page.locator("#saveDouyinTopicButton").click();
    await page.waitForURL("**/static/douyin.html");
    await page.locator("body[data-douyin-ready='true']").waitFor();
    const row = page.locator(`[data-topic-id="${topicId}"]`);
    await row.waitFor();
    record("抖音运营页显示新选题", (await row.locator(".douyin-topic__id").textContent()) === topicId);
    record("抖音运营页保留脚本摘要", (await row.locator(".douyin-topic__summary").textContent()).trim().length > 10);

    const updateResponsePromise = page.waitForResponse((response) =>
      response.request().method() === "PATCH" && new URL(response.url()).pathname.endsWith(`/api/douyin/topics/${topicId}`)
    );
    await row.locator(".douyin-topic__status").selectOption("ready");
    const updateResponse = await updateResponsePromise;
    record("选题状态可更新", updateResponse.status() === 200 && (await updateResponse.json()).status === "ready");
    await page.locator(`[data-topic-id="${topicId}"]`).waitFor();
    record("可进实验计数更新", Number(await page.locator("#statReady").textContent()) >= 1);
    const desktopLayout = await layout(page);
    record("桌面抖音页无横向溢出", desktopLayout.rootWidth <= desktopLayout.viewport + 1 && desktopLayout.bodyWidth <= desktopLayout.viewport + 1, JSON.stringify(desktopLayout));
    await capture(page, "douyin-desktop");

    await page.locator(`[data-topic-id="${topicId}"] .douyin-topic__action`).click();
    await page.waitForURL("**/static/publish.html");
    await page.locator("body[data-publish-ready='true']").waitFor();
    record("发布实验预填内容编号", await page.locator("#cSourceTopicId").inputValue() === topicId);
    record("发布实验预填抖音平台", await page.locator("#cPlatform").inputValue() === "抖音");
    record("发布实验预填选题标题", (await page.locator("#cTitle").inputValue()).length > 0);
    await context.close();

    const mobileContext = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const mobile = await mobileContext.newPage();
    wireDiagnostics(mobile);
    await mobile.goto(`${baseUrl}/static/douyin.html`, { waitUntil: "networkidle" });
    await mobile.locator("body[data-douyin-ready='true']").waitFor();
    await mobile.locator(`[data-topic-id="${topicId}"]`).waitFor();
    const mobileLayout = await layout(mobile);
    record("390px 抖音页无横向溢出", mobileLayout.rootWidth <= mobileLayout.viewport + 1 && mobileLayout.bodyWidth <= mobileLayout.viewport + 1, JSON.stringify(mobileLayout));
    record("390px 主动作完整可见", await mobile.locator(`[data-topic-id="${topicId}"] .douyin-topic__action`).isVisible());
    await capture(mobile, "douyin-mobile-390");
    await mobileContext.close();
  } finally {
    await browser.close();
  }

  record("控制台无错误", diagnostics.consoleErrors.length === 0, diagnostics.consoleErrors.join(" | "));
  record("页面无异常", diagnostics.pageErrors.length === 0, diagnostics.pageErrors.join(" | "));
  record("请求无失败", diagnostics.requestFailures.length === 0, JSON.stringify(diagnostics.requestFailures));
  record("响应无 4xx/5xx", diagnostics.badResponses.length === 0, JSON.stringify(diagnostics.badResponses));

  const result = { baseUrl, topicId, passed: checks.filter((item) => item.ok).length, failed: checks.filter((item) => !item.ok).length, checks, diagnostics };
  if (outputDir) fs.writeFileSync(path.join(outputDir, "browser-results.json"), JSON.stringify(result, null, 2), "utf8");
  process.stdout.write(`${JSON.stringify(result)}\n`);
  process.exitCode = result.failed ? 1 : 0;
})().catch((error) => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exitCode = 1;
});
