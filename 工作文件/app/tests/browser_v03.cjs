"use strict";

const fs = require("fs");
const path = require("path");
const { chromium } = require("G:\\Tools\\gstack\\node_modules\\playwright-core");

const baseUrl = process.argv[2] || "http://127.0.0.1:8792";
const projectRoot = path.resolve(__dirname, "..", "..", "..");
const outputDir = process.argv[3] || path.join(projectRoot, "产出", "验真", "v03_demo");
const executablePath = "C:\\Users\\Administrator\\AppData\\Local\\ms-playwright\\chromium-1228\\chrome-win64\\chrome.exe";

fs.mkdirSync(outputDir, { recursive: true });

const checks = [];
const diagnostics = { consoleErrors: [], pageErrors: [], requestFailures: [], badResponses: [] };
const screenshots = [];

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

async function capture(page, name) {
  const file = path.join(outputDir, `${name}.png`);
  await page.screenshot({ path: file, fullPage: true, animations: "disabled" });
  screenshots.push(file);
}

async function layoutCheck(page) {
  return page.evaluate(() => ({
    viewport: window.innerWidth,
    rootWidth: document.documentElement.scrollWidth,
    overflow: document.documentElement.scrollWidth > window.innerWidth + 1
  }));
}

async function runViewport(browser, name, viewport) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  wireDiagnostics(page);

  await page.goto(baseUrl, { waitUntil: "networkidle" });
  record(`${name}: v0.3 标识`, await page.locator(".brand__version").textContent() === "v0.3");
  record(
    `${name}: 首屏标题`,
    (await page.locator("#workspaceTitle").textContent()).includes("视频链接")
  );
  record(`${name}: 主按钮`, await page.locator("#analyzeButton").innerText() === "快速看懂");

  await page.locator("#demoButton").click();
  await page.locator("#analyzeButton").click();
  await page.locator("#quickView").waitFor({ state: "visible", timeout: 10000 });
  const summary = (await page.locator("#quickSummary").textContent()).trim();
  record(`${name}: 一句话结果`, summary.length >= 10, summary);
  record(`${name}: 内容步骤`, await page.locator("#quickWhatHappens li").count() >= 1);
  record(`${name}: 可借鉴方法`, await page.locator("#quickTransferable li").count() >= 1);
  record(`${name}: 完整报告默认收起`, !(await page.locator("#advancedResults").evaluate((node) => node.open)));
  await capture(page, `${name}_quick`);

  await page.locator("#advancedToggleButton").click();
  record(`${name}: 完整报告可展开`, await page.locator("#advancedResults").evaluate((node) => node.open));
  record(`${name}: 完整脚本可见`, await page.locator("#recommendedDraft").isVisible());
  const layout = await layoutCheck(page);
  record(`${name}: 无横向溢出`, !layout.overflow, JSON.stringify(layout));

  await context.close();
}

(async () => {
  const browser = await chromium.launch({ executablePath, headless: true });
  try {
    await runViewport(browser, "desktop", { width: 1440, height: 900 });
    await runViewport(browser, "mobile", { width: 390, height: 844 });
  } finally {
    await browser.close();
  }

  record("控制台无错误", diagnostics.consoleErrors.length === 0, diagnostics.consoleErrors.join(" | "));
  record("页面无异常", diagnostics.pageErrors.length === 0, diagnostics.pageErrors.join(" | "));
  record("请求无失败", diagnostics.requestFailures.length === 0, JSON.stringify(diagnostics.requestFailures));
  record("响应无 4xx/5xx", diagnostics.badResponses.length === 0, JSON.stringify(diagnostics.badResponses));

  const result = {
    baseUrl,
    passed: checks.filter((item) => item.ok).length,
    failed: checks.filter((item) => !item.ok).length,
    checks,
    diagnostics,
    screenshots
  };
  fs.writeFileSync(path.join(outputDir, "browser-results.json"), JSON.stringify(result, null, 2), "utf8");
  process.stdout.write(`${JSON.stringify(result)}\n`);
  process.exitCode = result.failed ? 1 : 0;
})().catch((error) => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exitCode = 1;
});
