"use strict";

const fs = require("fs");
const path = require("path");
const { chromium } = require("G:\\Tools\\gstack\\node_modules\\playwright-core");

const baseUrl = process.argv[2] || "http://127.0.0.1:8787";
const projectRoot = path.resolve(__dirname, "..", "..", "..");
const outputDir = process.argv[3] || path.join(projectRoot, "产出", "验真", "v02_dev");
const mediaFile = process.argv[4] || path.resolve(
  __dirname,
  "..",
  "..",
  "TikTok采集",
  "7648937896535264533",
  "音轨_16k.wav"
);
const executablePath = "C:\\Users\\Administrator\\AppData\\Local\\ms-playwright\\chromium-1228\\chrome-win64\\chrome.exe";
const registeredTikTok = "https://vt.tiktok.com/ZS4BJ6sVM/";
const unknownTikTok = "https://www.tiktok.com/@research/video/7999999999999999999";

fs.mkdirSync(outputDir, { recursive: true });

const checks = [];
const diagnostics = {
  consoleErrors: [],
  pageErrors: [],
  requestFailures: [],
  badResponses: []
};
const screenshots = [];
let phase = "startup";

function record(name, ok, detail = "") {
  checks.push({ phase, name, ok: Boolean(ok), detail: String(detail || "") });
}

function wireDiagnostics(page) {
  page.on("console", (message) => {
    if (message.type() === "error") diagnostics.consoleErrors.push({ phase, text: message.text() });
  });
  page.on("pageerror", (error) => diagnostics.pageErrors.push({ phase, text: error.message }));
  page.on("requestfailed", (request) => diagnostics.requestFailures.push({
    phase,
    url: request.url(),
    error: request.failure()?.errorText || "unknown"
  }));
  page.on("response", (response) => {
    if (response.status() >= 400) diagnostics.badResponses.push({
      phase,
      url: response.url(),
      status: response.status()
    });
  });
}

async function capture(page, name, fullPage = true) {
  const file = path.join(outputDir, `${name}.png`);
  await page.screenshot({ path: file, fullPage, animations: "disabled" });
  screenshots.push(file);
}

async function layoutEvidence(page) {
  return page.evaluate(() => {
    const visible = (element) => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
    };
    const controls = [...document.querySelectorAll("button, input, textarea, select, a, summary")]
      .filter(visible)
      .map((element) => {
        const rect = element.getBoundingClientRect();
        return {
          id: element.id,
          tag: element.tagName,
          left: Math.round(rect.left),
          right: Math.round(rect.right),
          width: Math.round(rect.width),
          clientWidth: element.clientWidth,
          scrollWidth: element.scrollWidth,
          clientHeight: element.clientHeight,
          scrollHeight: element.scrollHeight
        };
      });
    const clipped = controls.filter((item) => (
      item.right > window.innerWidth + 1
      || item.left < -1
      || item.scrollWidth > item.clientWidth + 2
      || item.scrollHeight > item.clientHeight + 2
    ));
    return {
      viewportWidth: window.innerWidth,
      rootScrollWidth: document.documentElement.scrollWidth,
      bodyScrollWidth: document.body.scrollWidth,
      horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
      clipped
    };
  });
}

async function installMockTranscription(page) {
  let requestEvidence = null;
  await page.route("**/api/transcribe", async (route) => {
    const request = route.request();
    requestEvidence = {
      method: request.method(),
      contentType: request.headers()["content-type"] || "",
      bodyBytes: request.postDataBuffer()?.length || 0
    };
    await new Promise((resolve) => setTimeout(resolve, 250));
    await route.fulfill({
      status: 200,
      contentType: "application/json; charset=utf-8",
      body: JSON.stringify({
        status: "completed",
        message: "mock transcription",
        transcript: "一台微型挖掘机依次演示破碎、挖取和抓取动作。",
        provider: "external_api",
        model: "mock-asr",
        language: "zh",
        segments: [],
        segments_status: "provided",
        source: { retained: false },
        confidence: null,
        confidence_status: "not_provided_by_provider"
      })
    });
  });
  return () => requestEvidence;
}

async function runDesktop(browser) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: baseUrl });
  const page = await context.newPage();
  wireDiagnostics(page);
  const getTranscriptionRequest = await installMockTranscription(page);

  phase = "desktop_initial";
  const response = await page.goto(baseUrl, { waitUntil: "networkidle" });
  record("homepage_http_200", response?.status() === 200, response?.status());
  record("v02_brand_visible", (await page.locator(".brand").innerText()).includes("v0.2"));
  record("tiktok_active_copy", (await page.locator("#platformStatus").innerText()).includes("TikTok"));
  await page.locator("#supplementDetails summary").click();
  record("media_input_visible", await page.locator("#mediaFileInput").isVisible());
  record("transcribe_disabled_without_file", await page.locator("#transcribeButton").isDisabled());

  phase = "desktop_transcription";
  await page.locator("#mediaFileInput").setInputFiles(mediaFile);
  record("selected_file_visible", (await page.locator("#mediaFileMeta").innerText()).includes("音轨_16k.wav"));
  record("transcribe_enabled_with_file", !(await page.locator("#transcribeButton").isDisabled()));
  await page.locator("#transcribeButton").click();
  await page.waitForFunction(() => document.querySelector("#transcribeButton .button__label")?.textContent === "转写中");
  record("transcribing_state_visible", await page.locator("#transcribeButton").isDisabled());
  await page.waitForFunction(() => document.querySelector("#transcriptInput")?.value.includes("微型挖掘机"));
  record("transcript_written", (await page.locator("#transcriptInput").inputValue()).includes("破碎、挖取和抓取"));
  record("provider_result_visible", (await page.locator("#transcriptionStatus").innerText()).includes("external_api · mock-asr"));
  const requestEvidence = getTranscriptionRequest();
  record("multipart_upload_sent", requestEvidence?.method === "POST" && requestEvidence.contentType.includes("multipart/form-data"), JSON.stringify(requestEvidence));
  await capture(page, "desktop_transcription");

  phase = "desktop_registered_tiktok";
  await page.locator("#urlInput").fill(registeredTikTok);
  await page.locator("#analyzeButton").click();
  await page.locator("#reportLayout").waitFor({ state: "visible" });
  await page.waitForFunction(() => !document.querySelector("#analyzeButton")?.disabled);
  const bodyText = await page.locator("body").innerText();
  record("recommended_script_visible", await page.locator("#recommendedDraft .deliverable-block").isVisible());
  record("shooting_table_visible", await page.locator("#shootingPlan .shooting-table").isVisible());
  record("research_gate_visible", bodyText.includes("研究稿") && bodyText.includes("禁止直接发布"));
  record("fixture_evidence_visible", bodyText.includes("Miya Home") && bodyText.includes("成人手部"));
  record("canonical_source_link", (await page.locator("#sourceLink").getAttribute("href"))?.includes("7648937896535264533"));
  record("registered_copy_research_visible", await page.locator("#copyScriptButton").isVisible());
  await page.locator("#copyScriptButton").click();
  record("registered_copy_feedback_visible", await page.locator("#copyFeedback").isVisible());
  const desktopLayout = await layoutEvidence(page);
  record("desktop_no_horizontal_overflow", !desktopLayout.horizontalOverflow, JSON.stringify(desktopLayout));
  record("desktop_controls_not_clipped", desktopLayout.clipped.length === 0, JSON.stringify(desktopLayout.clipped));
  await capture(page, "desktop_registered_tiktok");

  phase = "desktop_partial_research";
  await page.locator("#urlInput").fill(unknownTikTok);
  await page.locator("#analyzeButton").click();
  await page.waitForFunction(() => !document.querySelector("#analyzeButton")?.disabled);
  record("partial_research_report_visible", await page.locator("#reportLayout").isVisible());
  record("partial_copy_blocked_without_generated_script", await page.locator("#copyScriptButton").isHidden());
  record("partial_generation_boundary_visible", (await page.locator("#recommendedDraft").innerText()).includes("尚无完整脚本"));
  record("localization_disabled_visible", (await page.locator("#localizationSummary").innerText()).includes("未启用"));

  await context.close();
}

async function runMobile(browser) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await context.newPage();
  wireDiagnostics(page);

  phase = "mobile_initial";
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  await page.locator("#supplementDetails summary").click();
  record("mobile_media_input_visible", await page.locator("#mediaFileInput").isVisible());
  record("mobile_transcribe_button_visible", await page.locator("#transcribeButton").isVisible());
  const initialLayout = await layoutEvidence(page);
  record("mobile_initial_no_overflow", !initialLayout.horizontalOverflow, JSON.stringify(initialLayout));
  record("mobile_initial_controls_not_clipped", initialLayout.clipped.length === 0, JSON.stringify(initialLayout.clipped));

  phase = "mobile_registered_tiktok";
  await page.locator("#urlInput").fill(registeredTikTok);
  await page.locator("#analyzeButton").click();
  await page.locator("#reportLayout").waitFor({ state: "visible" });
  await page.waitForFunction(() => !document.querySelector("#analyzeButton")?.disabled);
  record("mobile_report_visible", await page.locator("#reportLayout").isVisible());
  record("mobile_shooting_rows_visible", await page.locator("#shootingPlan .shooting-table tbody tr").count() >= 5);
  const reportLayout = await layoutEvidence(page);
  record("mobile_report_no_overflow", !reportLayout.horizontalOverflow, JSON.stringify(reportLayout));
  record("mobile_report_controls_not_clipped", reportLayout.clipped.length === 0, JSON.stringify(reportLayout.clipped));
  await capture(page, "mobile_registered_tiktok");

  await context.close();
}

(async () => {
  if (!fs.existsSync(executablePath)) throw new Error(`Chromium not found: ${executablePath}`);
  if (!fs.existsSync(mediaFile)) throw new Error(`Media fixture not found: ${mediaFile}`);
  const browser = await chromium.launch({ headless: true, executablePath });
  try {
    await runDesktop(browser);
    await runMobile(browser);
  } finally {
    await browser.close();
  }

  record("no_console_errors", diagnostics.consoleErrors.length === 0, JSON.stringify(diagnostics.consoleErrors));
  record("no_page_errors", diagnostics.pageErrors.length === 0, JSON.stringify(diagnostics.pageErrors));
  record("no_request_failures", diagnostics.requestFailures.length === 0, JSON.stringify(diagnostics.requestFailures));
  record("no_bad_responses", diagnostics.badResponses.length === 0, JSON.stringify(diagnostics.badResponses));

  const failed = checks.filter((item) => !item.ok);
  const result = {
    baseUrl,
    generatedAt: new Date().toISOString(),
    summary: { total: checks.length, passed: checks.length - failed.length, failed: failed.length },
    checks,
    diagnostics,
    screenshots
  };
  fs.writeFileSync(path.join(outputDir, "browser-results.json"), JSON.stringify(result, null, 2), "utf8");
  process.stdout.write(JSON.stringify(result.summary));
  if (failed.length) process.exitCode = 1;
})().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
