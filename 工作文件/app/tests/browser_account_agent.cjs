"use strict";

const fs = require("fs");
const path = require("path");
const { chromium } = require("G:\\Tools\\gstack\\node_modules\\playwright-core");

const baseUrl = (process.env.BASE_URL || process.argv[2] || "http://127.0.0.1:8796").replace(/\/$/, "");
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
const runSuffix = `${Date.now()}_${Math.random().toString(16).slice(2, 8)}`;

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

const csv = `日期,投稿量,总播放量,总点赞量,总评论量,总分享量,5秒完播率,2秒跳出率,平均播放时长,粉丝净增
2026-08-19,1,1200,80,15,8,70%,21%,43.2秒,12
2026-08-20,1,1000,66,12,6,68%,23%,40秒,9
2026-08-21,1,700,30,5,2,55%,37%,31秒,3
2026-08-22,1,600,24,4,1,51%,42%,27秒,1
`;

(async () => {
  if (!executablePath) throw new Error("找不到可用 Chromium");
  const browser = await chromium.launch({ executablePath, headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1365, height: 900 } });
    const page = await context.newPage();
    wireDiagnostics(page);
    let agentRequests = 0;
    await page.route("**/api/agent/status", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ configured: true, provider: "mock", model: "mock-agent" })
      });
    });
    await page.route("**/api/agent/chat", async (route) => {
      agentRequests += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          reply: "判断完成：本轮只保留一个变量，已生成可执行策略。",
          updated_text: "本轮只测试首帧和第一句话，其他变量保持不变；发布后按同一观察窗口记录真实数据。",
          next_actions: ["核对首帧", "按同一窗口回填"]
        })
      });
    });
    await page.goto(`${baseUrl}/static/douyin.html`, { waitUntil: "networkidle" });
    await page.locator("body[data-douyin-ready='true']").waitFor();
    record("显示连接抖音账号按钮", await page.locator("#openAccountDialog").isVisible());

    await page.locator("#openAccountDialog").click();
    await page.locator("#accountDialog").waitFor({ state: "visible" });
    record("自动连接是账号主路径", await page.locator("#browserImport.button--primary").count() === 1);
    record("文件导入默认收在兜底区", !(await page.locator("#creatorDataFile").isVisible()) && (await page.locator(".account-fallback").textContent()).includes("自动连接失败"));
    const officialConnectionText = await page.locator("#officialConnectionStatus").textContent();
    record(
      "官方授权如实显示筹备条件",
      officialConnectionText.includes("官方") && (officialConnectionText.includes("审核") || officialConnectionText.includes("筹备")),
      officialConnectionText
    );
    await capture(page, "account-connection-desktop");
    await page.locator("#accountDisplayName").fill("项目024验收账号");
    await page.locator("#accountDouyinId").fill(`project024_demo_${runSuffix}`);
    await page.locator("#accountStrategy").fill("每周测试一个开头变量，并按同一窗口复盘。");
    await page.locator("#creatorDataFile").setInputFiles({
      name: "creator-center.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(csv, "utf8")
    });
    const accountWriteResponse = page.waitForResponse((response) =>
      ["POST", "PATCH"].includes(response.request().method()) &&
        /^\/api\/douyin\/accounts(?:\/[^/]+)?$/.test(new URL(response.url()).pathname)
    );
    const importResponse = page.waitForResponse((response) =>
      response.request().method() === "POST" && new URL(response.url()).pathname.endsWith("/imports")
    );
    await page.locator("#saveAccount").click();
    const [written, imported] = await Promise.all([accountWriteResponse, importResponse]);
    const account = await written.json();
    record("账号档案写入成功", [200, 201].includes(written.status()) && /^dya_/.test(account.id), account.id);
    record("创作者中心数据导入成功", imported.status() === 201, String(imported.status()));
    await page.locator("#accountDialog").waitFor({ state: "hidden" });
    await page.locator(".account-metrics").waitFor();
    record("显示账号自身指标", await page.locator(".account-metric").count() === 4);
    record("生成改进重点", await page.locator(".account-recommendations li").count() >= 1);
    record("证据边界不使用行业承诺", (await page.locator(".account-evidence-boundary").textContent()).includes("账号自身"));

    await page.getByRole("button", { name: "和 Agent 讨论运营" }).click();
    await page.locator("#project024AgentPanel").waitFor({ state: "visible" });
    record("Agent 小窗直接显示当前策略", (await page.locator(".agent-panel__draft").inputValue()).includes("每周测试一个开头变量"));
    record("Agent 小窗显示调用成本边界", (await page.locator(".agent-panel__notice").textContent()).includes("API 用量"));
    await page.locator(".agent-panel__input").fill("把策略改成只测试首帧和第一句话");
    const patchResponse = page.waitForResponse((response) =>
      response.request().method() === "PATCH" && new URL(response.url()).pathname === `/api/douyin/accounts/${account.id}`
    );
    await page.getByRole("button", { name: "发送并执行" }).click();
    record("Agent 指令只发送一次", agentRequests === 1, String(agentRequests));
    record("Agent 判断后自动写回账号档案", (await patchResponse).status() === 200);
    record("Agent 面板明确显示判断和执行结果", (await page.locator(".agent-panel__status").textContent()).includes("已判断并写回") && (await page.locator("[data-agent-execution]").last().textContent()).includes("已保存"));
    const saved = await page.request.get(`${baseUrl}/api/douyin/accounts/${account.id}`);
    record("写回策略可重新读取", (await saved.json()).strategy_notes.includes("只测试首帧"));

    const secondResponse = await page.request.post(`${baseUrl}/api/douyin/accounts`, {
      data: { display_name: "项目024竞态账号", douyin_id: `project024_race_${runSuffix}` }
    });
    const secondAccount = await secondResponse.json();
    await page.reload({ waitUntil: "networkidle" });
    await page.locator("body[data-douyin-ready='true']").waitFor();
    await page.route("**/api/douyin/accounts/*/analysis", async (route) => {
      const accountId = decodeURIComponent(new URL(route.request().url()).pathname.split("/").at(-2));
      if (accountId === account.id) await new Promise((resolve) => setTimeout(resolve, 250));
      const response = await route.fetch();
      await route.fulfill({ response });
    });
    await page.locator("#accountSelect").selectOption(account.id);
    await page.waitForTimeout(20);
    await page.locator("#accountSelect").selectOption(secondAccount.id);
    await page.locator("#accountOverview").getByText("项目024竞态账号", { exact: true }).waitFor();
    await page.waitForTimeout(300);
    const raceState = await page.evaluate(() => ({
      selectedId: document.querySelector("#accountSelect")?.value || "",
      overview: document.querySelector("#accountOverview")?.textContent || "",
      bridgeAccountId: window.project024AgentBridge?.getContext("strategy")?.context?.account?.id || ""
    }));
    record(
      "快速切换账号不会被旧诊断响应覆盖",
      raceState.selectedId === secondAccount.id && raceState.bridgeAccountId === secondAccount.id && raceState.overview.includes("项目024竞态账号"),
      JSON.stringify(raceState)
    );

    const desktopLayout = await layout(page);
    record("桌面页面无横向溢出", desktopLayout.rootWidth <= desktopLayout.viewport + 1 && desktopLayout.bodyWidth <= desktopLayout.viewport + 1, JSON.stringify(desktopLayout));
    await capture(page, "account-agent-desktop");
    await context.close();

    const mobileContext = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const mobile = await mobileContext.newPage();
    wireDiagnostics(mobile);
    await mobile.goto(`${baseUrl}/static/douyin.html`, { waitUntil: "networkidle" });
    await mobile.locator("body[data-douyin-ready='true']").waitFor();
    await mobile.locator("#openAccountDialog").click();
    const dialogBox = await mobile.locator("#accountDialog").boundingBox();
    record("390px 账号连接弹窗位于视口宽度内", dialogBox && dialogBox.x >= 0 && dialogBox.x + dialogBox.width <= 390, JSON.stringify(dialogBox));
    await capture(mobile, "account-connection-mobile-390");
    await mobile.locator("#closeAccountDialog").click();
    await mobile.locator(".agent-launcher").click();
    const panelBox = await mobile.locator("#project024AgentPanel").boundingBox();
    record("390px Agent 小窗完整位于视口", panelBox && panelBox.x >= 0 && panelBox.y >= 0 && panelBox.x + panelBox.width <= 390 && panelBox.y + panelBox.height <= 844, JSON.stringify(panelBox));
    const mobileLayout = await layout(mobile);
    record("390px 页面无横向溢出", mobileLayout.rootWidth <= mobileLayout.viewport + 1 && mobileLayout.bodyWidth <= mobileLayout.viewport + 1, JSON.stringify(mobileLayout));
    await capture(mobile, "account-agent-mobile-390");
    await mobileContext.close();
  } finally {
    await browser.close();
  }

  record("控制台无错误", diagnostics.consoleErrors.length === 0, diagnostics.consoleErrors.join(" | "));
  record("页面无异常", diagnostics.pageErrors.length === 0, diagnostics.pageErrors.join(" | "));
  record("请求无失败", diagnostics.requestFailures.length === 0, JSON.stringify(diagnostics.requestFailures));
  record("响应无 4xx/5xx", diagnostics.badResponses.length === 0, JSON.stringify(diagnostics.badResponses));
  const result = { baseUrl, passed: checks.filter((item) => item.ok).length, failed: checks.filter((item) => !item.ok).length, checks, diagnostics };
  if (outputDir) fs.writeFileSync(path.join(outputDir, "browser-account-agent-results.json"), JSON.stringify(result, null, 2), "utf8");
  process.stdout.write(`${JSON.stringify(result)}\n`);
  process.exitCode = result.failed ? 1 : 0;
})().catch((error) => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exitCode = 1;
});
