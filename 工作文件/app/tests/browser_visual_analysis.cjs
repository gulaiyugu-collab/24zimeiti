"use strict";

const fs = require("fs");
const path = require("path");
const playwrightPath = process.env.PLAYWRIGHT_CORE_PATH || "G:\\Tools\\gstack\\node_modules\\playwright-core";
const { chromium } = require(playwrightPath);

const baseUrl = (process.env.BASE_URL || process.argv[2] || "http://127.0.0.1:8792").replace(/\/$/, "");
const jobId = process.env.VISUAL_JOB_ID || process.argv[3] || "";
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
    url: request.url(),
    error: request.failure()?.errorText || "unknown"
  }));
  page.on("response", (response) => {
    if (response.status() >= 400) diagnostics.badResponses.push({
      url: response.url(),
      status: response.status()
    });
  });
}

async function loadAnalysis(page) {
  const statusResponse = await page.request.get(`${baseUrl}/api/acquisition/jobs/${encodeURIComponent(jobId)}`);
  const manifestResponse = await page.request.get(`${baseUrl}/api/acquisition/jobs/${encodeURIComponent(jobId)}/manifest`);
  if (!statusResponse.ok() || !manifestResponse.ok()) throw new Error("指定采集任务不可读取");
  const status = await statusResponse.json();
  const manifest = await manifestResponse.json();
  await page.route("**/api/acquisition/jobs", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify(status) });
      return;
    }
    await route.continue();
  });
  await page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded" });
  await page.locator("#analysisForm").waitFor({ state: "visible" });
  await page.locator("#urlInput").fill(manifest.canonical_url);
  const analysisResponsePromise = page.waitForResponse((response) => (
    response.request().method() === "POST"
      && new URL(response.url()).pathname.endsWith(`/${jobId}/analyze`)
  ));
  await page.locator("#analyzeButton").click();
  const response = await analysisResponsePromise;
  if (!response.ok()) throw new Error(`视觉分析接口失败：${response.status()} ${await response.text()}`);
  const payload = await response.json();
  await page.locator("#visualAnalysis .visual-analysis").waitFor({ state: "visible" });
  return payload;
}

async function capture(page, name) {
  if (!outputDir) return;
  fs.mkdirSync(outputDir, { recursive: true });
  await page.screenshot({ path: path.join(outputDir, `${name}.png`), fullPage: true, animations: "disabled" });
}

(async () => {
  if (!jobId) throw new Error("请通过 VISUAL_JOB_ID 或第三个参数指定已完成的采集任务");
  if (!executablePath) throw new Error("找不到可用 Chromium；请设置 CHROMIUM_EXECUTABLE");
  const browser = await chromium.launch({ executablePath, headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1365, height: 900 } });
    const page = await context.newPage();
    wireDiagnostics(page);
    const payload = await loadAnalysis(page);
    const visual = payload.report?.visual_analysis || {};
    record("真实分析返回代表帧", Number(visual.frame_count) > 0, String(visual.frame_count));
    record("镜头结构标记为机器估算完成", visual.scene_structure?.status === "completed" && visual.scene_structure?.pace_is_heuristic === true);
    record("本地 OCR 真实完成", visual.ocr?.status === "completed" && Number(visual.ocr?.block_count) > 0, JSON.stringify(visual.ocr || {}));
    record("OCR 文字块带帧、坐标和置信度", Array.isArray(visual.ocr?.blocks) && visual.ocr.blocks.every((block) => (
      block.frame_id
      && Array.isArray(block.box)
      && Number(block.confidence) >= 0
      && Number(block.confidence) <= 1
    )), JSON.stringify(visual.ocr?.blocks?.slice(0, 3) || []));

    const visualText = await page.locator("#visualAnalysis").textContent();
    record("页面展示候选切点和节奏", visualText.includes("候选切点") && visualText.includes("镜头节奏"), visualText);
    record("页面明确机器估算边界", visualText.includes("机器估算") && visualText.includes("不等于理解了画面含义"), visualText);
    record("页面显示本地 OCR 完成与复核边界", visualText.includes("本机 OCR 已识别") && visualText.includes("低置信度文字仍需对照代表帧复核"), visualText);

    await page.locator("#visualAnalysis details").evaluate((node) => { node.open = true; });
    const images = page.locator("#visualAnalysis img");
    const imageCount = await images.count();
    await images.evaluateAll((nodes) => nodes.forEach((image) => { image.loading = "eager"; }));
    if (imageCount) await images.last().scrollIntoViewIfNeeded();
    await page.waitForFunction(() => Array.from(document.querySelectorAll("#visualAnalysis img")).every((image) => image.complete), null, { timeout: 30000 });
    const imageState = await images.evaluateAll((nodes) => nodes.map((image) => ({
      naturalWidth: image.naturalWidth,
      naturalHeight: image.naturalHeight
    })));
    record("代表帧全部真实加载", imageCount === visual.frame_count && imageState.every((item) => item.naturalWidth > 0 && item.naturalHeight > 0), JSON.stringify(imageState));
    const visibleText = await page.locator("body").innerText();
    record("页面不显示任务号或哈希", !visibleText.includes("acq_") && !/[a-f0-9]{64}/i.test(visibleText));
    await capture(page, "visual-analysis-desktop");
    await context.close();

    const mobileContext = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const mobilePage = await mobileContext.newPage();
    wireDiagnostics(mobilePage);
    await loadAnalysis(mobilePage);
    const layout = await mobilePage.evaluate(() => ({
      viewport: window.innerWidth,
      root: document.documentElement.scrollWidth,
      body: document.body.scrollWidth
    }));
    record("390px 页面无横向溢出", layout.root <= layout.viewport + 1 && layout.body <= layout.viewport + 1, JSON.stringify(layout));
    await capture(mobilePage, "visual-analysis-mobile-390");
    await mobileContext.close();
  } finally {
    await browser.close();
  }

  record("控制台无错误", diagnostics.consoleErrors.length === 0, diagnostics.consoleErrors.join(" | "));
  record("页面无异常", diagnostics.pageErrors.length === 0, diagnostics.pageErrors.join(" | "));
  record("请求无网络失败", diagnostics.requestFailures.length === 0, JSON.stringify(diagnostics.requestFailures));
  record("页面响应无意外 4xx/5xx", diagnostics.badResponses.length === 0, JSON.stringify(diagnostics.badResponses));

  const result = {
    baseUrl,
    jobId,
    passed: checks.filter((item) => item.ok).length,
    failed: checks.filter((item) => !item.ok).length,
    checks,
    diagnostics
  };
  if (outputDir) {
    fs.mkdirSync(outputDir, { recursive: true });
    fs.writeFileSync(path.join(outputDir, "browser-results.json"), JSON.stringify(result, null, 2), "utf8");
  }
  process.stdout.write(`${JSON.stringify(result)}\n`);
  process.exitCode = result.failed ? 1 : 0;
})().catch((error) => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exitCode = 1;
});
