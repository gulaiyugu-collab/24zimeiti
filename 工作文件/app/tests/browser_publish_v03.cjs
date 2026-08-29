"use strict";

const fs = require("fs");
const path = require("path");
const playwrightPath = process.env.PLAYWRIGHT_CORE_PATH || "G:\\Tools\\gstack\\node_modules\\playwright-core";
const { chromium } = require(playwrightPath);

const baseUrl = (process.env.BASE_URL || process.argv[2] || "http://127.0.0.1:8792").replace(/\/$/, "");
const pageUrl = `${baseUrl}/static/publish.html`;
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

function isApiResponse(response, suffix, method = "POST") {
  const url = new URL(response.url());
  return response.request().method() === method && url.pathname.endsWith(suffix);
}

async function waitUntilReady(page) {
  await page.locator("body[data-publish-ready='true']").waitFor({ timeout: 15000 });
  await page.locator("#experiments").waitFor({ state: "visible" });
}

async function capture(page, name) {
  if (!outputDir) return;
  fs.mkdirSync(outputDir, { recursive: true });
  await page.screenshot({ path: path.join(outputDir, `${name}.png`), fullPage: true, animations: "disabled" });
}

async function verifyMobileLayout(browser) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await context.newPage();
  wireDiagnostics(page);
  await page.goto(pageUrl, { waitUntil: "domcontentloaded" });
  await waitUntilReady(page);
  const layout = await page.evaluate(() => ({
    viewportWidth: window.innerWidth,
    rootScrollWidth: document.documentElement.scrollWidth,
    bodyScrollWidth: document.body.scrollWidth,
    tableWrappers: Array.from(document.querySelectorAll(".publish-table-wrap")).map((node) => ({
      clientWidth: node.clientWidth,
      scrollWidth: node.scrollWidth
    }))
  }));
  record(
    "390px 页面无横向溢出",
    layout.rootScrollWidth <= layout.viewportWidth + 1 && layout.bodyScrollWidth <= layout.viewportWidth + 1,
    JSON.stringify(layout)
  );
  record("390px 内部宽表可独立滚动", layout.tableWrappers.every((item) => item.clientWidth > 0), JSON.stringify(layout.tableWrappers));
  await capture(page, "publish-mobile-390");
  await context.close();
}

(async () => {
  if (!executablePath) throw new Error("找不到可用 Chromium；请设置 CHROMIUM_EXECUTABLE");
  const browser = await chromium.launch({ executablePath, headless: true });
  const unique = Date.now().toString(36);
  const draft = {
    title: `P3 浏览器验收 ${unique}`,
    platform: "抖音",
    source_url: "https://example.com/source",
    analysis_ref: `analysis-${unique}`,
    content_summary: "开头用具体问题建立场景，再给出可核对证据。",
    hypothesis: "本轮只替换前三秒钩子，其他脚本和发布时间保持不变。"
  };

  try {
    const context = await browser.newContext({ viewport: { width: 1365, height: 900 } });
    const page = await context.newPage();
    wireDiagnostics(page);
    await page.addInitScript((value) => {
      if (!window.sessionStorage.getItem("project024_publish_test_seeded")) {
        window.sessionStorage.setItem("project024_publish_draft", JSON.stringify(value));
        window.sessionStorage.setItem("project024_publish_test_seeded", "true");
      }
    }, draft);
    await page.goto(pageUrl, { waitUntil: "domcontentloaded" });
    await waitUntilReady(page);

    record("分析草稿预填标题", await page.locator("#cTitle").inputValue() === draft.title);
    record("分析草稿只预填用户字段", await page.locator("#cAnalysisRef").inputValue() === draft.analysis_ref);
    record("7 维高级自评仍可展开填写", await page.locator("#scoreGrid input").count() === 7);
    record("7 个复盘基线指标仍可展开填写", await page.locator("#predGrid .prediction-item").count() === 7);
    record("两个高级项默认折叠", await page.locator("#advancedScores:not([open]), #advancedPredictions:not([open])").count() === 2);
    const scoreValues = await page.locator("#scoreGrid input").evaluateAll((inputs) => inputs.map((input) => input.value));
    record("7 维自评默认不代填", scoreValues.every((value) => value === ""), JSON.stringify(scoreValues));

    const createResponsePromise = page.waitForResponse((response) => isApiResponse(response, "/api/publish/experiments"));
    await page.locator("#createSubmit").click();
    const createResponse = await createResponsePromise;
    record("登记实验返回 201", createResponse.status() === 201, String(createResponse.status()));
    const created = await createResponse.json();
    record("核心字段可无评分和基线直接登记", created.scores.length === 0 && created.predictions.length === 0, JSON.stringify({ scores: created.scores, predictions: created.predictions }));
    const experimentId = created.id;
    const cardSelector = `[data-experiment-id="${experimentId}"]`;
    await page.locator(cardSelector).waitFor();
    record("新实验处于已登记状态", await page.locator(`${cardSelector} [data-status]`).getAttribute("data-status") === "predicted");
    record("登记态只开放登记发布", await page.locator(`${cardSelector} [data-action='publish']`).count() === 1 && await page.locator(`${cardSelector} [data-action='backfill']`).count() === 0);

    await page.reload({ waitUntil: "domcontentloaded" });
    await waitUntilReady(page);
    await page.locator(cardSelector).waitFor();
    record("刷新后登记记录仍存在", await page.locator(`${cardSelector} .experiment-title`).textContent() === draft.title);

    const illegalResponse = await context.request.post(`${baseUrl}/api/publish/experiments/${encodeURIComponent(experimentId)}/backfill`, {
      data: {
        metrics: { views: 150 },
        window_hours: 72,
        observed_at: new Date().toISOString(),
        data_source: "浏览器非法提前操作验收"
      }
    });
    record("非法提前回填返回 409", illegalResponse.status() === 409, String(illegalResponse.status()));

    await page.locator(`${cardSelector} [data-action='publish']`).click();
    const publishForm = page.locator(`${cardSelector} [data-inline-action='publish']`);
    await publishForm.locator("input[name='publish_url']").fill(`https://example.com/published/${unique}`);
    const publishResponsePromise = page.waitForResponse((response) => isApiResponse(response, `/${experimentId}/publish`));
    await publishForm.locator("button[type='submit']").click();
    const publishResponse = await publishResponsePromise;
    record("登记发布返回 200", publishResponse.status() === 200, String(publishResponse.status()));
    await page.locator(`${cardSelector} [data-status='published']`).waitFor();
    record("发布态只开放回填", await page.locator(`${cardSelector} [data-action='backfill']`).count() === 1 && await page.locator(`${cardSelector} [data-action='review']`).count() === 0);

    await page.locator(`${cardSelector} [data-action='backfill']`).click();
    const backfillForm = page.locator(`${cardSelector} [data-inline-action='backfill']`);
    record("无基线时仍可回填全部真实指标", await backfillForm.locator("[data-metric-key]").count() === 7);
    await backfillForm.locator("[data-metric-key='views']").fill("180");
    await backfillForm.locator("[data-metric-key='retention']").fill("48.5");
    await backfillForm.locator("textarea[name='note']").fill("平台后台同一时点人工读取。 ");
    const backfillResponsePromise = page.waitForResponse((response) => isApiResponse(response, `/${experimentId}/backfill`));
    await backfillForm.locator("button[type='submit']").click();
    const backfillResponse = await backfillResponsePromise;
    record("回填实测返回 200", backfillResponse.status() === 200, String(backfillResponse.status()));
    await page.locator(`${cardSelector} [data-status='measured']`).waitFor();
    record("默认观察窗口显示 T+72 小时", (await page.locator(`${cardSelector}`).textContent()).includes("T+72 小时"));

    await page.locator(`${cardSelector} [data-action='review']`).click();
    const reviewForm = page.locator(`${cardSelector} [data-inline-action='review']`);
    await reviewForm.locator("textarea[name='note']").fill("本轮未设指标基线，只记录真实结果和已知异常。 ");
    const reviewResponsePromise = page.waitForResponse((response) => isApiResponse(response, `/${experimentId}/review`));
    await reviewForm.locator("button[type='submit']").click();
    const reviewResponse = await reviewResponsePromise;
    record("复盘返回 200", reviewResponse.status() === 200, String(reviewResponse.status()));
    const reviewed = await reviewResponse.json();
    record("无基线复盘不计算偏差或生成经验候选", reviewed.deviations.length === 0 && reviewed.learning_candidate === false, JSON.stringify({ deviations: reviewed.deviations, learning_candidate: reviewed.learning_candidate }));
    await page.locator(`${cardSelector} [data-status='reviewed']`).waitFor();
    record("复盘后不再开放状态操作", await page.locator(`${cardSelector} [data-action]`).count() === 0);
    record("跨实验摘要已显示", !(await page.locator("#calibrationState").textContent()).includes("暂不可用"));

    const eventResponsePromise = page.waitForResponse((response) => isApiResponse(response, `/${experimentId}/events`, "GET"));
    await page.locator(`${cardSelector} [data-events]`).click();
    const eventResponse = await eventResponsePromise;
    record("事件历史接口返回 200", eventResponse.status() === 200, String(eventResponse.status()));
    await page.locator(`${cardSelector} .event-list`).waitFor();
    record("四步事件历史完整", await page.locator(`${cardSelector} .event-list li`).count() === 4);

    await page.reload({ waitUntil: "domcontentloaded" });
    await waitUntilReady(page);
    await page.locator(`${cardSelector} [data-status='reviewed']`).waitFor();
    const hashText = (await page.locator(`${cardSelector} .snapshot-hash`).textContent()).trim();
    record("刷新后复盘状态持久化", await page.locator(`${cardSelector} [data-status='reviewed']`).count() === 1);
    record("内容版本哈希可见", /^[a-f0-9]{64}$/i.test(hashText), hashText);
    record("分析引用可见", (await page.locator(`${cardSelector}`).textContent()).includes(draft.analysis_ref));
    record("发布链接可打开", await page.locator(`${cardSelector} a.publish-link`).filter({ hasText: "打开已发布内容" }).getAttribute("href") === `https://example.com/published/${unique}`);
    await capture(page, "publish-reviewed-desktop");
    await context.close();

    await verifyMobileLayout(browser);
  } finally {
    await browser.close();
  }

  record("控制台无错误", diagnostics.consoleErrors.length === 0, diagnostics.consoleErrors.join(" | "));
  record("页面无异常", diagnostics.pageErrors.length === 0, diagnostics.pageErrors.join(" | "));
  record("请求无网络失败", diagnostics.requestFailures.length === 0, JSON.stringify(diagnostics.requestFailures));
  record("页面响应无意外 4xx/5xx", diagnostics.badResponses.length === 0, JSON.stringify(diagnostics.badResponses));

  const result = {
    baseUrl,
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
