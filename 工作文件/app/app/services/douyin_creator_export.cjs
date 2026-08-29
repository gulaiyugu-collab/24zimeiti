"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { chromium, firefox } = require("playwright-core");

const CREATOR_URL = "https://creator.douyin.com/creator-micro/data/stats/overview";
const DEFAULT_TIMEOUT_MS = 180000;

function arg(name, fallback = "") {
  const index = process.argv.indexOf(name);
  return index >= 0 ? String(process.argv[index + 1] || fallback) : fallback;
}

function envPath(name, fallback) {
  return process.env[name] || fallback;
}

function existingPath(candidates) {
  return candidates.find((candidate) => fs.existsSync(candidate)) || "";
}

function chromiumProfile(root) {
  if (!root || !fs.existsSync(root)) return "Default";
  try {
    const state = JSON.parse(fs.readFileSync(path.join(root, "Local State"), "utf8"));
    return state?.profile?.last_used || "Default";
  } catch {
    return "Default";
  }
}

function firefoxProfile(root) {
  if (!root || !fs.existsSync(root)) return "";
  const profiles = fs.readdirSync(root, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && /default/i.test(entry.name))
    .map((entry) => path.join(root, entry.name));
  return profiles[0] || "";
}

function browserCandidates() {
  const local = envPath("LOCALAPPDATA", "");
  const appData = envPath("APPDATA", "");
  const programFiles = envPath("PROGRAMFILES", "C:\\Program Files");
  const programFilesX86 = envPath("PROGRAMFILES(X86)", "C:\\Program Files (x86)");
  const list = [
    {
      id: "chrome",
      label: "Google Chrome",
      family: "chromium",
      executable: existingPath([
        path.join(programFiles, "Google/Chrome/Application/chrome.exe"),
        path.join(programFilesX86, "Google/Chrome/Application/chrome.exe"),
      ]),
      profileRoot: path.join(local, "Google/Chrome/User Data"),
    },
    {
      id: "edge",
      label: "Microsoft Edge",
      family: "chromium",
      executable: existingPath([
        path.join(programFiles, "Microsoft/Edge/Application/msedge.exe"),
        path.join(programFilesX86, "Microsoft/Edge/Application/msedge.exe"),
      ]),
      profileRoot: path.join(local, "Microsoft/Edge/User Data"),
    },
    {
      id: "brave",
      label: "Brave",
      family: "chromium",
      executable: existingPath([
        path.join(programFiles, "BraveSoftware/Brave-Browser/Application/brave.exe"),
        path.join(programFilesX86, "BraveSoftware/Brave-Browser/Application/brave.exe"),
      ]),
      profileRoot: path.join(local, "BraveSoftware/Brave-Browser/User Data"),
    },
    {
      id: "chromium",
      label: "Chromium",
      family: "chromium",
      executable: existingPath([
        path.join(programFiles, "Chromium/Application/chrome.exe"),
        path.join(local, "Chromium/Application/chrome.exe"),
      ]),
      profileRoot: path.join(local, "Chromium/User Data"),
    },
    {
      id: "firefox",
      label: "Mozilla Firefox",
      family: "firefox",
      executable: existingPath([
        path.join(programFiles, "Mozilla Firefox/firefox.exe"),
        path.join(programFilesX86, "Mozilla Firefox/firefox.exe"),
      ]),
      profileRoot: path.join(appData, "Mozilla/Firefox/Profiles"),
    },
  ];
  return list.filter((item) => item.executable).map((item) => ({
    ...item,
    profile: item.family === "firefox"
      ? firefoxProfile(item.profileRoot)
      : chromiumProfile(item.profileRoot),
    profile_available: item.family === "firefox"
      ? Boolean(firefoxProfile(item.profileRoot))
      : fs.existsSync(item.profileRoot),
  }));
}

function emit(payload, exitCode = 0) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
  process.exitCode = exitCode;
}

async function launchBrowser(browser, mode) {
  const temporaryRoot = mode === "temporary"
    ? fs.mkdtempSync(path.join(os.tmpdir(), "project024-douyin-"))
    : "";
  const userDataDir = mode === "temporary"
    ? temporaryRoot
    : browser.family === "firefox" ? browser.profile : browser.profileRoot;
  if (!userDataDir) throw new Error("没有找到可用的浏览器用户配置目录。");
  const options = {
    executablePath: browser.executable,
    headless: false,
    locale: "zh-CN",
    acceptDownloads: true,
  };
  if (browser.family === "chromium" && mode !== "temporary") {
    options.args = [`--profile-directory=${browser.profile || "Default"}`, "--no-first-run", "--no-default-browser-check"];
  }
  const context = browser.family === "firefox"
    ? await firefox.launchPersistentContext(userDataDir, options)
    : await chromium.launchPersistentContext(userDataDir, options);
  return { context, temporaryRoot };
}

async function clickExportIfVisible(page) {
  const candidates = [
    page.getByRole("button", { name: /导出|下载数据|导出数据/ }).first(),
    page.getByText(/导出数据|下载数据/).first(),
  ];
  for (const locator of candidates) {
    try {
      if (await locator.isVisible({ timeout: 1000 })) {
        await locator.click({ timeout: 3000 });
        return true;
      }
    } catch {
      // The creator center may render a different control; wait for a user click.
    }
  }
  return false;
}

async function waitForCreatorExport(page, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let attempted = false;
  while (Date.now() < deadline) {
    // QR login, SMS verification, and creator-center hydration can all finish
    // after the initial page load. Re-scan the live DOM instead of timing out
    // after one early button lookup.
    // Consume the timeout rejection while the QR login page is still visible.
    // An unhandled waitForEvent rejection would terminate Node after 5 seconds.
    const downloadPromise = page
      .waitForEvent("download", { timeout: 5000 })
      .catch(() => null);
    const clicked = await clickExportIfVisible(page);
    attempted = attempted || clicked;
    if (clicked) {
      const download = await downloadPromise;
      if (download) {
        return download;
      }
      // Some versions open an export menu first; keep polling for the final
      // download action or let the user click it in the visible window.
    }
    await page.waitForTimeout(1000);
  }
  if (!attempted) {
    throw new Error("等待二维码登录或导出按钮超时。");
  }
  throw new Error("已打开创作者中心，但未等到导出文件下载。");
}

(async () => {
  const available = browserCandidates();
  if (process.argv.includes("--list")) {
    emit({ status: "ok", browsers: available.map(({ id, label, family, profile, profile_available }) => ({
      id, label, family, profile, profile_available,
    })) });
    return;
  }
  const browserId = arg("--browser");
  const browser = available.find((item) => item.id === browserId) || available[0];
  if (!browser) {
    emit({ status: "error", code: "browser_not_found", message: "未找到 Chrome、Edge、Brave、Chromium 或 Firefox。" }, 1);
    return;
  }
  const output = path.resolve(arg("--output", path.join(os.tmpdir(), "project024-creator-export.csv")));
  const timeoutMs = Math.max(30000, Number(arg("--timeout-ms", String(DEFAULT_TIMEOUT_MS))) || DEFAULT_TIMEOUT_MS);
  const requestedMode = arg("--profile-mode", "existing");
  let activeMode = requestedMode === "temporary" ? "temporary" : "existing";
  let launched;
  try {
    try {
      launched = await launchBrowser(browser, activeMode);
    } catch (error) {
      if (activeMode !== "existing") throw error;
      activeMode = "temporary";
      launched = await launchBrowser(browser, activeMode);
    }
    const { context, temporaryRoot } = launched;
    const page = context.pages()[0] || await context.newPage();
    await page.goto(arg("--url", CREATOR_URL), { waitUntil: "domcontentloaded", timeout: timeoutMs });
    await page.bringToFront();
    const download = await waitForCreatorExport(page, timeoutMs);
    const suggested = download.suggestedFilename() || "creator-export.csv";
    const extension = /\.xlsx$/i.test(suggested) ? ".xlsx" : ".csv";
    const target = /\.(?:csv|xlsx)$/i.test(output) ? output : `${output}${extension}`;
    await fs.promises.mkdir(path.dirname(target), { recursive: true });
    await download.saveAs(target);
    const stat = await fs.promises.stat(target);
    if (!stat.size) throw new Error("创作者中心导出文件为空。");
    await context.close();
    if (temporaryRoot) fs.rmSync(temporaryRoot, { recursive: true, force: true });
    emit({
      status: "ok",
      browser: browser.id,
      browser_label: browser.label,
      profile_mode: activeMode,
      output: target,
      bytes: stat.size,
      temporary_profile_deleted: Boolean(temporaryRoot),
      message: "已取得创作者中心导出文件；未导出或保存 Cookie。",
    });
  } catch (error) {
    if (launched?.context) await launched.context.close().catch(() => {});
    if (launched?.temporaryRoot) fs.rmSync(launched.temporaryRoot, { recursive: true, force: true });
    emit({
      status: "error",
      code: "creator_export_failed",
      browser: browser.id,
      profile_mode: activeMode,
      message: "浏览器会话未完成创作者中心导出；请确认已登录、允许下载，并在需要时手动点击“导出数据”。",
      detail: error?.message || "unknown",
    }, 1);
  }
})().catch((error) => emit({ status: "error", code: "creator_export_failed", message: error?.message || "unknown" }, 1));
