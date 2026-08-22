"use strict";

const fs = require("fs");
const path = require("path");
const { chromium } = require("G:\\Tools\\gstack\\node_modules\\playwright-core");
const sharp = require("G:\\Tools\\gstack\\node_modules\\sharp");

const baseUrl = process.argv[2] || "http://127.0.0.1:8791";
const outputDir = process.argv[3] || path.resolve(
  __dirname,
  "..",
  "..",
  "..",
  "产出",
  "验真",
  "p1_auto_acquisition"
);
const runMode = String(process.argv[4] || "all").toLowerCase();
if (!["all", "desktop"].includes(runMode)) {
  throw new Error(`Unsupported browser verification mode: ${runMode}`);
}
const executablePath = "C:\\Users\\Administrator\\AppData\\Local\\ms-playwright\\chromium-1228\\chrome-win64\\chrome.exe";
const liveSource = process.argv[5] || "https://www.tiktok.com/@jackeyephone/video/6898699405898059010";
const expectedLivePlatform = /(?:^|\.)douyin\.com$/i.test(new URL(liveSource).hostname)
  ? "douyin"
  : "tiktok";

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

async function resetScroll(page) {
  await page.evaluate(() => {
    document.documentElement.style.scrollBehavior = "auto";
    document.body.style.scrollBehavior = "auto";
    document.documentElement.scrollTop = 0;
    document.body.scrollTop = 0;
    window.scrollTo(0, 0);
  });
  await page.waitForFunction(() => Math.abs(window.scrollY) < 1);
}

async function capture(page, name) {
  await resetScroll(page);
  const file = path.join(outputDir, `${name}.png`);
  await page.screenshot({ path: file, fullPage: true, animations: "disabled" });
  screenshots.push(file);
}

async function captureLocator(page, locator, name) {
  const file = path.join(outputDir, `${name}.png`);
  const style = await page.addStyleTag({
    content: ".topbar{position:absolute!important}.skip-link,.toast,.copy-feedback{display:none!important}"
  });
  try {
    await locator.scrollIntoViewIfNeeded();
    await page.waitForTimeout(50);
    await locator.screenshot({ path: file, animations: "disabled" });
    screenshots.push(file);
  } finally {
    await style.evaluate((node) => node.remove());
  }
}

async function captureIsolatedComponent(page, locator, name) {
  const file = path.join(outputDir, `${name}.png`);
  const overlayId = "verificationCaptureOverlay";
  await locator.evaluate((element, id) => {
    document.getElementById(id)?.remove();
    const overlay = document.createElement("div");
    overlay.id = id;
    overlay.style.cssText = "position:fixed;inset:0;z-index:2147483647;overflow:auto;background:#f7f9f8;padding:24px;";
    const clone = element.cloneNode(true);
    const width = Math.min(element.getBoundingClientRect().width, window.innerWidth - 48);
    clone.style.width = `${width}px`;
    clone.style.margin = "0 auto";
    overlay.appendChild(clone);
    document.body.appendChild(overlay);
  }, overlayId);
  try {
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await page.screenshot({ path: file, animations: "disabled" });
    const stats = await sharp(file).stats();
    if (!stats.channels.slice(0, 3).some((channel) => channel.stdev > 1)) {
      throw new Error(`Screenshot is blank: ${name}`);
    }
    screenshots.push(file);
  } finally {
    await page.locator(`#${overlayId}`).evaluate((node) => node.remove()).catch(() => {});
  }
}

async function captureViewport(page, locator, name) {
  const file = path.join(outputDir, `${name}.png`);
  const style = await page.addStyleTag({
    content: ".topbar{position:absolute!important}.skip-link,.toast,.copy-feedback{display:none!important}"
  });
  try {
    const region = await locator.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      const documentHeight = document.documentElement.scrollHeight;
      const y = Math.max(0, window.scrollY + rect.top - 96);
      return {
        y,
        width: document.documentElement.clientWidth,
        height: Math.max(1, Math.min(window.innerHeight, documentHeight - y)),
        documentHeight
      };
    });
    await resetScroll(page);
    const fullPage = await page.screenshot({ fullPage: true, animations: "disabled" });
    const metadata = await sharp(fullPage).metadata();
    const scaleY = metadata.height / region.documentHeight;
    const top = Math.max(0, Math.min(metadata.height - 1, Math.floor(region.y * scaleY)));
    const height = Math.max(1, Math.min(metadata.height - top, Math.ceil(region.height * scaleY)));
    await sharp(fullPage)
      .extract({ left: 0, top, width: metadata.width, height })
      .png()
      .toFile(file);
    const stats = await sharp(file).stats();
    if (!stats.channels.slice(0, 3).some((channel) => channel.stdev > 1)) {
      throw new Error(`Screenshot is blank: ${name}; region=${JSON.stringify(region)}; image=${metadata.width}x${metadata.height}; top=${top}; height=${height}`);
    }
    screenshots.push(file);
  } finally {
    await style.evaluate((node) => node.remove());
  }
}

async function layoutEvidence(page) {
  return page.evaluate(() => {
    const visible = (element) => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      let ancestor = element.parentElement;
      while (ancestor) {
        if (ancestor.tagName === "DETAILS" && !ancestor.open) {
          const summary = ancestor.querySelector(":scope > summary");
          if (!summary?.contains(element)) return false;
        }
        ancestor = ancestor.parentElement;
      }
      return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
    };
    const elements = [...document.querySelectorAll("button, input, textarea, select, a, summary")]
      .filter(visible);
    const boxes = elements.map((element) => {
      const rect = element.getBoundingClientRect();
      let ancestor = element;
      let floating = false;
      while (ancestor) {
        const position = getComputedStyle(ancestor).position;
        if (position === "fixed" || position === "sticky") {
          floating = true;
          break;
        }
        ancestor = ancestor.parentElement;
      }
      return {
        id: element.id || element.tagName,
        tag: element.tagName,
        className: element.className || "",
        text: (element.textContent || "").trim().slice(0, 48),
        floating,
        left: rect.left,
        right: rect.right,
        top: rect.top,
        bottom: rect.bottom,
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
        clientHeight: element.clientHeight,
        scrollHeight: element.scrollHeight
      };
    });
    const clipped = boxes.filter((box) => (
      box.left < -1
      || box.right > window.innerWidth + 1
      || (!["INPUT", "TEXTAREA", "SELECT"].includes(box.tag) && box.scrollWidth > box.clientWidth + 2)
      || box.scrollHeight > box.clientHeight + 2
    ));
    const overlaps = [];
    for (let left = 0; left < boxes.length; left += 1) {
      for (let right = left + 1; right < boxes.length; right += 1) {
        const a = boxes[left];
        const b = boxes[right];
        const horizontal = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        const vertical = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        if (!a.floating && !b.floating && horizontal > 2 && vertical > 2) overlaps.push({
          left: { id: a.id, tag: a.tag, className: a.className, text: a.text },
          right: { id: b.id, tag: b.tag, className: b.className, text: b.text },
          horizontal,
          vertical
        });
      }
    }
    return {
      viewportWidth: window.innerWidth,
      rootScrollWidth: document.documentElement.scrollWidth,
      horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
      clipped,
      overlaps
    };
  });
}

function analysisResponse(page) {
  return page.waitForResponse(
    (response) => /\/api\/acquisition\/jobs\/[^/]+\/analyze$/.test(response.url()),
    { timeout: 180000 }
  );
}

function pause(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function installRequestDelays(page) {
  await page.route("**/api/acquisition/jobs", async (route) => {
    if (route.request().method() === "POST") await pause(180);
    await route.continue();
  });
  await page.route(/\/api\/acquisition\/jobs\/[^/]+\/analyze$/, async (route) => {
    await pause(220);
    await route.continue();
  });
}

async function waitForButtonLabel(page, expected, timeout = 5000) {
  try {
    await page.waitForFunction(
      (label) => document.querySelector("#analyzeButton .button__label")?.textContent?.trim() === label,
      expected,
      { timeout }
    );
    return true;
  } catch {
    return false;
  }
}

async function submitAndWait(page, name) {
  const responsePromise = analysisResponse(page);
  await page.locator("#analyzeButton").click();
  record(`${name}: submit status visible`, await waitForButtonLabel(page, "提交中"));
  record(`${name}: analyzing status visible`, await waitForButtonLabel(page, "解读中", 180000));
  const response = await responsePromise;
  const payload = await response.json();
  await page.locator("#reportLayout").waitFor({ state: "visible", timeout: 10000 });
  await page.waitForFunction(() => !document.querySelector("#analyzeButton")?.disabled);
  return { response, payload };
}

function numericMetricKeys(payload) {
  return Object.entries(payload?.source?.metrics || {})
    .filter(([, value]) => typeof value === "number" && Number.isFinite(value))
    .map(([key]) => key);
}

async function verifyMetricEvidence(page, payload, name) {
  const metricKeys = numericMetricKeys(payload);
  const acquisition = payload?.diagnostics?.acquisition || {};
  const evidenceControls = page.locator("details.metric-evidence");
  const renderedKeys = await evidenceControls.evaluateAll((nodes) => nodes.map((node) => node.dataset.evidenceFor));
  record(`${name}: numeric metrics available`, metricKeys.length > 0, JSON.stringify(metricKeys));
  record(
    `${name}: one adjacent evidence control per metric`,
    metricKeys.every((key) => renderedKeys.filter((rendered) => rendered === key).length === 1),
    JSON.stringify(renderedKeys)
  );
  record(
    `${name}: metric evidence defaults closed`,
    await evidenceControls.evaluateAll((nodes) => nodes.every((node) => !node.open))
  );

  for (let index = 0; index < metricKeys.length; index += 1) {
    const key = metricKeys[index];
    const details = page.locator(`details.metric-evidence[data-evidence-for="${key}"]`);
    const adjacent = await details.evaluate((node) => Boolean(
      node.closest(".metric-row")?.querySelector("strong.metric-row__value")
    ));
    record(`${name}: ${key} evidence is beside value`, adjacent);

    await details.locator("summary").click();
    await page.waitForFunction(
      (metricKey) => document.querySelector(`details.metric-evidence[data-evidence-for="${metricKey}"]`)?.open,
      key
    );
    const expanded = await details.evaluate((node) => ({
      text: node.innerText,
      links: [...node.querySelectorAll("a")].map((link) => ({ text: link.textContent.trim(), href: link.href }))
    }));
    const fieldsPresent = expanded.text.includes("资料来源")
      && expanded.text.includes("采集时间")
      && !expanded.text.includes("未记录")
      && !expanded.text.includes(acquisition.evidence_strength || "__missing_strength__");
    const internalValuesHidden = !expanded.text.includes(acquisition.job_id || "__missing_job_id__")
      && !expanded.text.includes(acquisition?.source_artifact?.sha256 || "__missing_hash__")
      && expanded.links.length === 0;
    record(`${name}: ${key} evidence is compact and localized`, fieldsPresent, expanded.text);
    record(
      `${name}: ${key} hides internal ids, hashes, and api links`,
      internalValuesHidden,
      JSON.stringify(expanded)
    );

    if (index === 0) {
      const metricSection = details.locator("xpath=ancestor::section[contains(concat(' ', normalize-space(@class), ' '), ' metric-section ')]");
      await captureIsolatedComponent(page, metricSection, `${name}_metric_evidence_open`);
      const layout = await layoutEvidence(page);
      record(`${name}: expanded evidence has no overflow`, !layout.horizontalOverflow && layout.clipped.length === 0, JSON.stringify(layout));
    }

    await details.locator("summary").click();
  }

  record(
    `${name}: metric evidence closes cleanly`,
    await evidenceControls.evaluateAll((nodes) => nodes.every((node) => !node.open))
  );
}

async function verifyResultBoundary(page, payload, name, expectFullPackage) {
  const reportText = await page.locator("#reportLayout").innerText();
  const reportContent = await page.locator("#reportLayout").textContent();
  const rawEnums = ["runtime_public_snapshot", "reviewed_fixture", "needs_human_review", "research_draft", "publish_ready"];
  const internalMarkers = ["补充信息", "json_object", "prompt_tokens", "completion_tokens", "/api/acquisition/jobs/", "local_asr", "acq_"];
  record(`${name}: quick conclusion uses visible user language`, (await page.locator("#quickSummary").innerText()).trim().length > 10);
  record(`${name}: technical enums are translated`, rawEnums.every((value) => !reportText.includes(value)), reportText);
  record(`${name}: customer report hides internal metadata`, internalMarkers.every((value) => !reportContent.includes(value)), reportContent);
  record(`${name}: customer report hides 64 character hashes`, !/\b[a-f0-9]{64}\b/i.test(reportContent), reportContent);

  const stageScript = (await page.locator("#stageScriptStatus").innerText()).trim();
  const stageShooting = (await page.locator("#stageShootingStatus").innerText()).trim();
  const stagePublish = (await page.locator("#stagePublishStatus").innerText()).trim();
  const draftText = await page.locator("#recommendedDraft").innerText();
  const shootingRows = await page.locator("#shootingPlan .shooting-table tbody tr").count();
  const deliveryText = await page.locator("#deliverySummary").innerText();

  if (!expectFullPackage) {
    record(`${name}: incomplete result stays partial`, payload.status === "partial", payload.status);
    record(`${name}: missing draft is stated plainly`, /完整推荐稿尚未形成|尚无完整脚本/.test(draftText), draftText);
    record(`${name}: missing shooting plan is not fabricated`, shootingRows === 0, shootingRows);
    record(`${name}: incomplete stages are not marked complete`, stageScript !== "已通关" && stagePublish !== "已通关", JSON.stringify({ stageScript, stagePublish }));
    record(`${name}: publishing remains a review decision`, deliveryText.includes("发布前核对"), deliveryText);
    return;
  }

  const expectedRows = payload?.report?.shooting_table?.rows?.length
    || payload?.report?.shooting_plan?.rows?.length
    || payload?.report?.content_package?.script?.segments?.length
    || 0;
  const expectedScript = payload?.report?.recommended_script?.full_text
    || payload?.report?.recommended_draft?.full_text
    || payload?.report?.content_package?.script?.full_text
    || "";
  const publishingText = await page.locator("#publishingPackage").innerText();
  const pathwayText = (await page.locator("#pathwaySummary").innerText()).trim();
  const copyLabel = (await page.locator("#copyScriptButton").innerText()).trim();

  record(`${name}: completed reviewed fixture retained`, payload.status === "completed", payload.status);
  record(`${name}: full original draft is visible`, expectedScript.length > 200 && draftText.includes(expectedScript.slice(0, 40)), `expected=${expectedScript.length}; rendered=${draftText.length}`);
  record(`${name}: shooting table is complete`, expectedRows >= 1 && shootingRows === expectedRows, `expected=${expectedRows}; rendered=${shootingRows}`);
  record(`${name}: publishing package is visible`, publishingText.trim().length > 40, publishingText);
  record(`${name}: human review is stated plainly`, deliveryText.includes("可参考，发布前核对"), deliveryText);
  record(`${name}: script and shooting gates are complete`, stageScript === "已通关" && stageShooting === "已通关", JSON.stringify({ stageScript, stageShooting }));
  record(`${name}: publishing gate remains pending`, stagePublish === "待补充", stagePublish);
  record(`${name}: pathway does not claim four completed gates`, pathwayText.includes("3 / 4"), pathwayText);
  record(`${name}: copy action preserves research boundary`, copyLabel === "复制研究稿", copyLabel);
}

async function runLivePath(page, name) {
  const requests = [];
  const listener = (request) => {
    if (request.url().includes("/api/")) requests.push({ method: request.method(), url: request.url() });
  };
  page.on("request", listener);

  await page.locator("#urlInput").fill(liveSource);
  record(`${name}: transcript starts empty`, (await page.locator("#transcriptInput").inputValue()) === "");
  const { response, payload } = await submitAndWait(page, name);

  const acquisition = payload?.diagnostics?.acquisition || {};
  record(`${name}: analysis http 200`, response.status() === 200, response.status());
  record(`${name}: expected live platform`, payload?.platform === expectedLivePlatform, payload?.platform);
  record(`${name}: automatic transcript provenance`, acquisition?.transcript?.source === "local_asr", JSON.stringify(acquisition?.transcript));
  record(`${name}: transcript hash retained`, /^[a-f0-9]{64}$/.test(acquisition?.transcript?.sha256 || ""));
  const generation = payload?.diagnostics?.generation || {};
  record(`${name}: paid content generation not called`, generation.paid_api_called === false, JSON.stringify(generation));
  record(`${name}: client transcript remains empty`, (await page.locator("#transcriptInput").inputValue()) === "");
  record(`${name}: report visible`, await page.locator("#reportLayout").isVisible());
  record(`${name}: button reset`, (await page.locator("#analyzeButton .button__label").textContent()) === "快速看懂");
  record(`${name}: acquisition submit used`, requests.some((item) => item.method === "POST" && /\/api\/acquisition\/jobs$/.test(item.url)), JSON.stringify(requests));
  record(`${name}: acquisition analysis used`, requests.some((item) => item.method === "POST" && /\/analyze$/.test(item.url) && item.url.includes("/acquisition/jobs/")), JSON.stringify(requests));
  record(`${name}: legacy analyze not used`, !requests.some((item) => item.method === "POST" && /\/api\/analyze$/.test(item.url)), JSON.stringify(requests));
  await verifyResultBoundary(page, payload, `${name}_live`, false);
  await verifyMetricEvidence(page, payload, `${name}_live`);

  await resetScroll(page);
  const layout = await layoutEvidence(page);
  record(`${name}: no horizontal overflow`, !layout.horizontalOverflow, JSON.stringify(layout));
  record(`${name}: controls not clipped`, layout.clipped.length === 0, JSON.stringify(layout.clipped));
  record(`${name}: controls do not overlap`, layout.overlaps.length === 0, JSON.stringify(layout.overlaps));
  await capture(page, `${name}_live_auto`);
  page.off("request", listener);
}

async function runFixturePath(page, name) {
  const requests = [];
  const listener = (request) => {
    if (request.url().includes("/api/")) requests.push({ method: request.method(), url: request.url() });
  };
  page.on("request", listener);

  await page.locator("#demoButton").click();
  await page.waitForFunction(() => document.querySelector("#urlInput")?.value.includes("douyin.com/video/"));
  record(`${name}: fixture transcript remains empty`, (await page.locator("#transcriptInput").inputValue()) === "");
  const { response, payload } = await submitAndWait(page, name);
  const acquisition = payload?.diagnostics?.acquisition || {};
  record(`${name}: fixture analysis http 200`, response.status() === 200, response.status());
  record(`${name}: registered fixture completed`, payload.status === "completed", payload.status);
  record(`${name}: fixture provenance retained`, acquisition.acquisition_mode === "registered_fixture", JSON.stringify(acquisition));
  record(`${name}: acquisition submit used`, requests.some((item) => item.method === "POST" && /\/api\/acquisition\/jobs$/.test(item.url)), JSON.stringify(requests));
  record(`${name}: acquisition analysis used`, requests.some((item) => item.method === "POST" && /\/analyze$/.test(item.url) && item.url.includes("/acquisition/jobs/")), JSON.stringify(requests));
  record(`${name}: legacy analyze not used`, !requests.some((item) => item.method === "POST" && /\/api\/analyze$/.test(item.url)), JSON.stringify(requests));
  await verifyResultBoundary(page, payload, `${name}_fixture`, true);
  await verifyMetricEvidence(page, payload, `${name}_fixture`);

  await resetScroll(page);
  const layout = await layoutEvidence(page);
  record(`${name}: no horizontal overflow`, !layout.horizontalOverflow, JSON.stringify(layout));
  record(`${name}: controls not clipped`, layout.clipped.length === 0, JSON.stringify(layout.clipped));
  record(`${name}: controls do not overlap`, layout.overlaps.length === 0, JSON.stringify(layout.overlaps));
  await captureLocator(page, page.locator("#stageScript"), `${name}_complete_script`);
  await captureLocator(page, page.locator("#stageShooting"), `${name}_shooting_table`);
  await captureViewport(page, page.locator("#stagePublish .stage__heading"), `${name}_publish_review`);
  await capture(page, `${name}_fixture_auto`);
  page.off("request", listener);
}

async function runDesktop(browser) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  wireDiagnostics(page);
  await installRequestDelays(page);

  phase = "desktop_startup";
  const homepage = await page.goto(baseUrl, { waitUntil: "networkidle" });
  record("desktop: homepage http 200", homepage?.status() === 200, homepage?.status());
  record("desktop: current brand", (await page.locator(".brand__name").textContent()) === "自媒体通关搭档");

  phase = "desktop_live";
  await runLivePath(page, "desktop");

  phase = "desktop_fixture";
  await runFixturePath(page, "desktop");

  await context.close();
}

async function runMobile(browser) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await context.newPage();
  wireDiagnostics(page);
  await installRequestDelays(page);

  phase = "mobile_startup";
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  record("mobile: input visible", await page.locator("#urlInput").isVisible());

  phase = "mobile_live";
  await runLivePath(page, "mobile");

  phase = "mobile_fixture";
  await runFixturePath(page, "mobile");
  await context.close();
}

(async () => {
  if (!fs.existsSync(executablePath)) throw new Error(`Chromium not found: ${executablePath}`);
  const browser = await chromium.launch({ executablePath, headless: true });
  try {
    await runDesktop(browser);
    if (runMode === "all") await runMobile(browser);
  } finally {
    await browser.close();
  }

  record("no console errors", diagnostics.consoleErrors.length === 0, JSON.stringify(diagnostics.consoleErrors));
  record("no page errors", diagnostics.pageErrors.length === 0, JSON.stringify(diagnostics.pageErrors));
  record("no request failures", diagnostics.requestFailures.length === 0, JSON.stringify(diagnostics.requestFailures));
  record("no bad responses", diagnostics.badResponses.length === 0, JSON.stringify(diagnostics.badResponses));

  const failed = checks.filter((item) => !item.ok);
  const result = {
    baseUrl,
    runMode,
    expectedLivePlatform,
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
