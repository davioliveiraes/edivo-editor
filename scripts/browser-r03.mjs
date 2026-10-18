/** Real API, real MP4 and native media events. No browser routes are mocked. */
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';

const { chromium } = await import(process.env.EDIVO_PLAYWRIGHT_MODULE ?? 'playwright');
const base = process.env.EDIVO_BROWSER_BASE_URL;
const runId = process.env.EDIVO_BROWSER_RUN_ID ?? `r03-browser-${Date.now()}`;
const stage = process.env.EDIVO_BROWSER_STAGE ?? 'player';
assert.ok(base, 'EDIVO_BROWSER_BASE_URL is required');
const work = '/work';
const checks = [];
const pageErrors = [];
const streamResponses = [];
const screenshots = [];
const dialogs = [];
const dialogDecisions = [];
const passed = (check, detail) => {
  const item = { check, status: 'passed', ...(detail === undefined ? {} : { detail }) };
  checks.push(item);
  console.log(JSON.stringify(item));
};
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1440, height: 1100 },
  colorScheme: 'dark',
});
context.setDefaultTimeout(15_000);
const page = await context.newPage();
page.on('dialog', async (dialog) => {
  const accept = dialogDecisions.shift() ?? true;
  dialogs.push({ type: dialog.type(), message: dialog.message(), accepted: accept });
  if (accept) await dialog.accept();
  else await dialog.dismiss();
});
await page.addInitScript(() => {
  document.addEventListener(
    'loadedmetadata',
    (event) => {
      if (event.target instanceof HTMLVideoElement) {
        event.target.dataset.qaEvents = `${event.target.dataset.qaEvents ?? ''},loadedmetadata`;
      }
    },
    true,
  );
});
page.on('pageerror', (error) => pageErrors.push(error.message));
page.on('response', (response) => {
  if (!response.url().includes('/stream')) return;
  streamResponses.push({
    path: new URL(response.url()).pathname,
    status: response.status(),
    range: response.request().headers().range ?? null,
    content_range: response.headers()['content-range'] ?? null,
    content_length: response.headers()['content-length'] ?? null,
  });
});
const request = context.request;
const video = () => page.getByLabel('Vídeo do projeto', { exact: true });
const editorPath = (id) => `/#/projects/${id}/editor`;
let seed;
let failure;

async function api(path, options = {}) {
  const response = await request.fetch(`${base}/api/v1${path}`, options);
  const body = await response.json().catch(() => null);
  assert.ok(response.ok(), `API ${path}: HTTP ${response.status()} ${JSON.stringify(body)}`);
  return body;
}

async function persistSeed() {
  await writeFile(`${work}/seed.json`, JSON.stringify(seed, null, 2));
}

async function prepareSeed() {
  seed = await readFile(`${work}/seed.json`, 'utf8')
    .then(JSON.parse)
    .catch(() => null);
  if (seed) {
    const existing = await request.get(`${base}/api/v1/projects/${seed.project.id}`);
    if (existing.status() === 404 && stage !== 'verify') seed = null;
    else assert.equal(existing.status(), 200, 'The retained QA seed project must remain active');
  }
  if (!seed) {
    assert.notEqual(stage, 'verify', 'Verify requires the seed saved by the editor stage');
    const project = await api('/projects', {
      method: 'POST',
      data: {
        name: `QA editor ${runId}`,
        purpose: 'Amostras sintéticas R03',
        target_preset: '16:9',
      },
    });
    seed = { project, assets: {}, created_by: runId };
    await persistSeed();
  }
  if (stage === 'verify') return;
  const filenames =
    stage === 'player'
      ? ['horizontal.mp4', 'silent_vertical.mp4']
      : [
          'horizontal.mp4',
          'silent_vertical.mp4',
          'square.mp4',
          'rotated.mp4',
          'sar.mp4',
          'vfr.mp4',
          'unsupported.mp4',
        ];
  for (const filename of filenames) {
    if (seed.assets[filename]) continue;
    const buffer = await readFile(`${work}/fixtures/${filename}`);
    seed.assets[filename] = await api(`/projects/${seed.project.id}/media`, {
      method: 'POST',
      multipart: { file: { name: filename, mimeType: 'video/mp4', buffer } },
    });
    await persistSeed();
  }
  passed('synthetic_project_and_r02_imports_real', {
    project_id: seed.project.id,
    assets: Object.values(seed.assets).map((asset) => ({
      id: asset.id,
      filename: asset.original_filename,
      duration_ms: asset.duration_ms,
      has_audio: asset.has_audio,
      codec: asset.video_codec,
    })),
  });
}

async function mediaState(target = video()) {
  return target.evaluate((element) => ({
    paused: element.paused,
    current_time: element.currentTime,
    duration: element.duration,
    ready_state: element.readyState,
    width: element.videoWidth,
    height: element.videoHeight,
    error: element.error?.code ?? null,
    src: element.getAttribute('src'),
    preload: element.preload,
    native_events: element.dataset.qaEvents?.split(',') ?? [],
    decoded_frames: element.getVideoPlaybackQuality?.().totalVideoFrames ?? null,
    audio_decoded_bytes: element.webkitAudioDecodedByteCount ?? null,
  }));
}

async function observeNativeMedia() {
  await video().evaluate((element) => {
    if (element.dataset.qaObserved) return;
    element.dataset.qaObserved = 'true';
    for (const event of [
      'loadedmetadata',
      'playing',
      'pause',
      'timeupdate',
      'seeking',
      'seeked',
      'error',
    ]) {
      element.addEventListener(event, () => {
        element.dataset.qaEvents = `${element.dataset.qaEvents ?? ''},${event}`;
      });
    }
  });
}

async function waitLoaded(asset) {
  await video().waitFor();
  await page.waitForFunction((id) => {
    const element = document.querySelector('video');
    return (
      element?.getAttribute('src')?.includes(id) && element.readyState >= 1 && element.duration > 0
    );
  }, asset.id);
  const state = await mediaState();
  assert.ok(state.src.startsWith('/api/v1/projects/'));
  assert.ok(state.src.endsWith(`/media/${asset.id}/stream`));
  assert.equal(state.preload, 'metadata');
  assert.ok(Math.abs(state.duration * 1000 - asset.duration_ms) < 150);
  await observeNativeMedia();
  return state;
}

async function selectSource(filename) {
  const asset = seed.assets[filename];
  await page.getByLabel('Vídeo de origem', { exact: true }).selectOption(asset.id);
  return waitLoaded(asset);
}

async function realPlayPause() {
  const before = await mediaState();
  await page.getByRole('button', { name: 'Reproduzir vídeo', exact: true }).click();
  await page.waitForFunction((previous) => {
    const element = document.querySelector('video');
    return element && !element.paused && element.currentTime > previous + 0.12;
  }, before.current_time);
  await page.getByRole('button', { name: 'Pausar vídeo', exact: true }).click();
  await page.waitForFunction(() => {
    const element = document.querySelector('video');
    return element?.paused && element.dataset.qaEvents?.split(',').includes('pause');
  });
  const after = await mediaState();
  assert.equal(after.paused, true);
  assert.ok(after.native_events.includes('playing'));
  assert.ok(after.native_events.includes('pause'));
  assert.ok(after.native_events.includes('timeupdate'));
  assert.ok(after.decoded_frames > 0, 'Playback must decode actual video frames');
  return {
    advanced_seconds: after.current_time - before.current_time,
    decoded_frames: after.decoded_frames,
    audio_decoded_bytes: after.audio_decoded_bytes,
    native_events: after.native_events,
  };
}

async function clickSeek(fraction = 0.55) {
  const seek = page.getByRole('slider', { name: 'Buscar no vídeo', exact: true });
  const box = await seek.boundingBox();
  assert.ok(box);
  await seek.click({ position: { x: box.width * fraction, y: box.height / 2 } });
  await page.waitForFunction(() => {
    const element = document.querySelector('video');
    return element && !element.seeking && element.currentTime > 0.5;
  });
  const state = await mediaState();
  assert.ok(state.native_events.includes('seeked'));
  assert.ok(Math.abs(state.current_time / state.duration - fraction) < 0.12);
  return state;
}

async function capture(name, target = page) {
  await target.evaluate(() => {
    document.activeElement?.blur();
    window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
  });
  await target.evaluate(() => document.fonts.ready);
  const pathname = `${work}/artifacts/${runId}-${name}.png`;
  await target.screenshot({ path: pathname, fullPage: true, animations: 'disabled' });
  const filename = pathname.split('/').pop();
  screenshots.push(filename);
  return filename;
}

async function eventually(check, message, timeout = 10_000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (await check()) return;
    await page.waitForTimeout(50);
  }
  assert.fail(message);
}

const startInput = (target = page) => target.getByLabel('Entrada (ms)', { exact: true });
const endInput = (target = page) => target.getByLabel('Saída (ms)', { exact: true });
const saveButton = (target = page) =>
  target.getByRole('button', { name: 'Salvar edição', exact: true });

async function setRange(locator, value) {
  const bounds = await locator.evaluate((element) => ({
    min: Number(element.min),
    max: Number(element.max),
    step: Number(element.step),
  }));
  const steps = Math.round((value - bounds.min) / bounds.step);
  if (steps <= 100) {
    await locator.focus();
    await locator.press('Home');
    for (let count = 0; count < steps; count += 1) await locator.press('ArrowRight');
  } else {
    const box = await locator.boundingBox();
    assert.ok(box);
    await locator.click({
      position: {
        x: 8 + (box.width - 16) * ((value - bounds.min) / (bounds.max - bounds.min)),
        y: box.height / 2,
      },
    });
  }
}

async function openEditor(target = page) {
  await target.goto(`${base}${editorPath(seed.project.id)}`, { waitUntil: 'networkidle' });
  await target.getByRole('heading', { name: 'Editor do projeto.', exact: true }).waitFor();
  await target.getByLabel('Vídeo de origem', { exact: true }).waitFor();
}

async function saveFromUI(target = page) {
  const responsePromise = target.waitForResponse(
    (response) =>
      response.url().endsWith(`/projects/${seed.project.id}/edit-spec`) &&
      response.request().method() === 'PUT',
  );
  await saveButton(target).click();
  const response = await responsePromise;
  const body = await response.json();
  if (response.ok()) {
    await target
      .getByText(`Edição salva · Revisão ${body.revision_number}`, { exact: true })
      .waitFor();
  }
  return { status: response.status(), body };
}

async function fixturePlaybackChecks() {
  const observations = [];
  for (const filename of [
    'horizontal.mp4',
    'silent_vertical.mp4',
    'square.mp4',
    'rotated.mp4',
    'sar.mp4',
    'vfr.mp4',
  ]) {
    const state = await selectSource(filename);
    const playback = await realPlayPause();
    if (filename === 'horizontal.mp4') assert.ok(playback.audio_decoded_bytes > 0);
    if (filename === 'silent_vertical.mp4') assert.equal(playback.audio_decoded_bytes, 0);
    if (filename === 'square.mp4') assert.equal(state.width, state.height);
    if (filename === 'rotated.mp4')
      assert.ok(state.height > state.width, 'Display matrix must be honored by this browser');
    if (filename === 'sar.mp4')
      assert.ok(
        Math.abs(state.width / state.height - 8 / 3) < 0.02,
        'Browser display dimensions must honor SAR',
      );
    observations.push({ filename, native_metadata: state, playback });
  }
  passed('real_playback_audio_silent_horizontal_square_rotated_sar_vfr', observations);

  await page
    .getByLabel('Vídeo de origem', { exact: true })
    .selectOption(seed.assets['unsupported.mp4'].id);
  await page.getByRole('alert').filter({ hasText: 'codec' }).waitFor();
  const unsupported = await mediaState();
  assert.ok([3, 4].includes(unsupported.error));
  assert.equal(
    await page.getByRole('button', { name: 'Reproduzir vídeo', exact: true }).isDisabled(),
    true,
  );
  passed('real_mpeg4_part2_codec_failure_is_explained_without_transcoding', unsupported);
  await capture('unsupported-codec');

  await selectSource('horizontal.mp4');
  // A fresh browser context prevents Chromium's in-memory media reuse from
  // satisfying an offline request with bytes already decoded in this context.
  const offlineContext = await browser.newContext();
  const offlinePage = await offlineContext.newPage();
  offlinePage.on('dialog', (dialog) => dialog.accept());
  offlinePage.on('pageerror', (error) => pageErrors.push(error.message));
  await openEditor(offlinePage);
  const initiallyLoaded = await offlinePage
    .getByLabel('Vídeo de origem', { exact: true })
    .inputValue();
  const offlineAsset = Object.values(seed.assets).find(
    (asset) => asset.id !== initiallyLoaded && asset.video_codec === 'h264',
  );
  assert.ok(offlineAsset);
  await offlineContext.setOffline(true);
  try {
    await offlinePage.getByLabel('Vídeo de origem', { exact: true }).selectOption(offlineAsset.id);
    await offlinePage
      .getByRole('alert')
      .filter({ hasText: /conexão|acessar o vídeo/ })
      .waitFor();
    passed(
      'real_browser_offline_media_failure_sanitized',
      await offlinePage.getByRole('alert').allTextContents(),
    );
  } finally {
    await offlineContext.setOffline(false);
  }
  await offlinePage.getByLabel('Vídeo de origem', { exact: true }).selectOption(initiallyLoaded);
  await offlinePage.getByLabel('Vídeo de origem', { exact: true }).selectOption(offlineAsset.id);
  await offlinePage.waitForFunction((id) => {
    const element = document.querySelector('video');
    return element?.src.includes(id) && element.readyState >= 1 && !element.error;
  }, offlineAsset.id);
  await offlineContext.close();
  passed('real_browser_media_recovers_after_network_restored');
}

async function timelineChecks() {
  const asset = seed.assets['horizontal.mp4'];
  await startInput().fill('400');
  await endInput().fill('3000');
  const marker = page.getByRole('slider', { name: 'Marcador de entrada', exact: true });
  await marker.focus();
  await marker.press('ArrowRight');
  assert.equal(await startInput().inputValue(), '500');
  await marker.press('Shift+ArrowRight');
  assert.equal(await startInput().inputValue(), '1500');
  await marker.press('Home');
  assert.equal(await startInput().inputValue(), '0');
  passed('timeline_marker_keyboard_arrows_shift_home_and_integer_ms');

  const track = page.locator('.timeline-track');
  await track.scrollIntoViewIfNeeded();
  const box = await track.boundingBox();
  const handle = await marker.boundingBox();
  assert.ok(box && handle);
  await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.2, box.y + box.height / 2, { steps: 8 });
  await page.mouse.up();
  const dragged = Number(await startInput().inputValue());
  assert.ok(Math.abs(dragged - asset.duration_ms * 0.2) < 35);
  assert.ok(Number.isInteger(dragged));
  passed('timeline_real_pointer_capture_drag_changes_trim', { start_ms: dragged });

  await track.click({ position: { x: box.width * 0.45, y: box.height * 0.7 } });
  await page.waitForFunction(
    () => Math.abs(document.querySelector('video').currentTime - 1.8) < 0.2,
  );
  assert.ok(
    Number(
      await page.getByRole('slider', { name: 'Posição na timeline', exact: true }).inputValue(),
    ) > 1500,
  );
  await page.getByRole('button', { name: 'Marcar entrada', exact: true }).click();
  const markIn = Number(await startInput().inputValue());
  assert.ok(markIn > 1500 && markIn < 2100);
  await endInput().fill(String(asset.duration_ms));
  await setRange(page.getByRole('slider', { name: 'Buscar no vídeo', exact: true }), 3300);
  await page.getByRole('button', { name: 'Marcar saída', exact: true }).click();
  assert.ok(Math.abs(Number(await endInput().inputValue()) - 3300) < 50);
  passed('timeline_click_seek_playhead_and_mark_in_out_from_native_time', {
    start_ms: markIn,
    end_ms: Number(await endInput().inputValue()),
  });

  await startInput().fill('500');
  await endInput().fill('1500');
  await page.getByRole('button', { name: 'Voltar à entrada', exact: true }).click();
  await page.getByRole('button', { name: 'Reproduzir vídeo', exact: true }).click();
  await page.waitForFunction(() => {
    const element = document.querySelector('video');
    return element.paused && Math.abs(element.currentTime - 1.5) < 0.08;
  });
  const stopped = await mediaState();
  assert.ok(stopped.decoded_frames > 0);
  passed('native_playback_stops_at_selected_out_point', stopped);

  await page.getByRole('button', { name: 'Voltar à entrada', exact: true }).click();
  await page.getByRole('button', { name: 'Reproduzir vídeo', exact: true }).click();
  await page.waitForFunction(() => !document.querySelector('video').paused);
  await setRange(page.getByRole('slider', { name: 'Buscar no vídeo', exact: true }), 0);
  await page.waitForFunction(() => {
    const element = document.querySelector('video');
    return element.currentTime >= 0.49 && element.currentTime <= 1.55;
  });
  await page
    .getByRole('button', { name: 'Pausar vídeo', exact: true })
    .click()
    .catch(() => {});
  passed('seek_before_in_point_while_playing_clamps_to_selected_region');
}

async function compositionChecks() {
  const ratios = { '9:16': 9 / 16, '1:1': 1, '4:5': 4 / 5, '16:9': 16 / 9 };
  const geometry = [];
  for (const [preset, ratio] of Object.entries(ratios)) {
    await page.getByRole('button', { name: preset, exact: true }).click();
    assert.equal(
      await page.getByRole('button', { name: preset, exact: true }).getAttribute('aria-pressed'),
      'true',
    );
    const size = await page.locator('.composition-frame').boundingBox();
    assert.ok(Math.abs(size.width / size.height - ratio) < 0.015);
    geometry.push({ preset, width: size.width, height: size.height });
  }
  passed('all_four_presets_apply_real_css_aspect_ratios', geometry);
  await page.getByRole('button', { name: '9:16', exact: true }).click();
  await page.getByRole('radio', { name: 'Preencher (cover)', exact: true }).check();
  const focalX = page.getByRole('slider', { name: 'Ponto focal horizontal', exact: true });
  const focalY = page.getByRole('slider', { name: 'Ponto focal vertical', exact: true });
  await setRange(focalX, 0.25);
  await setRange(focalY, 0.75);
  assert.deepEqual(
    await video().evaluate((element) => [element.style.objectFit, element.style.objectPosition]),
    ['cover', '25% 75%'],
  );
  await page.getByRole('radio', { name: 'Ajustar (contain)', exact: true }).check();
  assert.equal(await focalX.isDisabled(), true);
  assert.equal(await focalY.isDisabled(), true);
  assert.equal(await focalX.inputValue(), '0.25');
  assert.deepEqual(
    await video().evaluate((element) => [element.style.objectFit, element.style.objectPosition]),
    ['contain', '50% 50%'],
  );
  await capture('desktop-contain');
  await page.getByRole('radio', { name: 'Preencher (cover)', exact: true }).check();
  assert.equal(await focalX.inputValue(), '0.25');
  assert.deepEqual(
    await video().evaluate((element) => [element.style.objectFit, element.style.objectPosition]),
    ['cover', '25% 75%'],
  );
  passed('cover_contain_and_normalized_focal_are_visual_and_preserved');
}

async function savedStateChecks() {
  const saved = await saveFromUI();
  assert.ok([200, 201].includes(saved.status));
  assert.deepEqual(saved.body.trim, { start_ms: 500, end_ms: 1500 });
  assert.deepEqual(saved.body.canvas, {
    preset: '9:16',
    fit: 'cover',
    focal_x: 0.25,
    focal_y: 0.75,
  });
  assert.equal(saved.body.source_media_id, seed.assets['horizontal.mp4'].id);
  passed('explicit_ui_save_real_postgresql_revision', {
    http_status: saved.status,
    revision: saved.body,
  });
  assert.equal(await saveButton().isDisabled(), true);
  await setRange(page.getByRole('slider', { name: 'Buscar no vídeo', exact: true }), 900);
  assert.equal(await saveButton().isDisabled(), true, 'Playhead must not make the plan dirty');
  const afterSeek = await api(`/projects/${seed.project.id}/edit-spec`);
  assert.equal(afterSeek.revision_number, saved.body.revision_number);
  passed('playhead_changes_neither_dirty_state_nor_revision');
  await openEditor();
  await waitLoaded(seed.assets['horizontal.mp4']);
  assert.equal(await startInput().inputValue(), '500');
  assert.equal(await endInput().inputValue(), '1500');
  assert.equal(
    await page.getByRole('button', { name: '9:16', exact: true }).getAttribute('aria-pressed'),
    'true',
  );
  passed('browser_reload_recovers_saved_trim_canvas_source_and_revision');

  // Build a real same-document history entry whose Back destination is the
  // library, rather than relying on incidental history from earlier fixtures.
  await page.getByRole('link', { name: 'Voltar à biblioteca', exact: true }).click();
  await page.getByRole('link', { name: 'Abrir editor', exact: true }).click();
  await waitLoaded(seed.assets['horizontal.mp4']);
  await setRange(page.getByRole('slider', { name: 'Buscar no vídeo', exact: true }), 900);

  await endInput().fill('1700');
  await page.getByText('Alterações não salvas', { exact: true }).waitFor();
  dialogDecisions.push(false);
  await page
    .getByLabel('Vídeo de origem', { exact: true })
    .selectOption(seed.assets['silent_vertical.mp4'].id);
  assert.equal(
    await page.getByLabel('Vídeo de origem', { exact: true }).inputValue(),
    seed.assets['horizontal.mp4'].id,
  );
  assert.equal(await endInput().inputValue(), '1700');
  passed('cancel_source_change_preserves_unsaved_draft');

  await video().evaluate((element) => {
    element.dataset.qaEditorIdentity = 'retained-on-back';
  });
  const beforeBack = await mediaState();
  const dialogCount = dialogs.length;
  dialogDecisions.push(false);
  await page.goBack();
  await eventually(
    () => dialogs.length > dialogCount,
    'Native Back must request confirmation for the dirty editor',
  );
  assert.equal(dialogs.at(-1).type, 'confirm');
  assert.equal(dialogs.at(-1).accepted, false);
  assert.ok(page.url().endsWith(editorPath(seed.project.id)));
  assert.equal(await endInput().inputValue(), '1700');
  assert.equal(await video().getAttribute('data-qa-editor-identity'), 'retained-on-back');
  const afterBack = await mediaState();
  assert.ok(Math.abs(afterBack.current_time - beforeBack.current_time) < 0.15);
  passed('native_browser_back_cancel_preserves_url_component_draft_and_seek', {
    before_seconds: beforeBack.current_time,
    after_seconds: afterBack.current_time,
  });

  dialogDecisions.push(false);
  await page.getByRole('link', { name: 'Voltar à biblioteca', exact: true }).click();
  await eventually(
    () => page.url().endsWith(editorPath(seed.project.id)),
    'Canceled hash navigation must restore editor URL',
  );
  assert.equal(await endInput().inputValue(), '1700');
  passed('cancel_hash_navigation_preserves_editor_and_local_changes');

  dialogDecisions.push(false);
  await page.reload({ waitUntil: 'domcontentloaded', timeout: 5000 }).catch(() => {});
  assert.equal(await endInput().inputValue(), '1700');
  assert.ok(dialogs.some((dialog) => dialog.type === 'beforeunload' && !dialog.accepted));
  passed('native_beforeunload_cancel_keeps_unsaved_changes');

  const current = await api(`/projects/${seed.project.id}/edit-spec`);
  const external = await api(`/projects/${seed.project.id}/edit-spec`, {
    method: 'PUT',
    data: {
      expected_revision: current.revision_number,
      schema_version: 1,
      source_media_id: current.source_media_id,
      trim: current.trim,
      canvas: { ...current.canvas, preset: '1:1' },
    },
  });
  const conflict = await saveFromUI();
  assert.equal(conflict.status, 409);
  assert.equal(conflict.body.code, 'edit_spec_conflict');
  await page.getByRole('alert').filter({ hasText: 'Outra revisão' }).waitFor();
  assert.equal(await endInput().inputValue(), '1700');
  assert.equal(await saveButton().isDisabled(), true);
  assert.equal((await api(`/projects/${seed.project.id}/edit-spec`)).id, external.id);
  passed('real_409_conflict_retains_draft_and_never_overwrites_external_revision');
  dialogDecisions.push(false);
  await page.getByRole('button', { name: 'Recarregar revisão atual', exact: true }).click();
  assert.equal(await endInput().inputValue(), '1700');
  dialogDecisions.push(true);
  await page.getByRole('button', { name: 'Recarregar revisão atual', exact: true }).click();
  await page.getByText('Revisão atual recarregada.', { exact: true }).waitFor();
  assert.equal(await endInput().inputValue(), String(external.trim.end_ms));
  assert.equal(
    await page.getByRole('button', { name: '1:1', exact: true }).getAttribute('aria-pressed'),
    'true',
  );
  passed('conflict_reload_requires_explicit_discard_and_recovers_external_revision');

  await endInput().fill('1900');
  dialogDecisions.push(true);
  await page.getByRole('link', { name: 'Voltar à biblioteca', exact: true }).click();
  await page.getByRole('link', { name: 'Abrir editor', exact: true }).waitFor();
  await page.getByRole('link', { name: 'Abrir editor', exact: true }).click();
  await waitLoaded(seed.assets['horizontal.mp4']);
  assert.equal(await endInput().inputValue(), String(external.trim.end_ms));
  passed('confirmed_navigation_discards_only_local_draft_and_reopens_saved_revision');
  await page.getByRole('button', { name: '9:16', exact: true }).click();
  const finalSave = await saveFromUI();
  assert.equal(finalSave.status, 200);
  seed.saved_revision = finalSave.body;
  await persistSeed();
}

async function themeAndMobileChecks() {
  await capture('desktop-dark');
  await page.getByRole('button', { name: 'Ativar tema claro', exact: true }).click();
  assert.equal(await page.locator('html').getAttribute('data-theme'), 'light');
  await capture('desktop-light');
  await page.reload({ waitUntil: 'networkidle' });
  assert.equal(await page.locator('html').getAttribute('data-theme'), 'light');
  passed('editor_dark_light_theme_and_local_persistence');

  const mobileContext = await browser.newContext({
    viewport: { width: 375, height: 812 },
    isMobile: true,
    hasTouch: true,
    colorScheme: 'light',
    deviceScaleFactor: 1,
  });
  const mobile = await mobileContext.newPage();
  mobile.on('pageerror', (error) => pageErrors.push(error.message));
  mobile.on('dialog', (dialog) => dialog.accept());
  await mobile.addInitScript(() => {
    document.addEventListener(
      'pointerdown',
      (event) => {
        window.qaPointerType = event.pointerType;
      },
      true,
    );
  });
  await openEditor(mobile);
  await mobile.getByRole('slider', { name: 'Marcador de entrada', exact: true }).waitFor();
  await mobile.getByRole('button', { name: '9:16', exact: true }).tap();
  assert.equal(
    await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    true,
  );
  const timeline = mobile.locator('.timeline-track');
  await timeline.scrollIntoViewIfNeeded();
  const box = await timeline.boundingBox();
  const marker = await mobile
    .getByRole('slider', { name: 'Marcador de entrada', exact: true })
    .boundingBox();
  assert.ok(box && marker);
  const touch = await mobileContext.newCDPSession(mobile);
  await touch.send('Input.dispatchTouchEvent', {
    type: 'touchStart',
    touchPoints: [{ x: marker.x + marker.width / 2, y: marker.y + marker.height / 2 }],
  });
  for (const fraction of [0.18, 0.2, 0.23, 0.25]) {
    await touch.send('Input.dispatchTouchEvent', {
      type: 'touchMove',
      touchPoints: [{ x: box.x + box.width * fraction, y: box.y + box.height / 2 }],
    });
  }
  await touch.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  const start = Number(await startInput(mobile).inputValue());
  assert.equal(await mobile.evaluate(() => window.qaPointerType), 'touch');
  assert.ok(Math.abs(start - seed.assets['horizontal.mp4'].duration_ms * 0.25) < 50);
  passed('mobile_375_native_touch_marker_drag_and_no_horizontal_overflow', {
    start_ms: start,
    pointer_type: 'touch',
    viewport: '375x812',
  });
  await endInput(mobile).fill('2600');
  const saved = await saveFromUI(mobile);
  assert.equal(saved.status, 200);
  seed.saved_revision = saved.body;
  await persistSeed();
  await capture('mobile-375', mobile);
  await mobile.setViewportSize({ width: 812, height: 375 });
  assert.equal(
    await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    true,
  );
  await capture('mobile-landscape', mobile);
  passed('mobile_landscape_layout_has_no_horizontal_overflow');
  await mobileContext.close();
}

async function editorChecks() {
  const empty = await api('/projects', { method: 'POST', data: { name: `QA vazio ${runId}` } });
  try {
    await page.goto(`${base}${editorPath(empty.id)}`, { waitUntil: 'networkidle' });
    await page
      .getByRole('heading', { name: 'Este projeto ainda não tem vídeos.', exact: true })
      .waitFor();
    assert.equal(await page.locator('video').count(), 0);
    passed('empty_project_has_import_navigation_and_no_fictitious_player');
  } finally {
    await api(`/projects/${empty.id}`, { method: 'DELETE' });
  }
  await openEditor();
  await fixturePlaybackChecks();
  await timelineChecks();
  await compositionChecks();
  await savedStateChecks();
  await themeAndMobileChecks();
  assert.ok(streamResponses.some((response) => response.status === 206 && response.range));
  passed(
    'editor_native_video_requests_private_http_range_206',
    streamResponses.filter((response) => response.status === 206).length,
  );
  assert.equal(await page.locator('video').count(), 1);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  passed('single_track_editor_desktop_has_no_horizontal_overflow');
}

async function playerChecks() {
  await page.goto(`${base}/#/projects/${seed.project.id}`, { waitUntil: 'networkidle' });
  const link = page.getByRole('link', { name: 'Abrir editor', exact: true });
  assert.equal(await link.getAttribute('href'), `#/projects/${seed.project.id}/editor`);
  await link.click();
  await page.getByRole('heading', { name: 'Editor do projeto.', exact: true }).waitFor();
  assert.ok(page.url().endsWith(editorPath(seed.project.id)));
  passed('library_opens_editor_hash_route');
  const horizontal = seed.assets['horizontal.mp4'];
  await page.getByLabel('Vídeo de origem', { exact: true }).selectOption(horizontal.id);
  const horizontalState = await waitLoaded(horizontal);
  assert.ok(horizontal.has_audio);
  assert.ok(horizontalState.width > horizontalState.height);
  assert.equal(horizontalState.paused, true, 'No autoplay with audio');
  assert.ok(horizontalState.native_events.includes('loadedmetadata'));
  passed('native_h264_aac_metadata_and_no_autoplay', horizontalState);
  passed('real_play_pause_native_media_events', await realPlayPause());
  passed('real_seek_via_accessible_control', await clickSeek());

  const vertical = await selectSource('silent_vertical.mp4');
  assert.equal(seed.assets['silent_vertical.mp4'].has_audio, false);
  assert.ok(vertical.height > vertical.width);
  passed('silent_vertical_mp4_native_metadata', vertical);
  passed('silent_video_real_play_pause', await realPlayPause());
  await page.waitForFunction(() => document.querySelector('video')?.readyState >= 2);
  assert.ok(streamResponses.some((response) => response.status === 206 && response.range));
  assert.ok(streamResponses.every((response) => [200, 206].includes(response.status)));
  passed('native_video_http_range_206', streamResponses);
  assert.equal(await page.locator('video').count(), 1);
  assert.equal(await page.locator('audio').count(), 0);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  passed('single_native_player_and_desktop_no_overflow');
  await capture('desktop-dark');
}

async function verifySaved() {
  assert.ok(seed.saved_revision, 'Editor stage must save an expected revision before verify');
  const current = await api(`/projects/${seed.project.id}/edit-spec`);
  assert.deepEqual(current, seed.saved_revision);
  await page.goto(`${base}${editorPath(seed.project.id)}`, { waitUntil: 'networkidle' });
  await page.getByRole('heading', { name: 'Editor do projeto.', exact: true }).waitFor();
  await waitLoaded(
    Object.values(seed.assets).find((asset) => asset.id === current.source_media_id),
  );
  assert.equal(
    await page.getByLabel('Vídeo de origem', { exact: true }).inputValue(),
    current.source_media_id,
  );
  assert.equal(
    Number(await page.getByLabel('Entrada (ms)', { exact: true }).inputValue()),
    current.trim.start_ms,
  );
  assert.equal(
    Number(await page.getByLabel('Saída (ms)', { exact: true }).inputValue()),
    current.trim.end_ms,
  );
  assert.equal(
    await page
      .getByRole('button', { name: current.canvas.preset, exact: true })
      .getAttribute('aria-pressed'),
    'true',
  );
  assert.equal(
    await page
      .getByRole('radio', {
        name: current.canvas.fit === 'cover' ? 'Preencher (cover)' : 'Ajustar (contain)',
        exact: true,
      })
      .isChecked(),
    true,
  );
  assert.equal(
    Number(
      await page.getByRole('slider', { name: 'Ponto focal horizontal', exact: true }).inputValue(),
    ),
    current.canvas.focal_x,
  );
  assert.equal(
    Number(
      await page.getByRole('slider', { name: 'Ponto focal vertical', exact: true }).inputValue(),
    ),
    current.canvas.focal_y,
  );
  passed('saved_revision_and_editor_recovered_after_service_restart', {
    revision_number: current.revision_number,
    source_media_id: current.source_media_id,
    trim: current.trim,
    canvas: current.canvas,
  });
  for (const asset of Object.values(seed.assets)) {
    const reloaded = await api(`/projects/${seed.project.id}/media/${asset.id}`);
    assert.equal(reloaded.checksum_sha256, asset.checksum_sha256);
    assert.equal(reloaded.byte_size, asset.byte_size);
  }
  passed('r02_original_inventory_sha256_and_size_unchanged');
  passed('saved_trim_real_playback_after_service_restart', await realPlayPause());
  assert.ok(streamResponses.some((response) => response.status === 206 && response.range));
  passed('private_http_range_206_after_service_restart');
  await capture('persisted-editor');
}

try {
  await prepareSeed();
  if (stage === 'verify') await verifySaved();
  else if (stage === 'editor') await editorChecks();
  else await playerChecks();
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
    dialogs,
    screenshots,
    data_policy:
      'Synthetic QA seed remains active between stages; imported originals are preserved.',
    ...(failure ? { failure } : {}),
  };
  await writeFile(`${work}/artifacts/${runId}.json`, JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
  await browser.close();
}
