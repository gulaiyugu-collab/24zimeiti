"use strict";

const fs = require("fs");
const path = require("path");
const { chromium } = require("G:\\Tools\\gstack\\node_modules\\playwright-core");

const baseUrl = process.argv[2] || "http://127.0.0.1:8793";
const outputDir = process.argv[3] || path.resolve(__dirname, "product-relevance-acceptance");
const fixtureOnly = process.argv[4] === "fixture";
const executablePath = "C:\\Users\\Administrator\\AppData\\Local\\ms-playwright\\chromium-1228\\chrome-win64\\chrome.exe";
const sourceUrl = "https://www.douyin.com/video/7999999999999999999";

fs.mkdirSync(outputDir, { recursive: true });

const checks = [];
const diagnostics = {
  consoleErrors: [],
  pageErrors: [],
  requestFailures: [],
  badResponses: []
};

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

async function submitTranscript(page, transcript) {
  await page.locator("#urlInput").fill(sourceUrl);
  await page.locator("#supplementDetails").evaluate((node) => { node.open = true; });
  await page.locator("#transcriptInput").fill(transcript);
  const responsePromise = page.waitForResponse(
    (response) => (
      response.url().endsWith("/api/analyze")
      || /\/api\/acquisition\/jobs\/[^/]+\/analyze$/.test(response.url())
    ) && response.request().method() === "POST",
    { timeout: 15000 }
  );
  await page.locator("#analyzeButton").click();
  const response = await responsePromise;
  const payload = await response.json();
  await page.waitForFunction(() => {
    const report = document.querySelector("#reportLayout");
    return report && report.hidden === false;
  });
  await page.waitForFunction(
    () => document.querySelectorAll(".stage.is-revealed").length === 3,
    { timeout: 3000 }
  );
  return { response, payload };
}

async function layout(page) {
  return page.evaluate(() => {
    const ids = [
      "resultArea",
      "gateQuick",
      "productRelevance",
      "pathway",
      "stageScript",
      "stageShooting",
      "stagePublish",
      "publishingPackage",
      "requirementsSummary",
      "sourceSummary",
      "qualitySummary",
      "asrSummary",
      "distillationReport",
      "trafficAssessment",
      "calibrationPlan",
      "audienceInsights",
      "localizationSummary",
      "evidenceRisks",
      "riskReview",
      "additionalReport"
    ];
    return {
      viewport: window.innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
      scrollHeight: document.documentElement.scrollHeight,
      overflow: document.documentElement.scrollWidth > window.innerWidth + 1,
      elements: Object.fromEntries(ids.map((id) => {
        const node = document.getElementById(id);
        if (!node) return [id, null];
        const rect = node.getBoundingClientRect();
        return [id, {
          top: Math.round(rect.top + window.scrollY),
          bottom: Math.round(rect.bottom + window.scrollY),
          height: Math.round(rect.height)
        }];
      }))
    };
  });
}

async function runDesktop(browser) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  wireDiagnostics(page);
  await page.goto(baseUrl, { waitUntil: "networkidle" });

  const nonProduct = await submitTranscript(
    page,
    "今天分享三个提高专注力的方法，先减少干扰，再拆小任务，最后记录每天的复盘。"
  );
  const nonProductReport = nonProduct.payload.report;
  record("非商品接口返回 no_product", nonProductReport.product_relevance.status === "no_product");
  record("非商品不返回商品缺失项", !nonProduct.payload.missing.some((item) => String(item).includes("商品")), JSON.stringify(nonProduct.payload.missing));
  record("非商品商品资料状态为不适用", nonProductReport.product_requirements.status === "not_applicable");
  record("独立商品属性栏可见", await page.locator("#productRelevance").isVisible());
  record("独立栏标注无商品属性", (await page.locator(".product-relevance__status").textContent()) === "无商品属性");
  record("非商品隐藏商品输入", await page.locator("#productContextField").isHidden());
  record("非商品不显示商品缺口组", await page.locator(".product-relevance__group--requirements").count() === 0);
  record("资料盘点显示可选增强", (await page.locator("#requirementsSummary").innerText()).includes("可选增强"));
  await page.screenshot({
    path: path.join(outputDir, "desktop_no_product.png"),
    fullPage: true,
    animations: "disabled"
  });

  const ambiguous = await submitTranscript(page, "这个东西最近很好用，很多人都在问。");
  record("模糊内容返回 needs_confirmation", ambiguous.payload.report.product_relevance.status === "needs_confirmation");
  record("待确认显示两个确认按钮", await page.locator(".product-relevance__actions button").count() === 2);
  await page.getByRole("button", { name: "不是商品内容" }).click();
  record("用户可确认不是商品内容", (await page.locator(".product-relevance__status").textContent()) === "无商品属性");
  record("确认后商品输入保持隐藏", await page.locator("#productContextField").isHidden());

  const product = await submitTranscript(
    page,
    "这款商品今天演示三种配件，价格和规格请查看商品页，正式购买前核对型号。"
  );
  record("商品内容返回 has_product", product.payload.report.product_relevance.status === "has_product");
  record("商品栏标注具有商品属性", (await page.locator(".product-relevance__status").textContent()) === "具有商品属性");
  record("商品内容显示后续资料", await page.locator(".product-relevance__group--requirements li").count() >= 1);
  record("商品内容显示商品输入", await page.locator("#productContextField").isVisible());
  const desktopLayout = await layout(page);
  record("桌面无横向溢出", !desktopLayout.overflow, JSON.stringify(desktopLayout));
  record("桌面内容高度合理", desktopLayout.scrollHeight < 6500, JSON.stringify(desktopLayout));
  await page.screenshot({
    path: path.join(outputDir, "desktop_has_product.png"),
    fullPage: true,
    animations: "disabled"
  });
  await context.close();
}

async function runMobile(browser) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await context.newPage();
  wireDiagnostics(page);
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  await submitTranscript(
    page,
    "今天分享三个提高专注力的方法，先减少干扰，再拆小任务，最后记录每天的复盘。"
  );
  record("手机端商品属性栏可见", await page.locator("#productRelevance").isVisible());
  record("手机端无商品时隐藏商品输入", await page.locator("#productContextField").isHidden());
  const mobileLayout = await layout(page);
  record("手机端无横向溢出", !mobileLayout.overflow, JSON.stringify(mobileLayout));
  record("手机端内容高度合理", mobileLayout.scrollHeight < 8000, JSON.stringify(mobileLayout));
  await page.screenshot({
    path: path.join(outputDir, "mobile_no_product.png"),
    fullPage: true,
    animations: "disabled"
  });
  await context.close();
}

async function runRegisteredFixture(browser) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  wireDiagnostics(page);
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  await page.locator("#demoButton").click();
  await page.waitForFunction(
    () => document.querySelector("#urlInput")?.value.includes("douyin.com/video/")
  );
  const responsePromise = page.waitForResponse(
    (response) => (
      response.url().endsWith("/api/analyze")
      || /\/api\/acquisition\/jobs\/[^/]+\/analyze$/.test(response.url())
    ) && response.request().method() === "POST",
    { timeout: 15000 }
  );
  await page.locator("#analyzeButton").click();
  const response = await responsePromise;
  const payload = await response.json();
  await page.waitForFunction(
    () => document.querySelectorAll(".stage.is-revealed").length === 3,
    { timeout: 3000 }
  );
  record("正式入口登记样本 HTTP 200", response.status() === 200, response.status());
  record("登记样本完成解读", payload.status === "completed", payload.status);
  record("登记样本识别商品属性", payload.report?.product_relevance?.status === "has_product");
  record("完成解读没有必补项", payload.report?.requirements?.blocking_for_interpretation?.length === 0, JSON.stringify(payload.report?.requirements));
  record("正式入口显示独立商品栏", await page.locator("#productRelevance").isVisible());
  record("正式入口显示商品后续资料", await page.locator(".product-relevance__group--requirements li").count() >= 1);
  const fixtureLayout = await layout(page);
  record("正式入口无横向溢出", !fixtureLayout.overflow, JSON.stringify(fixtureLayout));
  await page.screenshot({
    path: path.join(outputDir, "desktop_registered_fixture.png"),
    fullPage: true,
    animations: "disabled"
  });
  await context.close();
}

(async () => {
  const browser = await chromium.launch({ executablePath, headless: true });
  try {
    if (fixtureOnly) {
      await runRegisteredFixture(browser);
    } else {
      await runDesktop(browser);
      await runMobile(browser);
    }
  } finally {
    await browser.close();
  }

  record("控制台无错误", diagnostics.consoleErrors.length === 0, JSON.stringify(diagnostics.consoleErrors));
  record("页面无异常", diagnostics.pageErrors.length === 0, JSON.stringify(diagnostics.pageErrors));
  record("请求无失败", diagnostics.requestFailures.length === 0, JSON.stringify(diagnostics.requestFailures));
  record("响应无 4xx/5xx", diagnostics.badResponses.length === 0, JSON.stringify(diagnostics.badResponses));

  const result = {
    baseUrl,
    summary: {
      total: checks.length,
      passed: checks.filter((item) => item.ok).length,
      failed: checks.filter((item) => !item.ok).length
    },
    checks,
    diagnostics
  };
  fs.writeFileSync(
    path.join(outputDir, "browser-results.json"),
    JSON.stringify(result, null, 2),
    "utf8"
  );
  process.stdout.write(JSON.stringify(result.summary));
  if (result.summary.failed > 0) process.exitCode = 1;
})().catch((error) => {
  process.stderr.write(String(error.stack || error));
  process.exitCode = 1;
});
