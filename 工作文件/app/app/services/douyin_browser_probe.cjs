"use strict";

const { chromium } = require("playwright-core");

function argument(name, fallback = "") {
  const index = process.argv.indexOf(name);
  return index >= 0 ? String(process.argv[index + 1] || fallback) : fallback;
}

function cleanPageUrl(value) {
  try {
    const url = new URL(value);
    const match = url.pathname.match(/\/video\/(\d{10,})/i);
    return match
      ? `https://www.douyin.com/video/${match[1]}`
      : `https://${url.host}${url.pathname}`;
  } catch {
    return "";
  }
}

function emitError(code, message) {
  process.stdout.write(
    `${JSON.stringify({ status: "error", code, message })}\n`,
  );
  process.exitCode = 1;
}

function responseTotalLength(headers) {
  const contentRange = String(headers["content-range"] || "");
  const match = contentRange.match(/\/(\d+)$/);
  return match
    ? Number(match[1])
    : Number(headers["content-length"] || 0);
}

function isSeparateAudioResponse(contentType, mediaUrl) {
  if (contentType.startsWith("audio/")) return true;
  return /(?:^|[\/_-])media[-_]?audio(?:[\/_\-.]|$)/i.test(
    mediaUrl.pathname,
  );
}

function recordCandidate(candidates, response, headers) {
  const current = candidates.get(response.url());
  const length = responseTotalLength(headers);
  if (!current || length > current.length) {
    candidates.set(response.url(), { url: response.url(), length });
  }
}

(async () => {
  const submittedUrl = argument("--url");
  const executablePath = argument("--browser");
  const timeoutMs = Math.max(10_000, Number(argument("--timeout-ms", "45000")) || 45_000);
  if (!submittedUrl || !executablePath) {
    emitError("invalid_arguments", "缺少公开链接或浏览器路径。");
    return;
  }

  let browser;
  try {
    browser = await chromium.launch({
      headless: true,
      executablePath,
      args: ["--disable-extensions", "--no-first-run", "--no-default-browser-check"],
    });
    // A non-persistent context deliberately starts without the user's profile,
    // cookies, local storage, extensions, or logged-in state.
    const context = await browser.newContext({ locale: "zh-CN" });
    const page = await context.newPage();
    const videoResponses = new Map();
    const audioResponses = new Map();
    page.on("response", (response) => {
      try {
        const headers = response.headers();
        const contentType = String(headers["content-type"] || "").toLowerCase();
        const mediaUrl = new URL(response.url());
        if (
          (contentType.startsWith("video/") || contentType.startsWith("audio/")) &&
          !mediaUrl.hostname.endsWith("douyinstatic.com")
        ) {
          const candidates = isSeparateAudioResponse(contentType, mediaUrl)
            ? audioResponses
            : videoResponses;
          recordCandidate(candidates, response, headers);
        }
      } catch {
        // Ignore unrelated or malformed browser responses.
      }
    });
    await page.goto(submittedUrl, {
      waitUntil: "domcontentloaded",
      timeout: timeoutMs,
    });
    await page.waitForFunction(
      () => {
        return Array.from(document.querySelectorAll("video")).some((video) => {
          if (!video.currentSrc || video.readyState < 2) return false;
          try {
            if (video.currentSrc.startsWith("blob:")) return true;
            const source = new URL(video.currentSrc);
            return !source.hostname.endsWith("douyinstatic.com");
          } catch {
            return false;
          }
        });
      },
      null,
      { timeout: timeoutMs },
    );
    const mediaDeadline = Date.now() + timeoutMs;
    while (videoResponses.size === 0 && Date.now() < mediaDeadline) {
      await page.waitForTimeout(250);
    }
    if (videoResponses.size === 0) {
      emitError("media_not_found", "公开作品页已打开，但没有发现可下载媒体。");
      return;
    }
    const audioDeadline = Date.now() + 1500;
    while (audioResponses.size === 0 && Date.now() < audioDeadline) {
      await page.waitForTimeout(100);
    }
    const videoCandidates = [...videoResponses.values()].sort(
      (left, right) => right.length - left.length,
    );
    const audioCandidates = [...audioResponses.values()].sort(
      (left, right) => right.length - left.length,
    );
    const mediaUrl = videoCandidates[0].url;
    const audioUrl = audioCandidates[0]?.url || null;
    const result = await page.evaluate(() => {
      const videos = Array.from(document.querySelectorAll("video"))
        .filter((video) => {
          if (!video.currentSrc || video.readyState < 2) return false;
          try {
            if (video.currentSrc.startsWith("blob:")) return true;
            const source = new URL(video.currentSrc);
            return !source.hostname.endsWith("douyinstatic.com");
          } catch {
            return false;
          }
        })
        .sort((left, right) => {
          const leftScore =
            (Number(left.duration) || 0) *
            Math.max(1, left.videoWidth * left.videoHeight);
          const rightScore =
            (Number(right.duration) || 0) *
            Math.max(1, right.videoWidth * right.videoHeight);
          return rightScore - leftScore;
        });
      const video = videos[0] || null;
      const pageUrl = new URL(window.location.href);
      const idMatch = pageUrl.pathname.match(/\/video\/(\d{10,})/i);
      const title = (document.title || "")
        .replace(/\s*-\s*抖音(?:\s*精选)?\s*$/u, "")
        .trim();
      const description =
        document.querySelector('meta[name="description"]')?.content?.trim() ||
        title ||
        null;
      return {
        aweme_id: idMatch ? idMatch[1] : null,
        webpage_url: idMatch
          ? `https://www.douyin.com/video/${idMatch[1]}`
          : `https://${pageUrl.host}${pageUrl.pathname}`,
        title: title || null,
        description,
        duration: Number.isFinite(video?.duration) ? video.duration : 0,
        language: "zh",
        uploader: document.querySelector('meta[name="author"]')?.content || null,
      };
    });
    result.webpage_url = cleanPageUrl(result.webpage_url);
    process.stdout.write(
      `${JSON.stringify({
        status: "ok",
        ...result,
        media_url: mediaUrl,
        audio_url: audioUrl,
      })}\n`,
    );
  } catch (error) {
    emitError("browser_probe_failed", "隔离浏览器未能取得公开媒体。");
  } finally {
    if (browser) {
      await browser.close().catch(() => {});
    }
  }
})().catch(() => {
  emitError("browser_probe_failed", "隔离浏览器未能取得公开媒体。");
});
