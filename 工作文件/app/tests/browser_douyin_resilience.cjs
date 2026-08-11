"use strict";

const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright-core");

const baseUrl = process.argv[2] || "http://127.0.0.1:8792";
const outputDir = process.argv[3] || path.resolve(
  __dirname,
  "..",
  "..",
  "..",
  "产出",
  "验真",
  "p2_douyin_resilience",
);
const executablePath = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const shareText = process.argv[4] || "1.25 复制打开抖音，看看【95KStar开源Skill，让Token消耗暴降90% #ai工具测评 #skill #vibecoding #编程效率 #ai编程】的作品！https://v.douyin.com/vgp9oxHDfmQ/ :2pm 11/17";
const expectedJobId = process.argv[5] || "";

fs.mkdirSync(outputDir, { recursive: true });

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const diagnostics = { consoleErrors: [], pageErrors: [], requestFailures: [], badResponses: [] };
  const requests = [];

  page.on("console", (message) => {
    if (message.type() === "error") diagnostics.consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => diagnostics.pageErrors.push(error.message));
  page.on("request", (request) => {
    if (request.url().includes("/api/")) {
      requests.push({ method: request.method(), url: request.url() });
    }
  });
  page.on("requestfailed", (request) => {
    diagnostics.requestFailures.push({ url: request.url(), error: request.failure()?.errorText || "unknown" });
  });
  page.on("response", (response) => {
    if (response.status() >= 400) diagnostics.badResponses.push({ url: response.url(), status: response.status() });
  });

  await page.goto(baseUrl, { waitUntil: "networkidle" });
  const health = await page.evaluate(() => fetch("/api/health").then((response) => response.json()));
  const acquisitionPromise = page.waitForResponse(
    (response) => response.request().method() === "POST" && /\/api\/acquisition\/jobs$/.test(response.url()),
    { timeout: 30_000 },
  );
  const analysisPromise = page.waitForResponse(
    (response) => response.request().method() === "POST" && /\/api\/acquisition\/jobs\/[^/]+\/analyze$/.test(response.url()),
    { timeout: 60_000 },
  );
  await page.locator("#urlInput").fill(shareText);
  await page.locator("#analyzeButton").click();
  const acquisitionResponse = await acquisitionPromise;
  const acquisition = await acquisitionResponse.json();
  const analysisResponse = await analysisPromise;
  const analysis = await analysisResponse.json();
  await page.locator("#reportLayout").waitFor({ state: "visible", timeout: 20_000 });
  await page.waitForFunction(() => !document.querySelector("#analyzeButton")?.disabled);

  const screenshot = path.join(outputDir, "desktop_share_text_completed.png");
  await page.screenshot({ path: screenshot, fullPage: true, animations: "disabled" });
  const jobStatusGets = requests.filter(
    (request) => request.method === "GET" && new RegExp(`/api/acquisition/jobs/${acquisition.job_id}$`).test(request.url),
  );
  const checks = {
    paidContentDisabledForTest: health.paid_content_enabled === false,
    acquisitionHttpAccepted: acquisitionResponse.status() === 202,
    acquisitionCompleted: acquisition.status === "completed",
    stableCacheHit: acquisition.cache_hit === true,
    expectedJobMatched: !expectedJobId || acquisition.job_id === expectedJobId,
    analysisHttpOk: analysisResponse.status() === 200,
    analysisHasReport: Boolean(analysis.report),
    reportVisible: await page.locator("#reportLayout").isVisible(),
    buttonReset: (await page.locator("#analyzeButton .button__label").textContent()).trim() === "快速看懂",
    terminalDidNotPoll: jobStatusGets.length === 0,
    noConsoleErrors: diagnostics.consoleErrors.length === 0,
    noPageErrors: diagnostics.pageErrors.length === 0,
    noRequestFailures: diagnostics.requestFailures.length === 0,
    noBadResponses: diagnostics.badResponses.length === 0,
  };
  const result = {
    passed: Object.values(checks).every(Boolean),
    checks,
    acquisition: {
      job_id: acquisition.job_id,
      status: acquisition.status,
      cache_hit: acquisition.cache_hit,
    },
    analysis: { status: analysis.status, platform: analysis.platform },
    diagnostics,
    jobStatusGetCount: jobStatusGets.length,
    screenshot,
  };
  fs.writeFileSync(
    path.join(outputDir, "browser-results.json"),
    `${JSON.stringify(result, null, 2)}\n`,
    "utf8",
  );
  process.stdout.write(`${JSON.stringify(result)}\n`);
  await browser.close();
  if (!result.passed) process.exitCode = 1;
})().catch((error) => {
  console.error(error.stack || error.message);
  process.exit(1);
});
