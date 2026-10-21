/** R04 browser evidence uses real API/storage, native media events and no mocks. */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';

const { chromium } = await import(process.env.EDIVO_PLAYWRIGHT_MODULE ?? 'playwright');
const base = process.env.EDIVO_BROWSER_BASE_URL;
const runId = process.env.EDIVO_BROWSER_RUN_ID ?? `r04-browser-${Date.now()}`;
const stage = process.env.EDIVO_BROWSER_STAGE ?? 'composition';
assert.ok(base, 'EDIVO_BROWSER_BASE_URL is required');
const work = '/work';
const checks = [];
const screenshots = [];
const pageErrors = [];
const streamResponses = [];
const previewFileResponses = [];
const previewStatuses = [];
const uiPreviews = [];
const uiSaves = [];
const dialogs = [];
const dialogDecisions = [];
const overlayText = "Edição: 50% 'á' \\ Ω\nAção, çã — teste";
const overlayCases = [
  {
    text: overlayText,
    start_ms: 2000,
    end_ms: 10000,
    position: 'top',
    style: 'blue_outline',
    size: 'medium',
  },
  {
    text: 'Centro — texto 2',
    start_ms: 3000,
    end_ms: 9000,
    position: 'center',
    style: 'white_outline',
    size: 'small',
  },
  {
    text: 'Base — texto 3',
    start_ms: 4000,
    end_ms: 8000,
    position: 'bottom',
    style: 'white_plain',
    size: 'large',
  },
];
const passed = (check, detail) => {
  const item = {
    check,
    status: 'passed',
    ...(detail === undefined ? {} : { detail: structuredClone(detail) }),
  };
  checks.push(item);
  console.log(JSON.stringify(item));
};
const browser = await chromium.launch({ headless: true });
const retainedSeed = await readFile(`${work}/seed-r04.json`, 'utf8')
  .then(JSON.parse)
  .catch(() => null);
const context = await browser.newContext({
  viewport: { width: 1440, height: 1100 },
  colorScheme: 'dark',
  ...(stage !== 'composition' && retainedSeed?.storage_state
    ? { storageState: retainedSeed.storage_state }
    : {}),
});
context.setDefaultTimeout(15_000);
const page = await context.newPage();
const request = context.request;
let seed;
let failure;

function observe(target) {
  target.on('pageerror', (error) => pageErrors.push(error.message));
  target.on('dialog', async (dialog) => {
    const accept = dialogDecisions.shift() ?? true;
    dialogs.push({
      type: dialog.type(),
      message: dialog.message(),
      accepted: accept,
    });
    if (accept) await dialog.accept();
    else await dialog.dismiss();
  });
  target.on('request', (outgoing) => {
    if (outgoing.method() === 'PUT' && outgoing.url().endsWith('/edit-spec')) {
      uiSaves.push({
        path: new URL(outgoing.url()).pathname,
        body: outgoing.postDataJSON(),
      });
    }
    if (outgoing.method() === 'POST' && outgoing.url().endsWith('/previews'))
      uiPreviews.push({ path: new URL(outgoing.url()).pathname, body: outgoing.postDataJSON() });
  });
  target.on('response', async (response) => {
    if (/\/previews\/[0-9a-f-]+$/.test(response.url())) {
      const body = await response.json().catch(() => null);
      if (body?.id && body.status) {
        const item = {
          id: body.id,
          status: body.status,
          stage: body.stage,
          attempts: body.attempts,
        };
        if (previewStatuses.at(-1)?.stage !== item.stage)
          console.log(JSON.stringify({ checkpoint: 'preview_status', ...item }));
        previewStatuses.push(item);
      }
    }
    const original = response.url().endsWith('/stream');
    const preview = /\/previews\/[0-9a-f-]+\/file$/.test(response.url());
    if (!original && !preview) return;
    (original ? streamResponses : previewFileResponses).push({
      path: new URL(response.url()).pathname,
      status: response.status(),
      range: response.request().headers().range ?? null,
      content_range: response.headers()['content-range'] ?? null,
    });
  });
}
observe(page);
await context.addInitScript(() => {
  window.edivoQaNativeEvents = [];
  for (const name of [
    'loadedmetadata',
    'playing',
    'pause',
    'timeupdate',
    'seeking',
    'seeked',
    'error',
  ]) {
    document.addEventListener(
      name,
      (event) => {
        if (event.target instanceof HTMLVideoElement) {
          window.edivoQaNativeEvents.push({
            name,
            time: event.target.currentTime,
            label: event.target.getAttribute('aria-label'),
          });
        }
      },
      true,
    );
  }
});

const editorPath = () => `/#/projects/${seed.project.id}/editor`;
const video = (target = page) => target.getByLabel('Vídeo do projeto', { exact: true });
const saveButton = (target = page) =>
  target.getByRole('button', { name: 'Salvar edição', exact: true });
const textInput = (index, target = page) =>
  target.getByLabel(`Conteúdo do texto ${index}`, { exact: true });
const overlay = (text, target = page) =>
  target.locator('.composition-frame').getByText(text, { exact: true });

async function api(path, options = {}) {
  const response = await request.fetch(`${base}/api/v1${path}`, options);
  const body = await response.json().catch(() => null);
  assert.ok(response.ok(), `API ${path}: HTTP ${response.status()} ${JSON.stringify(body)}`);
  return body;
}

async function persistSeed() {
  await writeFile(`${work}/seed-r04.json`, JSON.stringify(seed, null, 2));
}

async function prepareSeed() {
  seed = await readFile(`${work}/seed-r04.json`, 'utf8')
    .then(JSON.parse)
    .catch(() => null);
  const retainedProject = process.env.EDIVO_BROWSER_PROJECT_ID;
  if (!seed && retainedProject) {
    const project = await api(`/projects/${retainedProject}`);
    assert.ok(
      project.name.startsWith('QA R04 '),
      'Only a named synthetic QA project can be recovered',
    );
    const current = await api(`/projects/${retainedProject}/edit-spec`);
    if (stage === 'composition')
      assert.equal(current.schema_version, 1, 'Composition recovery must still have the legacy v1');
    const source = await api(`/projects/${retainedProject}/media/${current.source_media_id}`);
    seed = {
      project,
      recovered_by: runId,
      assets: { 'horizontal.mp4': source },
      ...(current.schema_version === 1
        ? { legacy_revision: current }
        : { saved_revision: current }),
    };
    if (current.schema_version === 2 && current.audio.background_audio_id)
      seed.music = await api(
        `/projects/${retainedProject}/audio/${current.audio.background_audio_id}`,
      );
    await persistSeed();
    passed('retained_synthetic_project_recovered_by_read_only_get_requests', {
      project_id: project.id,
      revision_id: current.id,
      revision_number: current.revision_number,
      schema_version: current.schema_version,
    });
  }
  if (!seed) {
    assert.equal(stage, 'composition', 'Later stages require the retained R04 seed');
    seed = {
      project: await api('/projects', {
        method: 'POST',
        data: {
          name: `QA R04 ${runId}`,
          purpose: 'Mídia sintética própria para QA R04',
          target_preset: '16:9',
        },
      }),
      created_by: runId,
      assets: {},
    };
    await persistSeed();
  }
  await api(`/projects/${seed.project.id}`);
  if (stage !== 'composition') return;
  if (!seed.assets['horizontal.mp4']) {
    const buffer = await readFile(`${work}/fixtures/horizontal.mp4`);
    const asset = await api(`/projects/${seed.project.id}/media`, {
      method: 'POST',
      multipart: {
        file: { name: 'horizontal.mp4', mimeType: 'video/mp4', buffer },
      },
    });
    assert.equal(asset.checksum_sha256, createHash('sha256').update(buffer).digest('hex'));
    seed.assets['horizontal.mp4'] = asset;
    await persistSeed();
  }
  if (!seed.legacy_revision) {
    seed.legacy_revision = await api(`/projects/${seed.project.id}/edit-spec`, {
      method: 'PUT',
      data: {
        expected_revision: 0,
        schema_version: 1,
        source_media_id: seed.assets['horizontal.mp4'].id,
        trim: { start_ms: 2000, end_ms: 11000 },
        canvas: { preset: '16:9', fit: 'cover', focal_x: 0.5, focal_y: 0.5 },
      },
    });
    await persistSeed();
  }
  const current = await api(`/projects/${seed.project.id}/edit-spec`);
  assert.deepEqual(current, seed.legacy_revision, 'Composition starts from the retained legacy v1');
  assert.equal(current.schema_version, 1);
  assert.equal(current.revision_number, 1);
  assert.equal('text_overlays' in current, false);
  assert.equal('audio' in current, false);
  passed('synthetic_source_import_and_v1_revision_saved_before_opening_ui', {
    project_id: seed.project.id,
    revision_id: current.id,
    source_sha256: seed.assets['horizontal.mp4'].checksum_sha256,
  });
}

async function openEditor(target = page) {
  await target.goto(`${base}${editorPath()}`, { waitUntil: 'networkidle' });
  await target.getByRole('heading', { name: 'Editor do projeto.', exact: true }).waitFor();
  await target.waitForFunction((id) => {
    const element = document.querySelector('video');
    return element?.getAttribute('src')?.includes(id) && element.readyState >= 2 && !element.error;
  }, seed.assets['horizontal.mp4'].id);
}

async function mediaState(target = page) {
  return video(target).evaluate((element) => ({
    current_time: element.currentTime,
    paused: element.paused,
    duration: element.duration,
    ready_state: element.readyState,
    width: element.videoWidth,
    height: element.videoHeight,
    src: element.getAttribute('src'),
    volume: element.volume,
    decoded_frames: element.getVideoPlaybackQuality?.().totalVideoFrames ?? null,
    audio_decoded_bytes: element.webkitAudioDecodedByteCount ?? null,
    events: window.edivoQaNativeEvents ?? [],
  }));
}

async function seek(seconds, target = page) {
  await video(target).evaluate((element, time) => {
    element.currentTime = time;
  }, seconds);
  await target.waitForFunction((time) => {
    const element = document.querySelector('video');
    return element && !element.seeking && Math.abs(element.currentTime - time) < 0.05;
  }, seconds);
}

async function playPause(target = page) {
  const before = await mediaState(target);
  await target.getByRole('button', { name: 'Reproduzir vídeo', exact: true }).click();
  await target.waitForFunction((time) => {
    const element = document.querySelector('video');
    return element && !element.paused && element.currentTime > time + 0.15;
  }, before.current_time);
  const pauseCount = await target.evaluate(
    () => window.edivoQaNativeEvents.filter((event) => event.name === 'pause').length,
  );
  await target.getByRole('button', { name: 'Pausar vídeo', exact: true }).click();
  await target.waitForFunction((previousPauses) => {
    const element = document.querySelector('video');
    return (
      element?.paused === true &&
      window.edivoQaNativeEvents.filter((event) => event.name === 'pause').length > previousPauses
    );
  }, pauseCount);
  const after = await mediaState(target);
  assert.ok(after.decoded_frames > 0);
  assert.ok(after.audio_decoded_bytes > 0);
  if (after.events.length) {
    for (const name of ['playing', 'pause', 'timeupdate']) {
      assert.ok(
        after.events.some((event) => event.name === name),
        `Native ${name} must occur`,
      );
    }
  }
  return {
    advanced_seconds: after.current_time - before.current_time,
    ...after,
  };
}

async function capture(name, target = page, element = null) {
  await target.evaluate(() => document.fonts.ready);
  if (!element)
    await target.evaluate(() => {
      document.activeElement?.blur();
      window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
    });
  const filename = `${runId}-${name}.png`;
  if (element)
    await element.screenshot({
      path: `${work}/artifacts/${filename}`,
      animations: 'disabled',
    });
  else
    await target.screenshot({
      path: `${work}/artifacts/${filename}`,
      fullPage: true,
      animations: 'disabled',
    });
  screenshots.push(filename);
}

async function setRange(locator, value) {
  const { min, max, step } = await locator.evaluate((element) => ({
    min: Number(element.min),
    max: Number(element.max),
    step: Number(element.step),
  }));
  assert.ok(value >= min && value <= max);
  const fromStart = value - min <= max - value;
  await locator.focus();
  await locator.press(fromStart ? 'Home' : 'End');
  const steps = Math.round((fromStart ? value - min : max - value) / step);
  for (let index = 0; index < steps; index += 1)
    await locator.press(fromStart ? 'ArrowRight' : 'ArrowLeft');
  assert.equal(Number(await locator.inputValue()), value);
}

async function configureText() {
  for (const [index, item] of overlayCases.entries()) {
    const number = index + 1;
    await page.getByRole('button', { name: 'Adicionar texto', exact: true }).click();
    await textInput(number).fill(item.text);
    await page
      .getByLabel(`Início do texto ${number} (ms)`, { exact: true })
      .fill(String(item.start_ms));
    await page.getByLabel(`Fim do texto ${number} (ms)`, { exact: true }).fill(String(item.end_ms));
    assert.equal(
      await page.getByLabel(`Fim do texto ${number} (ms)`, { exact: true }).inputValue(),
      String(item.end_ms),
    );
    await page
      .getByRole('combobox', { name: new RegExp(`^Posição do texto ${number}`) })
      .selectOption(item.position);
    await page
      .getByRole('combobox', { name: new RegExp(`^Estilo do texto ${number}`) })
      .selectOption(item.style);
    await page
      .getByRole('combobox', { name: new RegExp(`^Tamanho do texto ${number}`) })
      .selectOption(item.size);
  }
  assert.equal(
    await page.getByRole('button', { name: 'Adicionar texto', exact: true }).isDisabled(),
    true,
  );
  await textInput(1).fill('Uma\nDuas\nTrês');
  assert.equal(await saveButton().isDisabled(), true, 'Invalid text must block an explicit save');
  assert.equal(uiSaves.length, 0);
  await textInput(1).fill(overlayText);
  assert.equal(await saveButton().isEnabled(), true);
  passed('three_texts_limit_positions_styles_unicode_and_invalid_three_lines_block_save');

  await page.getByRole('button', { name: '9:16', exact: true }).click();
  await page.getByRole('radio', { name: 'Ajustar (contain)', exact: true }).check();
  const ratio = await page.locator('.composition-frame').boundingBox();
  assert.ok(Math.abs(ratio.width / ratio.height - 9 / 16) < 0.01);
  assert.equal(await video().evaluate((element) => element.style.objectFit), 'contain');

  const observations = [];
  for (const [time, visible] of [
    [1, []],
    [2.5, [0]],
    [3.5, [0, 1]],
    [5, [0, 1, 2]],
    [9.5, [0]],
    [10.5, []],
  ]) {
    await seek(time);
    for (const [index, item] of overlayCases.entries()) {
      if (visible.includes(index)) await overlay(item.text).waitFor({ state: 'visible' });
      else await overlay(item.text).waitFor({ state: 'hidden' });
    }
    observations.push({
      native_time: (await mediaState()).current_time,
      visible_positions: visible.map((index) => overlayCases[index].position),
    });
  }
  await seek(5);
  const frame = await page.locator('.composition-frame').boundingBox();
  const measurements = [];
  for (const item of overlayCases) {
    const element = overlay(item.text);
    const box = await element.boundingBox();
    assert.ok(box);
    assert.ok(box.x >= frame.x + frame.width * 0.07 - 2);
    assert.ok(box.x + box.width <= frame.x + frame.width * 0.93 + 2);
    assert.ok(box.y >= frame.y + frame.height * 0.07 - 2);
    assert.ok(box.y + box.height <= frame.y + frame.height * 0.93 + 2);
    measurements.push({
      position: item.position,
      box,
      computed: await element.evaluate((node) => {
        const style = getComputedStyle(node);
        return {
          color: style.color,
          font_family: style.fontFamily,
          font_size: style.fontSize,
          text_shadow: style.textShadow,
        };
      }),
    });
  }
  await capture('native-frame-texts', page, page.locator('.composition-frame'));
  passed('overlays_follow_native_current_time_and_fit_seven_percent_safe_area', {
    observations,
    measurements,
  });
}

async function uploadMusic() {
  if (seed.music) {
    await page.getByLabel('Música de fundo', { exact: true }).selectOption(seed.music.id);
    return;
  }
  await page
    .getByLabel('Selecionar música MP3', { exact: true })
    .setInputFiles(`${work}/fixtures/music-short.mp3`);
  const responsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/projects/${seed.project.id}/audio`) &&
      response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Importar MP3', exact: true }).click();
  const response = await responsePromise;
  assert.equal(response.status(), 201);
  assert.equal(response.request().resourceType(), 'xhr');
  seed.music = await response.json();
  const buffer = await readFile(`${work}/fixtures/music-short.mp3`);
  assert.equal(seed.music.checksum_sha256, createHash('sha256').update(buffer).digest('hex'));
  assert.equal(seed.music.byte_size, buffer.length);
  const inventory = await api(`/projects/${seed.project.id}/audio/${seed.music.id}`);
  assert.deepEqual(inventory, seed.music);
  await persistSeed();
  await page.getByLabel('Música de fundo', { exact: true }).selectOption(seed.music.id);
  passed('mp3_import_uses_real_ui_xhr_and_matches_api_inventory_sha256', {
    audio_id: seed.music.id,
    bytes: seed.music.byte_size,
    sha256: seed.music.checksum_sha256,
    transport: 'xhr',
  });
}

async function configureAudio() {
  await uploadMusic();
  await setRange(page.getByRole('slider', { name: 'Volume original', exact: true }), 125);
  await setRange(page.getByRole('slider', { name: 'Volume da música', exact: true }), 15);
  await page.getByLabel('Repetir música', { exact: true }).check();
  await page.getByLabel('Fade-in (ms)', { exact: true }).fill('500');
  await page.getByLabel('Fade-out (ms)', { exact: true }).fill('500');
  assert.equal(
    await page.locator('audio').count(),
    0,
    'Music is not falsely played by a parallel native audio tag',
  );
  assert.ok((await mediaState()).volume <= 1, 'Native HTML5 gain cannot exceed its real bound');
  passed('audio_controls_set_125_percent_source_15_percent_music_loop_and_full_trim_fades', {
    audible_mix_validation: 'deferred to the actual encoded preview; no manual listening claimed',
  });
}

function snapshot(revision) {
  const { id, project_id, revision_number, created_at, ...plan } = revision;
  return plan;
}

async function saveV2() {
  assert.deepEqual(await api(`/projects/${seed.project.id}/edit-spec`), seed.legacy_revision);
  assert.equal(uiSaves.length, 0, 'Editing controls never silently persist v2');
  const responsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/projects/${seed.project.id}/edit-spec`) &&
      response.request().method() === 'PUT',
  );
  await saveButton().click();
  const response = await responsePromise;
  assert.equal(response.status(), 200);
  const saved = await response.json();
  assert.equal(saved.revision_number, 2);
  assert.equal(saved.schema_version, 2);
  assert.equal('expected_revision' in saved, false);
  assert.equal(new Set(saved.text_overlays.map((item) => item.id)).size, 3);
  assert.deepEqual(snapshot(saved), {
    schema_version: 2,
    source_media_id: seed.assets['horizontal.mp4'].id,
    trim: { start_ms: 2000, end_ms: 11000 },
    canvas: { preset: '9:16', fit: 'contain', focal_x: 0.5, focal_y: 0.5 },
    text_overlays: overlayCases.map((item, index) => ({
      id: saved.text_overlays[index].id,
      ...item,
    })),
    audio: {
      source_volume: 1.25,
      background_audio_id: seed.music.id,
      background_volume: 0.15,
      background_loop: true,
      fade_in_ms: 500,
      fade_out_ms: 500,
    },
  });
  assert.equal(uiSaves.length, 1);
  assert.equal(uiSaves[0].body.expected_revision, 1);
  await page.getByText('Edição salva · Revisão 2', { exact: true }).waitFor();
  assert.equal(await saveButton().isDisabled(), true);
  assert.deepEqual(await api(`/projects/${seed.project.id}/edit-spec`), saved);
  seed.saved_revision = saved;
  await persistSeed();
  passed('explicit_save_creates_revision_two_v2_and_deep_get_matches_all_text_audio_values', {
    revision_id: saved.id,
    revision_number: saved.revision_number,
    schema_version: saved.schema_version,
  });
}

async function assertSavedUI(target = page) {
  await openEditor(target);
  assert.equal(await saveButton(target).isDisabled(), true);
  for (const [index, item] of overlayCases.entries())
    assert.equal(await textInput(index + 1, target).inputValue(), item.text);
  assert.equal(
    await target.getByLabel('Música de fundo', { exact: true }).inputValue(),
    seed.music.id,
  );
  assert.equal(
    Number(await target.getByRole('slider', { name: 'Volume original', exact: true }).inputValue()),
    125,
  );
  assert.equal(
    Number(
      await target.getByRole('slider', { name: 'Volume da música', exact: true }).inputValue(),
    ),
    15,
  );
  assert.equal(await target.getByLabel('Repetir música', { exact: true }).isChecked(), true);
  assert.equal(await target.getByLabel('Fade-in (ms)', { exact: true }).inputValue(), '500');
  assert.equal(await target.getByLabel('Fade-out (ms)', { exact: true }).inputValue(), '500');
}

async function persistenceAndNavigation() {
  await assertSavedUI();
  assert.deepEqual(await api(`/projects/${seed.project.id}/edit-spec`), seed.saved_revision);
  await seek(5);
  assert.equal(await saveButton().isDisabled(), true);
  const edited = `${overlayText} *`;
  await textInput(1).fill(edited);
  await page.getByText('Alterações não salvas', { exact: true }).waitFor();
  dialogDecisions.push(false);
  const previousDialogs = dialogs.length;
  const navigationDialog = page.waitForEvent('dialog');
  await page.getByRole('link', { name: 'Voltar à biblioteca', exact: true }).click();
  await navigationDialog;
  await page.waitForURL((url) => url.hash === `#/projects/${seed.project.id}/editor`);
  assert.equal(dialogs.length, previousDialogs + 1);
  assert.equal(dialogs.at(-1).accepted, false);
  assert.ok(page.url().endsWith(editorPath()));
  assert.equal(await textInput(1).inputValue(), edited);
  assert.deepEqual(await api(`/projects/${seed.project.id}/edit-spec`), seed.saved_revision);
  await textInput(1).fill(overlayText);
  assert.equal(await saveButton().isDisabled(), true);
  assert.equal(uiSaves.length, 1);
  passed('reload_persists_v2_playhead_stays_clean_and_cancel_navigation_retains_only_local_draft');
}

async function themesAndMobile() {
  await seek(5);
  await capture('desktop-dark');
  await page.getByRole('button', { name: 'Ativar tema claro', exact: true }).click();
  assert.equal(await page.locator('html').getAttribute('data-theme'), 'light');
  await capture('desktop-light');
  await page.reload({ waitUntil: 'networkidle' });
  assert.equal(await page.locator('html').getAttribute('data-theme'), 'light');
  assert.equal(await saveButton().isDisabled(), true);
  passed('dark_and_light_themes_persist_without_changing_the_saved_plan');
  const mobileContext = await browser.newContext({
    viewport: { width: 375, height: 812 },
    isMobile: true,
    hasTouch: true,
    colorScheme: 'light',
    deviceScaleFactor: 1,
  });
  const mobile = await mobileContext.newPage();
  observe(mobile);
  for (const width of [375, 390]) {
    await mobile.setViewportSize({ width, height: 844 });
    await assertSavedUI(mobile);
    assert.equal(
      await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      true,
    );
    await seek(5, mobile);
    for (const item of overlayCases) await overlay(item.text, mobile).waitFor({ state: 'visible' });
    await capture(`mobile-${width}`, mobile);
  }
  await mobileContext.close();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  passed('mobile_375_and_390_recover_three_texts_audio_controls_and_have_no_horizontal_overflow');
}

async function compositionChecks() {
  await openEditor();
  assert.equal(await saveButton().isDisabled(), true);
  assert.equal(await page.getByText('Alterações não salvas', { exact: true }).count(), 0);
  assert.deepEqual(await api(`/projects/${seed.project.id}/edit-spec`), seed.legacy_revision);
  assert.equal(uiSaves.length, 0);
  passed('opening_v1_normalizes_only_in_memory_without_dirty_state_or_automatic_save');
  passed('real_mp4_play_pause_uses_native_media_events', await playPause());
  await seek(5);
  assert.equal(await saveButton().isDisabled(), true);
  assert.ok((await mediaState()).events.some((event) => event.name === 'seeked'));
  assert.deepEqual(await api(`/projects/${seed.project.id}/edit-spec`), seed.legacy_revision);
  passed('native_seek_changes_neither_dirty_state_nor_v1_revision');
  await configureText();
  await configureAudio();
  await saveV2();
  await persistenceAndNavigation();
  await themesAndMobile();
  assert.ok(streamResponses.some((response) => response.status === 206 && response.range));
  passed('native_private_video_range_206_observed', streamResponses);
}

async function verifySaved() {
  assert.ok(seed.saved_revision, 'Composition must retain an expected v2 revision');
  assert.deepEqual(await api(`/projects/${seed.project.id}/edit-spec`), seed.saved_revision);
  await assertSavedUI();
  assert.deepEqual(await api(`/projects/${seed.project.id}/audio/${seed.music.id}`), seed.music);
  passed('retained_v2_and_mp3_inventory_recover_after_the_requested_runtime_transition');
  if (seed.preview) {
    const current = await api(`/projects/${seed.project.id}/previews/${seed.preview.id}`);
    assert.equal(current.revision_id, seed.preview.revision_id);
    if (seed.preview.status === 'completed') assert.deepEqual(current, seed.preview);
    seed.preview = current;
    if (stage === 'verify') assert.equal(current.status, 'completed');
    if (current.status === 'completed') {
      await page.getByLabel('Prévia gerada', { exact: true }).waitFor();
      await page
        .getByText(`Prévia da revisão ${seed.saved_revision.revision_number}`, { exact: true })
        .waitFor();
      assert.equal(uiPreviews.length, 0);
      assert.equal(uiSaves.length, 0);
      passed('completed_job_and_same_checksum_recover_by_get_after_restart_without_a_new_render', {
        preview_id: current.id,
        revision_id: current.revision_id,
        sha256: current.checksum_sha256,
        bytes: current.byte_size,
      });
      await inspectPreviewPlayback();
      await capture('persisted-preview');
      if (stage === 'verify') await verifyPreviewMobileFrames();
    }
  }
}

async function inspectPreviewPlayback() {
  const generated = page.getByLabel('Prévia gerada', { exact: true });
  await page.waitForFunction(() => {
    const element = document.querySelector('video[aria-label="Prévia gerada"]');
    return element?.readyState >= 2 && !element.error;
  });
  assert.equal(await generated.getAttribute('src'), seed.preview.file_url);
  assert.equal(await generated.getAttribute('preload'), 'metadata');
  assert.equal(await generated.getAttribute('controlslist'), 'nodownload');
  assert.equal(await generated.evaluate((element) => element.paused), true);
  await generated.evaluate((element) => {
    element.currentTime = 0;
  });
  await page.waitForFunction(() => {
    const element = document.querySelector('video[aria-label="Prévia gerada"]');
    return !element.seeking && element.currentTime < 0.05;
  });
  // Source and preview controls operate on native media; starting one pauses the other.
  await page.getByRole('button', { name: 'Reproduzir vídeo', exact: true }).click();
  await page.waitForFunction(
    () => !document.querySelector('video[aria-label="Vídeo do projeto"]').paused,
  );
  await generated.focus();
  await generated.press('Space');
  await page.waitForFunction(() => {
    const element = document.querySelector('video[aria-label="Prévia gerada"]');
    return element && !element.paused && element.currentTime > 0.2;
  });
  assert.equal(await video().evaluate((element) => element.paused), true);
  const beforePauses = await page.evaluate(
    () =>
      window.edivoQaNativeEvents.filter(
        (event) => event.name === 'pause' && event.label === 'Prévia gerada',
      ).length,
  );
  await generated.press('Space');
  await page.waitForFunction((count) => {
    const element = document.querySelector('video[aria-label="Prévia gerada"]');
    return (
      element?.paused &&
      window.edivoQaNativeEvents.filter(
        (event) => event.name === 'pause' && event.label === 'Prévia gerada',
      ).length > count
    );
  }, beforePauses);
  const state = await generated.evaluate((element) => ({
    duration: element.duration,
    width: element.videoWidth,
    height: element.videoHeight,
    current_time: element.currentTime,
    decoded_frames: element.getVideoPlaybackQuality().totalVideoFrames,
    audio_decoded_bytes: element.webkitAudioDecodedByteCount,
    events: window.edivoQaNativeEvents.filter((event) => event.label === 'Prévia gerada'),
  }));
  assert.ok(state.decoded_frames > 0);
  assert.ok(state.audio_decoded_bytes > 0);
  assert.ok(Math.abs(state.duration * 1000 - seed.preview.duration_ms) < 120);
  assert.deepEqual([state.width, state.height], [540, 960]);
  passed(
    'encoded_preview_has_real_play_pause_native_decoding_audio_and_stops_the_source_player',
    state,
  );
  await generated.evaluate((element) => {
    element.currentTime = 2.5;
  });
  await page.waitForFunction(() => {
    const element = document.querySelector('video[aria-label="Prévia gerada"]');
    return !element.seeking && Math.abs(element.currentTime - 2.5) < 0.05;
  });
  const head = await request.head(`${base}${seed.preview.file_url}`);
  assert.equal(head.status(), 200);
  assert.equal((await head.body()).length, 0);
  const range = await request.get(`${base}${seed.preview.file_url}`, {
    headers: { Range: 'bytes=0-15' },
  });
  assert.equal(range.status(), 206);
  assert.equal((await range.body()).length, 16);
  assert.equal(range.headers()['content-type'], 'video/mp4');
  assert.equal(range.headers()['cache-control'], 'private, no-store');
  assert.equal(range.headers()['x-content-type-options'], 'nosniff');
  assert.ok(previewFileResponses.some((response) => response.status === 206 && response.range));
  passed('encoded_preview_seek_head_and_private_range_206_are_real', {
    native_range: previewFileResponses,
    checked_range: range.headers()['content-range'],
  });
  await captureUnobstructedPreviewFrame('encoded-frame', page, generated);
}

async function captureUnobstructedPreviewFrame(name, target, element) {
  // Only hide the browser's native control overlay for this paused technical
  // frame inspection. Playback checks above use the actual native controls.
  await element.evaluate((video) => {
    video.blur();
    video.controls = false;
  });
  try {
    await capture(name, target, element);
  } finally {
    await element.evaluate((video) => {
      video.controls = true;
    });
  }
}

async function verifyPreviewMobileFrames() {
  const mobileContext = await browser.newContext({
    viewport: { width: 375, height: 844 },
    isMobile: true,
    hasTouch: true,
    deviceScaleFactor: 1,
    storageState: seed.storage_state,
  });
  const mobile = await mobileContext.newPage();
  observe(mobile);
  const observations = [];
  for (const width of [375, 390]) {
    await mobile.setViewportSize({ width, height: 844 });
    await assertSavedUI(mobile);
    const generated = mobile.getByLabel('Prévia gerada', { exact: true });
    await generated.waitFor();
    await mobile.waitForFunction(
      () => document.querySelector('video[aria-label="Prévia gerada"]')?.readyState >= 1,
    );
    await generated.evaluate((video) => {
      video.currentTime = 2.5;
    });
    await mobile.waitForFunction(() => {
      const video = document.querySelector('video[aria-label="Prévia gerada"]');
      return (
        video?.readyState >= 2 &&
        !video.seeking &&
        !video.error &&
        video.getVideoPlaybackQuality().totalVideoFrames > 0 &&
        Math.abs(video.currentTime - 2.5) < 0.05
      );
    });
    const state = await generated.evaluate((video) => ({
      current_time: video.currentTime,
      width: video.videoWidth,
      height: video.videoHeight,
      decoded_frames: video.getVideoPlaybackQuality().totalVideoFrames,
    }));
    assert.ok(state.decoded_frames > 0);
    assert.deepEqual([state.width, state.height], [540, 960]);
    assert.equal(
      await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      true,
    );
    observations.push({ viewport_width: width, ...state });
    await capture(`persisted-preview-mobile-${width}`, mobile);
    if (width === 375)
      await captureUnobstructedPreviewFrame('mobile-encoded-frame', mobile, generated);
  }
  await mobileContext.close();
  passed(
    'persisted_preview_decodes_a_real_seek_frame_in_mobile_375_and_390_without_overflow',
    observations,
  );
}

async function previewChecks() {
  await verifySaved();
  assert.equal(await saveButton().isDisabled(), true);
  const generate = page.getByRole('button', { name: /^(Gerar prévia|Tentar nova prévia)$/ });
  await textInput(1).fill(`${overlayText} *`);
  assert.equal(await generate.isDisabled(), true);
  await page.getByText('Salve a edição antes de gerar uma nova prévia.', { exact: true }).waitFor();
  assert.equal(uiPreviews.length, 0);
  assert.equal(uiSaves.length, 0);
  await textInput(1).fill(overlayText);
  assert.equal(await generate.isEnabled(), true);
  passed('dirty_composition_blocks_preview_and_never_implicitly_saves');
  await page.getByLabel('Início da prévia (ms)', { exact: true }).fill('5000');
  await page.getByLabel('Duração da prévia (ms)', { exact: true }).fill('4000');
  const postResponse = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      response.url().endsWith(`/projects/${seed.project.id}/previews`),
  );
  await generate.click();
  const response = await postResponse;
  assert.ok([200, 202].includes(response.status()));
  seed.preview = await response.json();
  assert.equal(seed.preview.revision_id, seed.saved_revision.id);
  assert.equal(seed.preview.revision_number, seed.saved_revision.revision_number);
  assert.equal(seed.preview.preview_start_ms, 5000);
  assert.equal(seed.preview.duration_ms, 4000);
  assert.deepEqual(uiPreviews[0].body, {
    edit_spec_revision_id: seed.saved_revision.id,
    preview_start_ms: 5000,
    duration_ms: 4000,
  });
  assert.equal(uiSaves.length, 0);
  await persistSeed();
  passed('ui_preview_post_references_saved_immutable_revision_and_intermediate_absolute_window', {
    status: response.status(),
    job_id: seed.preview.id,
    state: seed.preview.status,
  });
  await page.getByLabel('Prévia gerada', { exact: true }).waitFor({ timeout: 260_000 });
  await page.getByText('Prévia concluída', { exact: true }).waitFor();
  seed.preview = await api(`/projects/${seed.project.id}/previews/${seed.preview.id}`);
  assert.equal(seed.preview.status, 'completed');
  assert.equal(seed.preview.artifact_available, true);
  assert.ok(seed.preview.checksum_sha256);
  assert.ok(seed.preview.byte_size > 0);
  await persistSeed();
  const pollCount = previewStatuses.length;
  await page.waitForTimeout(2_300);
  assert.equal(previewStatuses.length, pollCount, 'Polling stops after the completed state');
  assert.equal(await page.getByRole('progressbar').count(), 0);
  passed('worker_completion_is_observed_by_real_status_polling_without_fake_progress', {
    statuses: previewStatuses,
    sha256: seed.preview.checksum_sha256,
    bytes: seed.preview.byte_size,
  });
  await inspectPreviewPlayback();
  await textInput(1).fill(`${overlayText} *`);
  await page
    .getByText('As alterações locais ainda não estão nesta prévia.', { exact: true })
    .waitFor();
  assert.equal(await generate.isDisabled(), true);
  assert.equal(await page.getByLabel('Prévia gerada').getAttribute('src'), seed.preview.file_url);
  await textInput(1).fill(overlayText);
  assert.equal(uiPreviews.length, 1);
  assert.equal(uiSaves.length, 0);
  passed('completed_result_keeps_its_revision_when_the_local_draft_changes');
  if ((await page.locator('html').getAttribute('data-theme')) !== 'dark')
    await page.getByRole('button', { name: 'Ativar tema escuro', exact: true }).click();
  await capture('preview-desktop-dark');
  await page.getByRole('button', { name: 'Ativar tema claro', exact: true }).click();
  await capture('preview-desktop-light');
  seed.storage_state = await context.storageState();
  await persistSeed();
  await page.reload({ waitUntil: 'networkidle' });
  await page.getByLabel('Prévia gerada', { exact: true }).waitFor();
  await page
    .getByText(`Prévia da revisão ${seed.saved_revision.revision_number}`, { exact: true })
    .waitFor();
  assert.equal(await page.locator('html').getAttribute('data-theme'), 'light');
  assert.equal(uiPreviews.length, 1);
  passed('page_reload_restores_completed_preview_by_persisted_job_id_without_creating_a_new_job');
  const mobileContext = await browser.newContext({
    viewport: { width: 375, height: 844 },
    isMobile: true,
    hasTouch: true,
    deviceScaleFactor: 1,
    storageState: seed.storage_state,
  });
  const mobile = await mobileContext.newPage();
  observe(mobile);
  for (const width of [375, 390]) {
    await mobile.setViewportSize({ width, height: 844 });
    await assertSavedUI(mobile);
    await mobile.getByLabel('Prévia gerada', { exact: true }).waitFor();
    assert.equal(
      await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      true,
    );
    await capture(`preview-mobile-${width}`, mobile);
  }
  await mobileContext.close();
  passed('preview_result_and_saved_composition_recover_in_mobile_375_and_390_without_overflow');
}

try {
  const stages = { composition: compositionChecks, preview: previewChecks, verify: verifySaved };
  assert.ok(
    stages[stage],
    'This driver currently supports composition and persistence verification',
  );
  await page.goto(base, { waitUntil: 'networkidle' });
  const secureContext = await page.evaluate(() => ({
    origin: location.origin,
    is_secure_context: window.isSecureContext,
    random_uuid_available: typeof crypto.randomUUID === 'function',
  }));
  assert.equal(secureContext.is_secure_context, true);
  assert.equal(secureContext.random_uuid_available, true);
  passed('localhost_browser_context_supports_secure_random_uuid', secureContext);
  await prepareSeed();
  await stages[stage]();
  assert.deepEqual(pageErrors, []);
  passed('no_uncaught_browser_page_errors');
} catch (error) {
  failure = { message: String(error.message).replaceAll(base, '<local-app>') };
  process.exitCode = 1;
  console.error(failure.message);
  await capture('failure').catch(() => {});
} finally {
  const result = {
    status: failure ? 'failed' : 'passed',
    stage,
    run_id: runId,
    browser: browser.version(),
    project_id: seed?.project?.id ?? null,
    checks,
    page_errors: pageErrors,
    stream_responses: streamResponses,
    preview_file_responses: previewFileResponses,
    preview_statuses: previewStatuses,
    ui_preview_requests: uiPreviews,
    ui_save_requests: uiSaves,
    dialogs,
    screenshots,
    scope:
      stage === 'composition'
        ? 'R04 composition UI, native video and persisted contracts; no encoded-preview or manual listening claim'
        : 'R04 saved composition and real worker-encoded preview playback with native media and private Range; no manual listening claim',
    data_policy:
      'Synthetic QA project, revisions and uploaded originals remain active for later stages.',
    ...(failure ? { failure } : {}),
  };
  await writeFile(`${work}/artifacts/${runId}.json`, JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
  await browser.close();
}
