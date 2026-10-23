/** Real Chromium R05 UI/worker/download evidence; no mocks or app lifecycle calls. */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";

const { chromium } = await import(
  process.env.EDIVO_PLAYWRIGHT_MODULE ?? "playwright"
);
const base = process.env.EDIVO_BROWSER_BASE_URL;
const runId = process.env.EDIVO_BROWSER_RUN_ID ?? `r05-browser-${Date.now()}`;
const stage = process.env.EDIVO_BROWSER_STAGE ?? "export";
const fixtures = process.env.EDIVO_BROWSER_FIXTURES ?? "/fixtures";
assert.ok(base, "Local frontend URL required");
const checks = [],
  screenshots = [],
  renderStates = [],
  ranges = [],
  saves = [],
  creates = [],
  pageErrors = [];
const seedPath = "/work/seed-r05.json";
const browser = await chromium.launch({ headless: true });
let seed = await readFile(seedPath, "utf8")
  .then(JSON.parse)
  .catch(() => null);
const context = await browser.newContext({
  viewport: { width: 1440, height: 1100 },
  colorScheme: "dark",
  acceptDownloads: true,
  ...(stage === "verify" && seed?.storage_state
    ? { storageState: seed.storage_state }
    : {}),
});
context.setDefaultTimeout(20_000);
const page = await context.newPage();
const text = "Edivo: 'ação' \\ 100%\nVídeo final local";
const saved = () =>
  page.getByRole("button", { name: "Salvar edição", exact: true });
const panel = (target = page) =>
  target.getByRole("region", { name: "Exportar vídeo", exact: true });
const generate = () =>
  panel().getByRole("button", { name: "Gerar vídeo", exact: true });
const profile = () =>
  panel().getByRole("combobox", { name: "Perfil de qualidade", exact: true });
const selectedResult = (target = page) =>
  target.getByRole("region", { name: "Exportação selecionada", exact: true });
const exportedVideo = (target = page) =>
  target.getByLabel("Vídeo exportado", { exact: true });
const editorUrl = () => `${base}/#/projects/${seed.project.id}/editor`;
let failure;

function passed(name, details) {
  const row = { check: name, passed: true, ...(details ? { details } : {}) };
  checks.push(row);
  console.log(JSON.stringify(row));
}
function observe(target) {
  target.on("pageerror", (error) => pageErrors.push(error.message));
  target.on("dialog", (dialog) => dialog.accept());
  target.on("request", (outgoing) => {
    if (outgoing.method() === "PUT" && outgoing.url().endsWith("/edit-spec"))
      saves.push(outgoing.postDataJSON());
    if (outgoing.method() === "POST" && outgoing.url().endsWith("/renders"))
      creates.push(outgoing.postDataJSON());
  });
  target.on("response", async (response) => {
    const path = new URL(response.url()).pathname;
    if (/\/renders\/[^/]+$/.test(path)) {
      const body = await response.json().catch(() => null);
      if (body?.status)
        renderStates.push({
          id: body.id,
          status: body.status,
          stage: body.stage,
          revision_id: body.revision_id,
          progress_percent: body.progress_percent,
          progress_encoded_ms: body.progress_encoded_ms,
          attempts: body.attempts,
        });
    }
    if (/\/(?:stream|renders\/[^/]+\/file)$/.test(path))
      ranges.push({
        path,
        status: response.status(),
        range: response.request().headers().range ?? null,
        content_range: response.headers()["content-range"] ?? null,
      });
  });
}
observe(page);
await context.addInitScript(() => {
  window.edivoR05Events = [];
  for (const name of [
    "loadedmetadata",
    "playing",
    "pause",
    "seeking",
    "seeked",
    "error",
  ])
    document.addEventListener(
      name,
      (event) => {
        if (event.target instanceof HTMLVideoElement)
          window.edivoR05Events.push({
            name,
            time: event.target.currentTime,
            label: event.target.getAttribute("aria-label"),
          });
      },
      true,
    );
});
async function api(path, options = {}) {
  const response = await context.request.fetch(
    `${base}/api/v1${path}`,
    options,
  );
  const body = await response.json().catch(() => null);
  assert.ok(
    response.ok(),
    `Real API ${path} returned HTTP${response.status()}`,
  );
  return body;
}
async function persist() {
  await writeFile(seedPath, JSON.stringify(seed, null, 2), { mode: 0o600 });
}
async function capture(name, target = page) {
  await target.evaluate(() => document.fonts.ready);
  const filename = `${runId}-${name}.png`;
  await target.screenshot({
    path: `/work/artifacts/${filename}`,
    fullPage: true,
    animations: "disabled",
  });
  screenshots.push(filename);
}
async function setRange(locator, value) {
  const limits = await locator.evaluate((element) => ({
    min: Number(element.min),
    max: Number(element.max),
    step: Number(element.step),
  }));
  assert.ok(value >= limits.min && value <= limits.max);
  const start = value - limits.min <= limits.max - value;
  await locator.focus();
  await locator.press(start ? "Home" : "End");
  const steps = Math.round(
    (start ? value - limits.min : limits.max - value) / limits.step,
  );
  for (let index = 0; index < steps; index += 1)
    await locator.press(start ? "ArrowRight" : "ArrowLeft");
  assert.equal(Number(await locator.inputValue()), value);
}
async function save() {
  const responseWait = page.waitForResponse(
    (response) =>
      response.url().endsWith("/edit-spec") &&
      response.request().method() === "PUT",
  );
  await saved().click();
  const response = await responseWait;
  assert.ok([200, 201].includes(response.status()));
  seed.saved_revision = await response.json();
  await persist();
  return seed.saved_revision;
}
async function waitState(id, predicate, timeout = 300_000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const row = renderStates.findLast((item) => item.id === id);
    if (row && predicate(row)) return row;
    if (row && ["failed", "completed", "canceled"].includes(row.status))
      throw new Error(
        `Real worker reached ${row.status} before the required UI observation`,
      );
    await page.waitForTimeout(100);
  }
  throw new Error("UI polling did not observe required real job state");
}
async function startExport(selected) {
  await profile().selectOption(selected);
  const responseWait = page.waitForResponse(
    (response) =>
      response.url().endsWith("/renders") &&
      response.request().method() === "POST",
  );
  await generate().focus();
  await page.keyboard.press("Enter");
  const response = await responseWait;
  assert.equal(response.status(), 202);
  return response.json();
}
async function nativePlayback(target = page) {
  const video = exportedVideo(target);
  await video.waitFor();
  await target.waitForFunction((id) => {
    const element = document.querySelector(
      'video[aria-label="Vídeo exportado"]',
    );
    return (
      element?.readyState >= 1 &&
      !element.error &&
      element.getAttribute("src")?.includes(id)
    );
  }, seed.completed_job.id);
  await video.evaluate((element) => {
    element.muted = true;
    element.currentTime = 1.5;
  });
  await target.waitForFunction(() => {
    const element = document.querySelector(
      'video[aria-label="Vídeo exportado"]',
    );
    return element?.readyState >= 2 && !element.seeking && !element.error;
  });
  const before = await video.evaluate((element) => element.currentTime);
  await video.evaluate((element) => element.play());
  await target.waitForFunction((time) => {
    const element = document.querySelector(
      'video[aria-label="Vídeo exportado"]',
    );
    return element && !element.paused && element.currentTime > time + 0.2;
  }, before);
  const state = await video.evaluate((element) => {
    element.pause();
    return {
      current_time: element.currentTime,
      duration: element.duration,
      video_width: element.videoWidth,
      video_height: element.videoHeight,
      decoded_frames:
        element.getVideoPlaybackQuality?.().totalVideoFrames ?? null,
      error: element.error?.code ?? null,
    };
  });
  assert.ok(state.duration > 8 && state.decoded_frames > 0 && !state.error);
  passed("real_final_mp4_native_play_seek_pause", state);
}
async function screenshotLayouts() {
  for (const theme of ["claro", "escuro"]) {
    const button = page.getByRole("button", {
      name: `Ativar tema ${theme}`,
      exact: true,
    });
    if (await button.count()) await button.click();
    await capture(`desktop-${theme}`);
  }
  const storageState = await context.storageState();
  for (const width of [375, 390]) {
    const mobile = await browser.newContext({
      viewport: { width, height: 844 },
      isMobile: true,
      hasTouch: true,
      deviceScaleFactor: 1,
      storageState,
    });
    const target = await mobile.newPage();
    observe(target);
    await target.goto(editorUrl(), { waitUntil: "networkidle" });
    await panel(target).waitFor();
    const metrics = await target.evaluate(() => ({
      width: innerWidth,
      scroll_width: document.documentElement.scrollWidth,
    }));
    assert.ok(
      metrics.scroll_width <= width + 1,
      "Export layout must fit mobile viewport",
    );
    passed(`mobile_${width}_layout_no_horizontal_overflow`, metrics);
    for (const theme of ["claro", "escuro"]) {
      const button = target.getByRole("button", {
        name: `Ativar tema ${theme}`,
        exact: true,
      });
      if (await button.count()) await button.click();
      await capture(`mobile-${width}-${theme}`, target);
    }
    await mobile.close();
  }
}
async function exportFlow() {
  assert.equal(
    seed,
    null,
    "Export stage requires a fresh --work; preserve failed attempt separately",
  );
  seed = {
    project: await api("/projects", {
      method: "POST",
      data: {
        name: `QA R05 browser ${runId}`,
        purpose: "Synthetic Chromium export validation",
        target_preset: "9:16",
      },
    }),
  };
  await persist();
  await page.goto(`${base}/#/projects/${seed.project.id}`, {
    waitUntil: "networkidle",
  });
  await page
    .getByLabel("Selecionar vídeo MP4", { exact: true })
    .setInputFiles(`${fixtures}/horizontal.mp4`);
  const uploadedWait = page.waitForResponse(
    (response) =>
      response.url().endsWith("/media") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Importar MP4", exact: true }).click();
  const uploaded = await uploadedWait;
  assert.equal(uploaded.status(), 201);
  seed.source = await uploaded.json();
  assert.equal(
    seed.source.checksum_sha256,
    createHash("sha256")
      .update(await readFile(`${fixtures}/horizontal.mp4`))
      .digest("hex"),
  );
  await persist();
  passed("real_mp4_import_through_ui");
  await page.goto(editorUrl(), { waitUntil: "networkidle" });
  await panel().waitFor();
  await page.getByLabel("Entrada (ms)", { exact: true }).fill("1000");
  await page.getByLabel("Saída (ms)", { exact: true }).fill("13000");
  await page.getByRole("button", { name: "9:16", exact: true }).click();
  await page
    .getByRole("radio", { name: "Preencher (cover)", exact: true })
    .check();
  await page
    .getByRole("button", { name: "Adicionar texto", exact: true })
    .click();
  await page.getByLabel("Conteúdo do texto 1", { exact: true }).fill(text);
  await page.getByLabel("Início do texto 1 (ms)", { exact: true }).fill("1000");
  await page.getByLabel("Fim do texto 1 (ms)", { exact: true }).fill("13000");
  await page
    .getByLabel("Selecionar música MP3", { exact: true })
    .setInputFiles(`${fixtures}/short.mp3`);
  const musicWait = page.waitForResponse(
    (response) =>
      response.url().endsWith("/audio") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Importar MP3", exact: true }).click();
  const music = await musicWait;
  assert.equal(music.status(), 201);
  seed.audio = await music.json();
  assert.equal(
    seed.audio.checksum_sha256,
    createHash("sha256")
      .update(await readFile(`${fixtures}/short.mp3`))
      .digest("hex"),
  );
  await page
    .getByLabel("Música de fundo", { exact: true })
    .selectOption(seed.audio.id);
  await setRange(
    page.getByRole("slider", { name: "Volume original", exact: true }),
    70,
  );
  await setRange(
    page.getByRole("slider", { name: "Volume da música", exact: true }),
    15,
  );
  await page.getByLabel("Repetir música", { exact: true }).check();
  await page.getByLabel("Fade-in (ms)", { exact: true }).fill("1000");
  await page.getByLabel("Fade-out (ms)", { exact: true }).fill("1500");
  assert.equal(await generate().isDisabled(), true);
  const createCount = creates.length;
  await page.waitForTimeout(100);
  assert.equal(creates.length, createCount);
  passed("dirty_draft_cannot_export_or_implicitly_save");
  const revision = await save();
  assert.equal(revision.schema_version, 2);
  assert.equal(revision.text_overlays[0].text, text);
  passed("explicit_saved_v2_from_manual_ui");
  const job = await startExport("standard");
  seed.completed_job = job;
  await persist();
  assert.equal(creates.at(-1).edit_spec_revision_id, revision.id);
  await waitState(
    job.id,
    (row) =>
      row.status === "running" &&
      row.stage === "encoding" &&
      row.progress_percent > 0,
  );
  const progress = panel().getByRole("progressbar", {
    name: "Progresso da renderização",
    exact: true,
  });
  await progress.waitFor();
  await page.waitForFunction(() => {
    const element = document.querySelector(
      '[aria-label="Progresso da renderização"]',
    );
    if (!element) return false;
    const value =
      element instanceof HTMLProgressElement
        ? element.value
        : Number(element.getAttribute("aria-valuenow"));
    return value > 0 && value <= 99;
  });
  const visibleProgress = await progress.evaluate((element) =>
    element instanceof HTMLProgressElement
      ? element.value
      : Number(element.getAttribute("aria-valuenow")),
  );
  assert.ok(
    renderStates.some(
      (row) => row.id === job.id && row.progress_percent === visibleProgress,
    ),
  );
  passed("visible_progress_matches_real_worker_status", {
    percent: visibleProgress,
  });
  await capture("desktop-encoding-progress");
  const completedState = await waitState(
    job.id,
    (row) => row.status === "completed",
  );
  assert.equal(completedState.progress_percent, 100);
  assert.ok(
    renderStates.some(
      (row) =>
        row.id === job.id &&
        row.stage === "encoding" &&
        row.progress_encoded_ms > 0 &&
        row.progress_percent < 100,
    ),
  );
  const complete = await api(`/projects/${seed.project.id}/renders/${job.id}`);
  assert.equal(complete.status, "completed");
  assert.equal(complete.revision_id, revision.id);
  assert.ok(Math.abs(complete.duration_ms - 12000) <= 35);
  assert.equal(complete.width, 1080);
  assert.equal(complete.height, 1920);
  seed.completed_job = complete;
  await persist();
  passed("real_worker_full_export_and_measured_progress");
  await nativePlayback();
  await mkdir("/work/downloads", { recursive: true, mode: 0o700 });
  const downloadWait = page.waitForEvent("download");
  await selectedResult()
    .getByRole("link", { name: "Baixar MP4", exact: true })
    .first()
    .click();
  const download = await downloadWait;
  assert.match(download.suggestedFilename(), /^edivo-r\d+-9x16-standard\.mp4$/);
  const destination = `/work/downloads/${runId}.mp4`;
  await download.saveAs(destination);
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(destination)) hash.update(chunk);
  assert.equal(hash.digest("hex"), complete.checksum_sha256);
  assert.equal((await stat(destination)).size, complete.byte_size);
  passed("native_browser_download_sha256_matches_saved_artifact", {
    checksum_sha256: complete.checksum_sha256,
    byte_size: complete.byte_size,
    suggested_filename: download.suggestedFilename(),
  });
  assert.ok(
    ranges.some(
      (row) =>
        row.path === complete.file_url && row.range && row.status === 206,
    ),
  );
  passed(
    "real_browser_final_mp4_range_206",
    ranges.filter((row) => row.path === complete.file_url),
  );
  await screenshotLayouts();
  await page
    .getByLabel("Conteúdo do texto 1", { exact: true })
    .fill("Revisão posterior da prova Chromium");
  assert.equal(await generate().isDisabled(), true);
  await save();
  await panel()
    .getByText(
      `Esta exportação mantém a revisão ${seed.completed_job.revision_number}; a edição está na revisão ${seed.saved_revision.revision_number}.`,
      { exact: true },
    )
    .waitFor();
  passed("earlier_revision_export_is_identified_without_rewriting_result");
  const canceled = await startExport("high");
  seed.cancel_job = canceled;
  await persist();
  await waitState(
    canceled.id,
    (row) =>
      row.status === "running" &&
      row.stage === "encoding" &&
      row.progress_encoded_ms > 0,
  );
  await panel()
    .getByRole("button", { name: "Cancelar renderização", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Confirmar cancelamento",
    exact: true,
  });
  await dialog.waitFor();
  const cancelWait = page.waitForResponse((response) =>
    response.url().endsWith(`/${canceled.id}/cancel`),
  );
  await dialog
    .getByRole("button", { name: "Confirmar cancelamento", exact: true })
    .focus();
  await page.keyboard.press("Enter");
  const cancelResponse = await cancelWait;
  assert.equal(cancelResponse.status(), 202);
  await waitState(canceled.id, (row) => row.status === "canceled");
  seed.cancel_job = await api(
    `/projects/${seed.project.id}/renders/${canceled.id}`,
  );
  assert.equal(seed.cancel_job.artifact_available, false);
  assert.equal(seed.cancel_job.file_url, null);
  passed("real_running_cancel_confirmed_without_published_file");
  await page.reload({ waitUntil: "networkidle" });
  await panel().waitFor();
  await panel()
    .getByText("Histórico de exportações", { exact: true })
    .waitFor();
  const history = await api(
    `/projects/${seed.project.id}/renders?limit=20&offset=0`,
  );
  assert.equal(history.total, 2);
  seed.history_ids = history.items.map((item) => item.id);
  for (const id of seed.history_ids)
    await page
      .getByRole("article", { name: `Exportação ${id}`, exact: true })
      .waitFor();
  await page
    .getByRole("button", {
      name: `Ver exportação ${seed.completed_job.id.slice(0, 8)}`,
      exact: true,
    })
    .focus();
  await page.keyboard.press("Enter");
  await nativePlayback();
  passed("history_and_saved_revision_recover_after_reload", {
    render_ids: seed.history_ids,
  });
  seed.storage_state = await context.storageState();
  await persist();
}
async function verifyFlow() {
  assert.ok(
    seed?.completed_job && seed.history_ids,
    "Verification requires successful export-stage seed",
  );
  assert.ok(seed.project.name.startsWith("QA R05 browser "));
  await page.goto(editorUrl(), { waitUntil: "networkidle" });
  await panel().waitFor();
  const revision = await api(`/projects/${seed.project.id}/edit-spec`);
  assert.equal(revision.id, seed.saved_revision.id);
  const history = await api(
    `/projects/${seed.project.id}/renders?limit=20&offset=0`,
  );
  assert.deepEqual(
    history.items.map((item) => item.id),
    seed.history_ids,
  );
  const completed = await api(
    `/projects/${seed.project.id}/renders/${seed.completed_job.id}`,
  );
  assert.equal(completed.status, "completed");
  assert.equal(completed.checksum_sha256, seed.completed_job.checksum_sha256);
  assert.equal(completed.byte_size, seed.completed_job.byte_size);
  passed("real_history_artifact_and_revision_persist_after_service_restart");
  await panel()
    .getByText("Histórico de exportações", { exact: true })
    .waitFor();
  await page
    .getByRole("button", {
      name: `Ver exportação ${seed.completed_job.id.slice(0, 8)}`,
      exact: true,
    })
    .click();
  await nativePlayback();
  await screenshotLayouts();
}
try {
  await mkdir("/work/artifacts", { recursive: true, mode: 0o700 });
  if (stage === "export") await exportFlow();
  else await verifyFlow();
  assert.equal(pageErrors.length, 0);
  passed("no_uncaught_browser_errors");
} catch (error) {
  failure = { name: error.name, message: error.message, stack: error.stack };
  console.error(JSON.stringify(failure));
  await capture("failure").catch(() => {});
} finally {
  const report = {
    status: failure ? "failed" : "passed",
    run_id: runId,
    stage,
    browser: browser.version(),
    project_id: seed?.project?.id ?? null,
    checks,
    screenshots,
    render_states: renderStates,
    range_responses: ranges,
    ui_save_requests: saves,
    ui_render_requests: creates,
    page_errors: pageErrors,
    human_auditory_inspection_performed: false,
    device_scope:
      "Chromium headless; mobile375/390 emulated, no physical devices",
    native_events: await page
      .evaluate(() => window.edivoR05Events ?? [])
      .catch(() => []),
    application_lifecycle_performed: false,
    ...(failure ? { failure } : {}),
  };
  await writeFile(
    `/work/artifacts/${runId}.json`,
    JSON.stringify(report, null, 2),
  );
  console.log(
    JSON.stringify({ status: report.status, checks: checks.length, stage }),
  );
  await browser.close();
}
if (failure) process.exitCode = 1;
