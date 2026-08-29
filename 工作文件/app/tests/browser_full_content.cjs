"use strict";

const fs = require("fs");
const path = require("path");
const playwrightPath = process.env.PLAYWRIGHT_CORE_PATH || "G:\\Tools\\gstack\\node_modules\\playwright-core";
const { chromium, request } = require(playwrightPath);

const baseUrl = (process.env.BASE_URL || process.argv[2] || "http://127.0.0.1:8794").replace(/\/$/, "");
const jobId = process.env.FULL_CONTENT_JOB_ID || process.argv[3] || "";
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

async function preparePage(page, status, manifest, demoResult) {
  await page.route("**/api/acquisition/jobs", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify(status) });
      return;
    }
    await route.continue();
  });
  await page.route(`**/api/acquisition/jobs/${jobId}/analyze`, async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(demoResult) });
      return;
    }
    await route.continue();
  });
  await page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded" });
  await page.locator("#urlInput").fill(manifest.canonical_url);
  await page.locator("#analyzeButton").click();
  await page.locator("#fullContentLayer .full-content").waitFor({ state: "visible" });
}

async function openAndWait(page, section) {
  const details = page.locator(`[data-full-content="${section}"]`);
  await details.evaluate((node) => { node.open = true; });
  if (section === "transcript") {
    await details.locator(".full-content__transcript-text").waitFor({ state: "visible", timeout: 120000 });
  } else if (section !== "original-script") {
    await details.locator(".full-content__row").first().waitFor({ state: "visible", timeout: 120000 });
  }
  return details;
}

async function capture(page, name) {
  if (!outputDir) return;
  fs.mkdirSync(outputDir, { recursive: true });
  await page.screenshot({ path: path.join(outputDir, `${name}.png`), fullPage: true, animations: "disabled" });
}

async function captureLocator(locator, name) {
  if (!outputDir) return;
  fs.mkdirSync(outputDir, { recursive: true });
  await locator.screenshot({ path: path.join(outputDir, `${name}.png`), animations: "disabled" });
}

(async () => {
  if (!jobId) throw new Error("请通过 FULL_CONTENT_JOB_ID 指定真实完成任务");
  if (!executablePath) throw new Error("找不到可用 Chromium");
  const browser = await chromium.launch({ executablePath, headless: true });
  try {
    const api = await request.newContext({ baseURL: baseUrl });
    const [statusResponse, manifestResponse, demoResponse, transcriptTextResponse] = await Promise.all([
      api.get(`/api/acquisition/jobs/${jobId}`),
      api.get(`/api/acquisition/jobs/${jobId}/manifest`),
      api.get("/api/demo"),
      api.get(`/api/acquisition/jobs/${jobId}/full-content/transcript-text`)
    ]);
    if (!statusResponse.ok() || !manifestResponse.ok() || !demoResponse.ok() || !transcriptTextResponse.ok()) throw new Error("验收资料读取失败");
    const status = await statusResponse.json();
    const manifest = await manifestResponse.json();
    const demo = await demoResponse.json();
    const transcriptText = await transcriptTextResponse.json();
    await api.dispose();

    const context = await browser.newContext({ viewport: { width: 1365, height: 900 } });
    await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: baseUrl });
    const page = await context.newPage();
    wireDiagnostics(page);
    const transcriptRequests = [];
    page.on("request", (request) => {
      if (
        request.method() === "GET"
        && request.url().includes(`/api/acquisition/jobs/${jobId}/full-content/transcript-text`)
      ) transcriptRequests.push(request.url());
    });
    await preparePage(page, status, manifest, demo.result);

    const bridgeState = await page.evaluate(() => {
      const bridge = window.project024AgentBridge;
      const scriptContext = bridge?.getContext("script");
      return {
        exists: Boolean(bridge && typeof bridge.getContext === "function" && typeof bridge.applyDraft === "function"),
        draft: scriptContext?.draft,
        sourceUrl: scriptContext?.context?.source_url,
        strategy: bridge?.getContext("strategy")
      };
    });
    record("Agent 桥接对象与当前上下文可用", (
      bridgeState.exists
      && typeof bridgeState.draft === "string"
      && bridgeState.sourceUrl === manifest.canonical_url
      && typeof bridgeState.strategy?.draft === "string"
      && bridgeState.strategy?.context?.source_url === manifest.canonical_url
    ), JSON.stringify(bridgeState));
    const agentDraft = "这是 Agent 修改后的发布脚本。开头先说明受众正在面对的具体问题，中段给出可以逐项执行的方法与核对依据，结尾保留清晰的行动引导和发布前复核提醒。脚本更新后应立即成为当前完整原创稿，并继续支持后续复制、修改和发布实验。";
    const appliedDraft = await page.evaluate((draft) => {
      window.project024AgentBridge.applyDraft("script", draft);
      return {
        draft: window.project024AgentBridge.getContext("script").draft,
        contentSummary: currentPublishDraft?.content_summary
      };
    }, agentDraft);
    const agentOriginal = page.locator('[data-full-content="original-script"]');
    await agentOriginal.evaluate((node) => { node.open = true; });
    const visibleAgentDraft = await agentOriginal.locator(".full-content__script").textContent();
    record("Agent 新脚本应用后页面显示新稿", (
      appliedDraft.draft === agentDraft
      && appliedDraft.contentSummary === agentDraft
      && visibleAgentDraft === agentDraft
    ));
    await agentOriginal.evaluate((node) => { node.open = false; });

    const summaries = await page.locator("#fullContentLayer summary").allTextContents();
    record("四个完整内容入口齐全", [
      "查看完整口播全文", "查看画面文字全文", "查看完整内容时间线", "查看完整原创稿"
    ].every((label) => summaries.includes(label)), summaries.join(" | "));

    const transcriptDetails = page.locator('[data-full-content="transcript"]');
    record("完整口播默认折叠", !(await transcriptDetails.evaluate((node) => node.open)));
    record("折叠时不提前读取完整口播", transcriptRequests.length === 0, String(transcriptRequests.length));
    const transcript = await openAndWait(page, "transcript");
    const transcriptBlock = transcript.locator(".full-content__transcript-text");
    const displayedTranscript = await transcriptBlock.textContent();
    record("完整口播一次显示全部纯文本", displayedTranscript === transcriptText.text, `${displayedTranscript.length}/${transcriptText.character_count}`);
    record("完整口播不渲染逐段时间码或继续加载", (
      (await transcript.locator("time, .full-content__time, .full-content__row").count()) === 0
      && (await transcript.locator("button", { hasText: "继续加载" }).count()) === 0
    ));
    const transcriptLayout = await transcriptBlock.evaluate((node) => ({
      overflowY: getComputedStyle(node).overflowY,
      clientHeight: node.clientHeight,
      scrollHeight: node.scrollHeight
    }));
    record("完整口播文本块无内部纵向滚动", (
      !["auto", "scroll"].includes(transcriptLayout.overflowY)
      && transcriptLayout.scrollHeight <= transcriptLayout.clientHeight + 1
    ), JSON.stringify(transcriptLayout));
    await transcript.locator("button", { hasText: "复制全文" }).click();
    await page.waitForFunction(() => document.querySelector("#copyFeedback")?.textContent?.includes("完整口播全文已复制"));
    const copiedTranscript = await page.evaluate(() => navigator.clipboard.readText());
    record("完整口播复制成功", copiedTranscript === transcriptText.text, `${copiedTranscript.length}/${transcriptText.character_count}`);
    record("展示与复制共用一次全文请求", transcriptRequests.length === 1, transcriptRequests.join(" | "));

    const ocr = await openAndWait(page, "ocr");
    const frameLink = ocr.locator(".full-content__frame-link").first();
    const frameHref = await frameLink.getAttribute("href");
    const frameResponse = await page.request.get(`${baseUrl}${frameHref}`);
    record("画面文字带时间、置信度和可打开帧", (
      (await ocr.locator(".full-content__time").count()) > 0
      && (await ocr.locator(".full-content__confidence").count()) > 0
      && frameResponse.ok()
      && frameResponse.headers()["content-type"].startsWith("image/jpeg")
    ), frameHref || "");

    const timeline = await openAndWait(page, "timeline");
    const timelineText = await timeline.textContent();
    record("时间线对齐口播和画面文字", timelineText.includes("口播") && timelineText.includes("画面文字"), timelineText.slice(0, 500));
    record("时间线分开显示画面观察与可能推断", timelineText.includes("画面观察") && timelineText.includes("可能推断（需复核）"), timelineText.slice(0, 500));
    const visualFrameLink = timeline.locator(".full-content__inline-frame-link").first();
    const visualFrameHref = await visualFrameLink.getAttribute("href");
    const visualFrameResponse = await page.request.get(`${baseUrl}${visualFrameHref}`);
    record("页面画面观察可打开对应帧", (
      visualFrameResponse.ok()
      && visualFrameResponse.headers()["content-type"].startsWith("image/jpeg")
    ), visualFrameHref || "");

    const timelineItems = [];
    let timelineOffset = 0;
    let timelinePayload;
    do {
      const response = await page.request.get(
        `${baseUrl}/api/acquisition/jobs/${jobId}/full-content/timeline?offset=${timelineOffset}&limit=100`
      );
      if (!response.ok()) throw new Error(`时间线分页读取失败：${response.status()}`);
      timelinePayload = await response.json();
      timelineItems.push(...timelinePayload.items);
      timelineOffset += timelinePayload.items.length;
    } while (timelinePayload.has_more);
    const visualEvidence = timelineItems.flatMap((item) => item.visual_evidence || []);
    const visualUrls = [...new Set(visualEvidence.map((item) => item.artifact_url).filter(Boolean))];
    const visualResponses = await Promise.all(visualUrls.map((url) => page.request.get(`${baseUrl}${url}`)));
    record("全部视觉证据都有可打开 JPEG", (
      timelinePayload.vision_status === "completed"
      && visualEvidence.length > 0
      && visualUrls.length > 0
      && visualEvidence.every((item) => item.artifact_url && item.frame_id)
      && visualResponses.every((response) => response.ok() && response.headers()["content-type"].startsWith("image/jpeg"))
    ), `${visualEvidence.length} 条证据 / ${visualUrls.length} 张帧`);
    await captureLocator(timeline, "full-content-timeline-desktop");

    const original = await openAndWait(page, "original-script");
    const script = await original.locator(".full-content__script").textContent();
    record("完整原创稿可展开", script.trim().length > 80, String(script.trim().length));
    await original.locator("button", { hasText: "复制原创稿" }).click();
    const copiedScript = await page.evaluate(() => navigator.clipboard.readText());
    record("完整原创稿复制成功", copiedScript.trim() === script.trim(), String(copiedScript.trim().length));
    await capture(page, "full-content-desktop");
    await context.close();

    const mobileContext = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const mobilePage = await mobileContext.newPage();
    wireDiagnostics(mobilePage);
    await preparePage(mobilePage, status, manifest, demo.result);
    const mobileTranscript = await openAndWait(mobilePage, "transcript");
    const mobileTranscriptLayout = await mobileTranscript.locator(".full-content__transcript-text").evaluate((node) => ({
      overflowY: getComputedStyle(node).overflowY,
      clientHeight: node.clientHeight,
      scrollHeight: node.scrollHeight,
      timeCount: node.parentElement?.querySelectorAll("time, .full-content__time").length || 0
    }));
    record("390px 完整口播无时间码或内部滚动", (
      mobileTranscriptLayout.timeCount === 0
      && !["auto", "scroll"].includes(mobileTranscriptLayout.overflowY)
      && mobileTranscriptLayout.scrollHeight <= mobileTranscriptLayout.clientHeight + 1
    ), JSON.stringify(mobileTranscriptLayout));
    await mobileTranscript.evaluate((node) => { node.open = false; });
    const mobileTimeline = await openAndWait(mobilePage, "timeline");
    const layout = await mobilePage.evaluate(() => ({
      viewport: window.innerWidth,
      root: document.documentElement.scrollWidth,
      body: document.body.scrollWidth,
      buttonWidths: Array.from(document.querySelectorAll("#fullContentLayer button")).map((button) => ({
        width: button.getBoundingClientRect().width,
        parent: button.parentElement?.getBoundingClientRect().width || 0
      }))
    }));
    record("390px 全文区域无横向溢出或按钮越界", (
      layout.root <= layout.viewport + 1
      && layout.body <= layout.viewport + 1
      && layout.buttonWidths.every((item) => item.width <= item.parent + 1)
    ), JSON.stringify(layout));
    await captureLocator(mobileTimeline, "full-content-timeline-mobile-390");
    await capture(mobilePage, "full-content-mobile-390");
    await mobileContext.close();
  } finally {
    await browser.close();
  }

  record("控制台无错误", diagnostics.consoleErrors.length === 0, diagnostics.consoleErrors.join(" | "));
  record("页面无异常", diagnostics.pageErrors.length === 0, diagnostics.pageErrors.join(" | "));
  record("请求无网络失败", diagnostics.requestFailures.length === 0, JSON.stringify(diagnostics.requestFailures));
  record("响应无意外 4xx/5xx", diagnostics.badResponses.length === 0, JSON.stringify(diagnostics.badResponses));

  const result = { baseUrl, jobId, passed: checks.filter((item) => item.ok).length, failed: checks.filter((item) => !item.ok).length, checks, diagnostics };
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
