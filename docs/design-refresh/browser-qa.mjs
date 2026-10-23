/** VIS-01 read-only Chromium QA. Never sends a mutating HTTP request. */
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

const require = createRequire(import.meta.url);
const playwrightModule =
  process.env.EDIVO_PLAYWRIGHT_MODULE ||
  "/home/davi/.cache/ms-playwright-go/1.50.1/package";
const { chromium } = require(playwrightModule);
let driverVersion = null;
try {
  driverVersion = require(
    path.join(path.dirname(require.resolve(playwrightModule)), "package.json"),
  ).version;
} catch {
  /* Driver version is diagnostic only. */
}
const argv = process.argv.slice(2);
const arg = (name, fallback) => {
  const index = argv.indexOf(name);
  return index < 0 ? fallback : argv[index + 1];
};
const stage = arg("--stage", "before");
if (!["before", "after"].includes(stage))
  throw new Error("Use --stage before|after");
const output = path.resolve(
  arg("--output", `docs/design-refresh/evidence/browser-${stage}`),
);
if (fs.existsSync(output))
  throw new Error("Evidence output already exists; use a new directory.");
fs.mkdirSync(output, { recursive: true });
const base = process.env.EDIVO_BROWSER_BASE_URL || "http://127.0.0.1:5174";
const origin = new URL(base).origin;
if (!["127.0.0.1", "localhost"].includes(new URL(base).hostname))
  throw new Error("Local QA only.");
const report = {
  stage,
  status: "failed",
  started_at: new Date().toISOString(),
  checks: [],
  screenshots: [],
  page_errors: [],
  blocked_mutations: [],
  external_requests: [],
  range_responses: [],
  browser: null,
  playwright_driver_version: driverVersion,
  project_id: null,
  real_api: true,
  database_storage_mutations_performed: false,
  simulated_states_separate: [],
  viewports: [],
};
let browser;
const check = (name, condition, detail) => {
  report.checks.push({
    check: name,
    passed: !!condition,
    ...(detail === undefined ? {} : { detail }),
  });
  if (!condition) throw new Error(name);
};
const shot = async (page, name, locator) => {
  // Ordinary screenshots are neutral. Dedicated modal/focus checks retain focus.
  if (!name.includes("modal"))
    await page.evaluate(() => {
      if (document.activeElement instanceof HTMLElement)
        document.activeElement.blur();
    });
  const file = `${name}.png`;
  await (locator || page).screenshot({
    path: path.join(output, file),
    ...(locator ? {} : { fullPage: true }),
    animations: "disabled",
  });
  report.screenshots.push(file);
};
async function setupContext(viewport, theme, extra = {}) {
  const context = await browser.newContext({
    viewport,
    colorScheme: theme ?? "light",
    reducedMotion: "reduce",
    ...extra,
  });
  await context.addInitScript(
    ({ theme, projectId, previewId, renderId }) => {
      if (theme) localStorage.setItem("edivo-theme", theme);
      if (previewId)
        localStorage.setItem(`edivo:preview:${projectId}`, previewId);
      if (renderId) {
        localStorage.setItem(`edivo:render:${projectId}`, renderId);
        localStorage.setItem(`edivo:render-result:${projectId}`, renderId);
      }
    },
    {
      theme,
      projectId: report.project_id,
      previewId: report.preview_id,
      renderId: report.render_id,
    },
  );
  await context.route("**/*", async (route) => {
    const request = route.request();
    if (!["GET", "HEAD"].includes(request.method())) {
      report.blocked_mutations.push({
        method: request.method(),
        path: new URL(request.url()).pathname,
      });
      return route.abort("blockedbyclient");
    }
    if (
      /^https?:/.test(request.url()) &&
      new URL(request.url()).origin !== origin
    ) {
      report.external_requests.push(request.url());
      return route.abort("blockedbyclient");
    }
    return route.continue();
  });
  context.on("page", (page) => {
    page.on("pageerror", (error) => report.page_errors.push(error.message));
    page.on("response", async (response) => {
      if (
        /\/(stream|file)$/.test(new URL(response.url()).pathname) &&
        response.status() === 206
      ) {
        report.range_responses.push({
          path: new URL(response.url()).pathname,
          status: 206,
          range: response.request().headers().range,
          content_range: response.headers()["content-range"],
        });
      }
    });
  });
  return context;
}
async function get(request, suffix) {
  const response = await request.get(`${base}/api/v1${suffix}`);
  return {
    status: response.status(),
    body: response.ok() ? await response.json() : null,
  };
}
async function overflow(page, label, strict = true) {
  const detail = await page.evaluate(() => ({
    viewport: innerWidth,
    width: document.documentElement.scrollWidth,
    height: document.documentElement.scrollHeight,
  }));
  if (strict || stage === "after")
    check(
      `${label}_no_horizontal_overflow`,
      detail.width <= detail.viewport + 1,
      detail,
    );
  else
    report.checks.push({
      check: `${label}_baseline_overflow_observation`,
      passed: detail.width <= detail.viewport + 1,
      detail,
    });
  return detail;
}
async function nativePlayback(page, label) {
  const video = page.getByLabel(label, { exact: true });
  await video.scrollIntoViewIfNeeded();
  await page.waitForFunction(
    (label) => {
      const v = document.querySelector(`video[aria-label="${label}"]`);
      return !!v && v.readyState >= 1 && v.videoWidth > 0;
    },
    label,
    { timeout: 30000 },
  );
  const started = await video.evaluate(async (v) => {
    v.muted = true;
    v.currentTime = Math.min(
      Math.max(0.5, v.currentTime + 0.5),
      v.duration - 0.25,
    );
    await v.play();
    return v.currentTime;
  });
  await page.waitForFunction(
    ({ label, started }) =>
      document.querySelector(`video[aria-label="${label}"]`).currentTime >
      started + 0.15,
    { label, started },
    { timeout: 15000 },
  );
  const metrics = await video.evaluate((v) => {
    v.pause();
    return {
      duration: v.duration,
      current_time: v.currentTime,
      video_width: v.videoWidth,
      video_height: v.videoHeight,
      decoded_frames: v.getVideoPlaybackQuality?.().totalVideoFrames,
      error: v.error?.code ?? null,
    };
  });
  check(
    `native_${label}_play_seek_pause`,
    metrics.current_time > started && metrics.error === null,
    metrics,
  );
}
async function contrastSamples(page, label) {
  const values = await page.evaluate(() => {
    const selectors = [
      "body",
      ".panel-description",
      ".field-help",
      ".project-meta",
      ".editor-save-state",
      '[data-slot="button"].bg-primary:not(:disabled)',
      ".section-kicker",
      ".timeline-ruler",
      ".export-history-main > strong",
    ];
    const rgb = (text) => {
      const values = text.match(/[\d.]+/g)?.map(Number);
      return values?.length >= 3 ? values : [0, 0, 0, 0];
    };
    const blend = (front, back) => {
      const a = front[3] ?? 1;
      return [0, 1, 2].map((i) => front[i] * a + back[i] * (1 - a));
    };
    const lum = (values) =>
      values
        .map((v) => v / 255)
        .map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
        .reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0);
    return selectors
      .map((selector) => {
        const node = document.querySelector(selector);
        if (!node) return null;
        const style = getComputedStyle(node);
        const stack = [];
        for (let n = node; n; n = n.parentElement)
          stack.push(rgb(getComputedStyle(n).backgroundColor));
        let bg = [255, 255, 255];
        for (const color of stack.reverse()) bg = blend(color, bg);
        const fg = blend(rgb(style.color), bg),
          a = lum(fg),
          b = lum(bg);
        return {
          selector,
          foreground: style.color,
          background_rgb: bg.map(Math.round),
          ratio: +((Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)).toFixed(
            2,
          ),
          font_size_px: parseFloat(style.fontSize),
          disabled: !!node.disabled,
        };
      })
      .filter(Boolean);
  });
  report.contrast ??= {};
  report.contrast[label] = values;
  if (stage === "after")
    for (const value of values.filter((v) => !v.disabled))
      check(
        `${label}_${value.selector}_contrast_AA`,
        value.ratio >= 4.5,
        value,
      );
}
try {
  browser = await chromium.launch({
    headless: true,
    executablePath:
      process.env.EDIVO_CHROMIUM_EXECUTABLE ||
      "/home/davi/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome",
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  report.browser = browser.version();
  const probe = await browser.newContext();
  let projectId = arg("--project", "3f4bc7e0-3b4e-4db6-a812-18b581a398e3");
  let selected = await get(probe.request, `/projects/${projectId}`);
  if (selected.status !== 200) {
    const projects = await get(probe.request, "/projects?limit=100&offset=0");
    for (const candidate of projects.body?.items ?? []) {
      const media = await get(
        probe.request,
        `/projects/${candidate.id}/media?limit=1&offset=0`,
      );
      const spec = await get(
        probe.request,
        `/projects/${candidate.id}/edit-spec`,
      );
      if (media.status === 200 && media.body.total && spec.status === 200) {
        projectId = candidate.id;
        selected = { status: 200, body: candidate };
        break;
      }
    }
  }
  check("existing_project_accessible_read_only", selected.status === 200);
  report.project_id = projectId;
  report.project_name = selected.body.name;
  const revision = await get(probe.request, `/projects/${projectId}/edit-spec`);
  const media = await get(
    probe.request,
    `/projects/${projectId}/media?limit=100&offset=0`,
  );
  const audio = await get(
    probe.request,
    `/projects/${projectId}/audio?limit=100&offset=0`,
  );
  const renders = await get(
    probe.request,
    `/projects/${projectId}/renders?limit=100&offset=0`,
  );
  check(
    "real_saved_revision_and_media_library_available",
    revision.status === 200 &&
      media.status === 200 &&
      media.body.items.length > 0,
  );
  report.initial_public_snapshot = {
    project: selected.body,
    revision: revision.body,
    media: media.body,
    audio: audio.body,
    renders: renders.body,
  };
  report.render_id = renders.body?.items.find(
    (job) => job.status === "completed" && job.artifact_available,
  )?.id;
  const previewId = arg(
    "--preview",
    projectId === "3f4bc7e0-3b4e-4db6-a812-18b581a398e3"
      ? "2bd24155-09d5-45c7-aa99-1d9952142be5"
      : null,
  );
  if (previewId) {
    const preview = await get(
      probe.request,
      `/projects/${projectId}/previews/${previewId}`,
    );
    if (
      preview.status === 200 &&
      preview.body.status === "completed" &&
      preview.body.artifact_available
    )
      report.preview_id = previewId;
  }
  await probe.close();
  const sizes = [
    { name: "desktop", width: 1366, height: 900 },
    { name: "tablet", width: 768, height: 1024 },
    { name: "mobile-375", width: 375, height: 812 },
    { name: "mobile-390", width: 390, height: 844 },
  ];
  for (const viewport of sizes)
    for (const theme of ["dark", "light"]) {
      const key = `${viewport.name}-${theme}`;
      const context = await setupContext(
        { width: viewport.width, height: viewport.height },
        theme,
      );
      const page = await context.newPage();
      await page.goto(`${base}/#/projects`, { waitUntil: "networkidle" });
      await page.locator(".project-list, .empty-state").waitFor();
      await page.evaluate(() => scrollTo(0, 0));
      await shot(page, `${key}-projects`);
      await overflow(page, `${key}_projects`, false);
      check(
        `${key}_saved_theme_respected`,
        (await page.locator("html").getAttribute("data-theme")) === theme,
      );
      if (stage === "after") {
        const logo = page.locator(".brand img");
        check(
          `${key}_official_logo_loaded`,
          await logo.evaluate((img) => img.complete && img.naturalWidth > 0),
        );
      }
      if (viewport.name === "desktop") {
        await page
          .getByRole("button", { name: "Novo projeto", exact: true })
          .click();
        await page.getByRole("dialog").waitFor();
        await shot(page, `${key}-modal`);
        await page.keyboard.press("Tab");
        check(
          `${key}_modal_keyboard_focus_inside`,
          await page
            .getByRole("dialog")
            .evaluate((dialog) => dialog.contains(document.activeElement)),
        );
        await page.keyboard.press("Escape");
        await page.getByRole("dialog").waitFor({ state: "hidden" });
      }
      await page.goto(`${base}/#/projects/${projectId}`, {
        waitUntil: "networkidle",
      });
      await page.locator(".media-card").first().waitFor();
      await page.evaluate(() => scrollTo(0, 0));
      await shot(page, `${key}-library`);
      await overflow(page, `${key}_library`, false);
      await page.goto(`${base}/#/projects/${projectId}/editor`, {
        waitUntil: "networkidle",
      });
      await page.locator(".editor-timeline").waitFor();
      await page.locator(".export-history").waitFor();
      // Hash navigation resolves before React's dependent GET effects. Wait for
      // the real resources and media metadata so before/after frames are stable.
      if (report.render_id)
        await page.locator(".export-history-item").first().waitFor();
      const mediaLabels = [
        "Vídeo do projeto",
        ...(report.render_id ? ["Vídeo exportado"] : []),
        ...(report.preview_id ? ["Prévia gerada"] : []),
      ];
      for (const label of mediaLabels)
        await page.waitForFunction(
          (label) => {
            const v = document.querySelector(`video[aria-label="${label}"]`);
            return !!v && v.readyState >= 2 && !v.seeking && v.videoWidth > 0;
          },
          label,
          { timeout: 45000 },
        );
      await page
        .locator(".player-overlay[role='status']")
        .waitFor({ state: "hidden" });
      if (report.render_id)
        await page
          .locator(".export-video > [role='status']")
          .waitFor({ state: "hidden" });
      if (report.preview_id)
        await page
          .locator(".preview-result-player > [role='status']")
          .waitFor({ state: "hidden" });
      await page
        .getByText("Edição salva · Revisão", { exact: false })
        .first()
        .waitFor();
      await page.evaluate(() => scrollTo(0, 0));
      await shot(page, `${key}-editor`);
      const layout = await overflow(page, `${key}_editor`, false);
      report.viewports.push({ name: viewport.name, theme, ...layout });
      if (viewport.name === "desktop" || viewport.name === "mobile-390") {
        for (const [name, selector] of [
          ["timeline", ".editor-timeline"],
          ["text-audio", ".editor-composition-panels"],
          ["previews", ".preview-panel"],
          ["exports", ".export-panel"],
        ]) {
          const section = page.locator(selector).first();
          if (await section.count())
            await shot(page, `${key}-${name}`, section);
        }
      }
      if (viewport.name === "desktop") {
        await contrastSamples(page, key);
        const range = page.getByRole("slider", { name: "Marcador de entrada" });
        await range.focus();
        check(
          `${key}_timeline_keyboard_focus`,
          await range.evaluate((node) => document.activeElement === node),
        );
        if (theme === "dark") {
          await nativePlayback(page, "Vídeo do projeto");
          if (report.render_id) await nativePlayback(page, "Vídeo exportado");
          if (report.preview_id) await nativePlayback(page, "Prévia gerada");
        }
        check(
          `${key}_playhead_does_not_dirty_saved_revision`,
          await page
            .getByText("Edição salva · Revisão", { exact: false })
            .first()
            .isVisible(),
        );
      }
      await context.close();
    }
  if (stage === "after") {
    const context = await setupContext({ width: 1366, height: 900 }, null);
    const page = await context.newPage();
    await page.goto(base, { waitUntil: "networkidle" });
    check(
      "first_visit_dark_first_even_with_light_os",
      (await page.locator("html").getAttribute("data-theme")) === "dark",
    );
    await page
      .getByRole("button", { name: "Ativar tema claro", exact: true })
      .click();
    check(
      "explicit_light_preference_saved",
      (await page.evaluate(() => localStorage.getItem("edivo-theme"))) ===
        "light",
    );
    await page.reload({ waitUntil: "networkidle" });
    check(
      "explicit_light_preference_survives_reload",
      (await page.locator("html").getAttribute("data-theme")) === "light",
    );
    check(
      "reduced_motion_preference_active",
      await page.evaluate(
        () => matchMedia("(prefers-reduced-motion: reduce)").matches,
      ),
    );
    const transition = await page
      .locator("button")
      .first()
      .evaluate((node) => getComputedStyle(node).transitionDuration);
    report.reduced_motion = {
      preference: "reduce",
      button_transition_duration: transition,
    };
    check(
      "decorative_transitions_reduced",
      transition
        .split(",")
        .every(
          (value) =>
            parseFloat(value) === 0 ||
            (value.trim().endsWith("ms")
              ? parseFloat(value) / 1000
              : parseFloat(value)) <= 0.001,
        ),
    );
    await context.close();
  }
  if (stage === "after") {
    for (const theme of ["dark", "light"]) {
      for (const scene of ["loading", "error", "empty"]) {
        const context = await setupContext({ width: 390, height: 844 }, theme);
        let release;
        const gate = new Promise((resolve) => {
          release = resolve;
        });
        await context.route(/\/api\/v1\/projects\?/, async (route) => {
          if (route.request().method() !== "GET")
            throw new Error("simulated_scene_nonread_request");
          if (scene === "loading") {
            await gate;
            return route.abort("aborted");
          }
          return route.fulfill({
            status: scene === "error" ? 503 : 200,
            contentType: "application/json",
            body: JSON.stringify(
              scene === "error"
                ? {
                    code: "database_unavailable",
                    message: "Serviço temporariamente indisponível.",
                    request_id: "vis01-simulated",
                  }
                : { items: [], total: 0, limit: 20, offset: 0 },
            ),
          });
        });
        const page = await context.newPage();
        await page.goto(`${base}/#/projects`, {
          waitUntil: "domcontentloaded",
        });
        if (scene === "loading")
          await page
            .getByText("Carregando projetos…", { exact: true })
            .waitFor();
        if (scene === "error")
          await page.locator('[role="alert"]').first().waitFor();
        if (scene === "empty") await page.locator(".empty-state").waitFor();
        await page.evaluate(() => scrollTo(0, 0));
        await shot(page, `simulated-${theme}-${scene}`);
        await overflow(page, `simulated_${theme}_${scene}`);
        report.simulated_states_separate.push({
          scene,
          theme,
          transport: "mocked_GET_in_separate_context",
          real_dependency_failure: false,
          screenshot: `simulated-${theme}-${scene}.png`,
        });
        release();
        await context.close();
      }
    }
  }
  if (stage === "after") {
    const context = await setupContext({ width: 1366, height: 900 }, "dark");
    const page = await context.newPage();
    await page.goto(base, { waitUntil: "networkidle" });
    const metadata = await page.evaluate(() => ({
      title: document.title,
      icons: [...document.querySelectorAll('link[rel*="icon"]')].map((n) => ({
        rel: n.rel,
        href: n.getAttribute("href"),
        sizes: n.sizes?.value,
      })),
      logo: [...document.querySelectorAll(".brand img")].map((n) => ({
        alt: n.alt,
        width: n.naturalWidth,
        height: n.naturalHeight,
        css_width: n.getBoundingClientRect().width,
        css_height: n.getBoundingClientRect().height,
      })),
    }));
    report.metadata = metadata;
    check(
      "official_browser_title",
      metadata.title === "Edivo — Editor de Vídeos com IA",
    );
    check(
      "favicon_and_apple_links_present",
      metadata.icons.some((n) => n.rel === "icon") &&
        metadata.icons.some((n) => n.rel === "apple-touch-icon"),
    );
    for (const name of [
      "/favicon-16x16.png",
      "/favicon-32x32.png",
      "/favicon-48x48.png",
      "/favicon.ico",
      "/apple-touch-icon.png",
      "/icon-192.png",
      "/icon-512.png",
    ]) {
      const response = await context.request.get(
        `${base}${name}?vis01=${Date.now()}`,
        { headers: { "Cache-Control": "no-cache" } },
      );
      const bytes = await response.body();
      check(
        `asset_${name}_fresh_200`,
        response.status() === 200 && bytes.length > 0,
      );
      const expected = {
        "/favicon-16x16.png": 16,
        "/favicon-32x32.png": 32,
        "/favicon-48x48.png": 48,
        "/apple-touch-icon.png": 180,
        "/icon-192.png": 192,
        "/icon-512.png": 512,
      }[name];
      if (expected)
        check(
          `asset_${name}_exact_dimensions`,
          bytes.subarray(1, 4).toString() === "PNG" &&
            bytes.readUInt32BE(16) === expected &&
            bytes.readUInt32BE(20) === expected,
        );
      if (name === "/favicon.ico")
        check(
          "favicon_ico_contains_three_sizes",
          bytes.readUInt16LE(0) === 0 &&
            bytes.readUInt16LE(2) === 1 &&
            bytes.readUInt16LE(4) >= 3,
        );
      report.assets ??= [];
      report.assets.push({
        name,
        bytes: bytes.length,
        content_type: response.headers()["content-type"],
        sha256: crypto.createHash("sha256").update(bytes).digest("hex"),
      });
    }
    await context.close();
  }
  const verify = await browser.newContext();
  const end = {
    project: (await get(verify.request, `/projects/${projectId}`)).body,
    revision: (await get(verify.request, `/projects/${projectId}/edit-spec`))
      .body,
    media: (
      await get(
        verify.request,
        `/projects/${projectId}/media?limit=100&offset=0`,
      )
    ).body,
    audio: (
      await get(
        verify.request,
        `/projects/${projectId}/audio?limit=100&offset=0`,
      )
    ).body,
    renders: (
      await get(
        verify.request,
        `/projects/${projectId}/renders?limit=100&offset=0`,
      )
    ).body,
  };
  check(
    "read_only_public_snapshot_unchanged",
    JSON.stringify(end) === JSON.stringify(report.initial_public_snapshot),
  );
  await verify.close();
  check(
    "no_mutating_requests_attempted",
    report.blocked_mutations.length === 0,
  );
  check(
    "no_unexpected_external_resources",
    report.external_requests.length === 0,
  );
  check("no_uncaught_page_errors", report.page_errors.length === 0);
  report.status = "passed";
} catch (error) {
  report.failure = { name: error.name, message: error.message };
  console.error(error.stack);
} finally {
  if (browser) await browser.close();
  report.finished_at = new Date().toISOString();
  fs.writeFileSync(
    path.join(output, "report.json"),
    JSON.stringify(report, null, 2) + "\n",
    { flag: "wx" },
  );
  console.log(
    JSON.stringify({
      stage,
      status: report.status,
      checks: report.checks.length,
      screenshots: report.screenshots.length,
      output,
    }),
  );
}
if (report.status !== "passed") process.exitCode = 1;
