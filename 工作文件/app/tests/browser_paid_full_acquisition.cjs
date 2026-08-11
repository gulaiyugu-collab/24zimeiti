"use strict";

const fs = require("fs");
const path = require("path");
const { chromium } = require("G:\\Tools\\gstack\\node_modules\\playwright-core");

const baseUrl = process.argv[2] || "http://127.0.0.1:8792";
const outputDir = process.argv[3] || path.resolve(__dirname, "paid-full-acceptance");
const sourceUrl = process.argv[4] || "https://v.douyin.com/n0JnmW4HmME/";
const executablePath = "C:\\Users\\Administrator\\AppData\\Local\\ms-playwright\\chromium-1228\\chrome-win64\\chrome.exe";

fs.mkdirSync(outputDir, { recursive: true });

(async () => {
  const checks = [];
  const diagnostics = { consoleErrors: [], pageErrors: [], requestFailures: [], badResponses: [] };
  const record = (name, ok, detail = "") => checks.push({ name, ok: Boolean(ok), detail: String(detail || "") });
  const browser = await chromium.launch({ executablePath, headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await context.newPage();
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

    const health = await context.request.get(`${baseUrl}/api/health`);
    const healthPayload = await health.json();
    record("paid mode enabled", healthPayload.paid_content_enabled === true, JSON.stringify(healthPayload));

    await page.goto(baseUrl, { waitUntil: "networkidle" });
    await page.waitForFunction(() => document.querySelector("#analyzeButton .button__label")?.textContent === "生成完整脚本");
    record("primary button uses full generation label", (await page.locator("#analyzeButton .button__label").textContent()) === "生成完整脚本");
    await page.locator("#urlInput").fill(sourceUrl);

    let analysisRequest = null;
    page.on("request", (request) => {
      if (/\/api\/acquisition\/jobs\/[^/]+\/analyze$/.test(request.url())) {
        analysisRequest = request;
      }
    });
    const responsePromise = page.waitForResponse(
      (response) => /\/api\/acquisition\/jobs\/[^/]+\/analyze$/.test(response.url()) && response.request().method() === "POST",
      { timeout: 240000 }
    );
    await page.locator("#analyzeButton").click();
    const response = await responsePromise;
    const payload = await response.json();
    await page.waitForFunction(() => document.querySelector("#reportLayout") && !document.querySelector("#reportLayout").hidden);
    await page.waitForFunction(() => document.querySelector("#analyzeButton .button__label")?.textContent === "生成完整脚本");

    const requestBody = analysisRequest?.postDataJSON() || {};
    const generation = payload?.report?.generation || {};
    const script = payload?.report?.recommended_script || {};
    const shootingRows = Array.isArray(payload?.report?.shooting_table?.rows) ? payload.report.shooting_table.rows : [];
    const renderedDraft = await page.locator("#recommendedDraft").innerText();
    const renderedShootingRows = await page.locator("#shootingPlan .shooting-table tbody tr").count();
    const bodyText = await page.locator("body").innerText();

    record("analysis returned http 200", response.status() === 200, response.status());
    record("browser requested full mode", requestBody.analysis_mode === "full", JSON.stringify(requestBody));
    record("paid model was called", generation.paid_api_called === true, JSON.stringify(generation));
    record("deepseek completed research draft", generation.status === "completed_research_draft", generation.status);
    record("complete script returned", typeof script.full_text === "string" && script.full_text.length > 200, script.full_text?.length);
    record("complete script rendered", renderedDraft.includes(String(script.full_text || "").slice(0, 40)), renderedDraft.length);
    record("shooting table rendered", shootingRows.length >= 1 && renderedShootingRows === shootingRows.length, `${shootingRows.length}/${renderedShootingRows}`);
    record("publishing package returned", Array.isArray(payload?.report?.publishing_package?.titles) && payload.report.publishing_package.titles.length >= 1);
    record("new review wording is visible", bodyText.includes("发布前审核"));
    record("old gate wording is absent", !bodyText.includes("门禁"));
    record("paid button resets correctly", (await page.locator("#analyzeButton .button__label").textContent()) === "生成完整脚本");
    record("no console errors", diagnostics.consoleErrors.length === 0, JSON.stringify(diagnostics.consoleErrors));
    record("no page errors", diagnostics.pageErrors.length === 0, JSON.stringify(diagnostics.pageErrors));
    record("no request failures", diagnostics.requestFailures.length === 0, JSON.stringify(diagnostics.requestFailures));
    record("no bad responses", diagnostics.badResponses.length === 0, JSON.stringify(diagnostics.badResponses));

    const screenshot = path.join(outputDir, "desktop_paid_full.png");
    await page.screenshot({ path: screenshot, fullPage: true, animations: "disabled" });
    const result = {
      summary: { total: checks.length, passed: checks.filter((item) => item.ok).length, failed: checks.filter((item) => !item.ok).length },
      checks,
      diagnostics,
      generation: {
        status: generation.status,
        provider: generation.provider,
        model: generation.model,
        paid_api_called: generation.paid_api_called,
        usage: generation.provider_metadata?.usage || null
      },
      screenshot
    };
    fs.writeFileSync(path.join(outputDir, "browser-results.json"), JSON.stringify(result, null, 2), "utf8");
    process.stdout.write(JSON.stringify(result.summary));
    if (result.summary.failed > 0) process.exitCode = 1;
    await context.close();
  } finally {
    await browser.close();
  }
})().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
