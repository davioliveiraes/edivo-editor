#!/usr/bin/env node
/* global Buffer, console, process */
/** Derive official raster assets with native Chromium Canvas, without network or AI.
 *
 * node frontend/scripts/brand-assets.mjs --docker
 * node frontend/scripts/brand-assets.mjs --chromium /path/to/chrome
 *
 * The Docker renderer mounts only a private temporary HTML input, read-only.
 * Existing files are accepted only when their bytes match the new derivation.
 */
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const publicRoot = join(root, 'frontend/public');
const evidenceRoot = join(root, 'docs/design-refresh/evidence');
const image = 'mcr.microsoft.com/playwright:v1.58.2-noble';
const args = process.argv.slice(2);
const docker = args.length === 1 && args[0] === '--docker';
const chrome = args.length === 2 && args[0] === '--chromium' ? resolve(args[1]) : null;
if (!docker && !chrome) {
  throw new Error('Use --docker or --chromium /absolute/path/to/chrome.');
}
const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');
const sourceFiles = ['design/source/logo-edivo.jpg', 'design/source/icone-edivo.jpg'];
const sourceBytes = sourceFiles.map((path) => readFileSync(join(root, path)));
const sourceHashes = sourceBytes.map(sha256);
process.on('uncaughtException', (error) => {
  mkdirSync(evidenceRoot, { recursive: true });
  const failurePath = join(evidenceRoot, `asset-generation-failure-${Date.now()}.json`);
  writeFileSync(
    failurePath,
    `${JSON.stringify(
      {
        status: 'failed',
        command: ['node', 'frontend/scripts/brand-assets.mjs', ...args],
        code: error.code || 'asset_generation_failed',
        message: error.message,
        stdout: String(error.stdout || ''),
        stderr: String(error.stderr || ''),
        application_services_modified: false,
      },
      null,
      2,
    )}\n`,
    { flag: 'wx' },
  );
  console.error(JSON.stringify({ status: 'failed', evidence: failurePath }));
  process.exit(1);
});

function exclusiveOrIdentical(path, bytes) {
  if (existsSync(path)) {
    if (!readFileSync(path).equals(bytes)) {
      throw new Error(`Existing derivative differs; review it before replacing: ${path}`);
    }
    return;
  }
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, bytes, { flag: 'wx' });
}

const html = `<!doctype html><html><head><meta charset="utf-8"></head><body>
<img id="logo" alt="" src="data:image/jpeg;base64,${sourceBytes[0].toString('base64')}">
<img id="icon" alt="" src="data:image/jpeg;base64,${sourceBytes[1].toString('base64')}">
<script>
addEventListener('load', () => {
  try {
    const source = (id) => {
      const img = document.getElementById(id);
      if (!img.complete || !img.naturalWidth) throw new Error('source_decode_failed');
      const canvas = document.createElement('canvas');
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      const ctx = canvas.getContext('2d', { willReadFrequently: true });
      ctx.drawImage(img, 0, 0);
      return { canvas, ctx };
    };
    const logo = source('logo');
    const w = logo.canvas.width, h = logo.canvas.height;
    const pixels = logo.ctx.getImageData(0, 0, w, h).data;
    let left = w, top = h, right = 0, bottom = 0;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      if (Math.max(pixels[i], pixels[i + 1], pixels[i + 2]) > 80) {
        left = Math.min(left, x); top = Math.min(top, y);
        right = Math.max(right, x); bottom = Math.max(bottom, y);
      }
    }
    if (left < 100 || top < 100 || right > w - 100 || bottom > h - 100) {
      throw new Error('unexpected_logo_bounds');
    }
    const clearspace = Math.ceil((bottom - top + 1) * 0.12);
    const crop = { x: left - clearspace, y: top - clearspace,
      width: right - left + 1 + clearspace * 2,
      height: bottom - top + 1 + clearspace * 2 };
    const target = document.createElement('canvas');
    target.width = 960;
    const scaledHeight = 960 * crop.height / crop.width;
    target.height = Math.ceil(scaledHeight);
    const tc = target.getContext('2d');
    tc.fillStyle = '#101010'; tc.fillRect(0, 0, target.width, target.height);
    tc.imageSmoothingEnabled = true; tc.imageSmoothingQuality = 'high';
    tc.drawImage(logo.canvas, crop.x, crop.y, crop.width, crop.height,
      0, 0, target.width, scaledHeight);
    const outputs = [
      { path: 'brand/edivo-logo.png', width: target.width, height: target.height,
        data: target.toDataURL('image/png') },
      { path: 'brand/edivo-logo.webp', width: target.width, height: target.height,
        data: target.toDataURL('image/webp', 0.92) }
    ];
    const icon = source('icon');
    const iw = icon.canvas.width, ih = icon.canvas.height;
    if (iw !== ih) throw new Error('icon_must_be_square');
    const data = icon.ctx.getImageData(0, 0, iw, ih);
    const queue = new Uint32Array(iw * ih);
    const visited = new Uint8Array(iw * ih);
    let head = 0, tail = 0, transparent = 0, partial = 0;
    const cornerBand = iw / 8;
    const eligible = (x, y) => {
      if (!((x < cornerBand || x >= iw - cornerBand) &&
            (y < cornerBand || y >= ih - cornerBand))) return false;
      const i = (y * iw + x) * 4;
      const min = Math.min(data.data[i], data.data[i + 1], data.data[i + 2]);
      const max = Math.max(data.data[i], data.data[i + 1], data.data[i + 2]);
      return min >= 32 && max - min <= 24;
    };
    const enqueue = (x, y) => {
      if (x < 0 || y < 0 || x >= iw || y >= ih) return;
      const p = y * iw + x;
      if (visited[p] || !eligible(x, y)) return;
      visited[p] = 1; queue[tail++] = p;
    };
    enqueue(0, 0); enqueue(iw - 1, 0);
    enqueue(0, ih - 1); enqueue(iw - 1, ih - 1);
    while (head < tail) {
      const p = queue[head++], x = p % iw, y = Math.floor(p / iw);
      enqueue(x - 1, y); enqueue(x + 1, y); enqueue(x, y - 1); enqueue(x, y + 1);
    }
    for (let p = 0; p < visited.length; p++) if (visited[p]) {
      const i = p * 4;
      const neutral = (data.data[i] + data.data[i + 1] + data.data[i + 2]) / 3;
      const alpha = neutral >= 244 ? 0 : Math.max(0,
        Math.min(255, Math.round((255 - neutral) / (255 - 16) * 255)));
      data.data[i] = data.data[i + 1] = data.data[i + 2] = 16;
      data.data[i + 3] = alpha;
      if (alpha === 0) transparent++; else if (alpha < 255) partial++;
    }
    if (!transparent || transparent > iw * ih * 0.03) throw new Error('unexpected_corner_mask');
    const original = icon.ctx.getImageData(0, 0, iw, ih).data;
    let greenPixelCount = 0;
    for (let i = 0; i < original.length; i += 4) {
      if (original[i + 1] > original[i] + 40 && original[i + 1] > original[i + 2] + 40) {
        greenPixelCount++;
        if (data.data[i] !== original[i] || data.data[i + 1] !== original[i + 1] ||
            data.data[i + 2] !== original[i + 2] || data.data[i + 3] !== 255) {
          throw new Error('symbol_pixel_modified');
        }
      }
    }
    icon.ctx.putImageData(data, 0, 0);
    for (const [path, size] of [
      ['favicon-16x16.png', 16], ['favicon-32x32.png', 32], ['favicon-48x48.png', 48],
      ['apple-touch-icon.png', 180], ['icon-192.png', 192], ['icon-512.png', 512],
      ['brand/edivo-icon.png', 512]
    ]) {
      const c = document.createElement('canvas'); c.width = c.height = size;
      const ctx = c.getContext('2d'); ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = 'high'; ctx.drawImage(icon.canvas, 0, 0, size, size);
      outputs.push({ path, width: size, height: size, data: c.toDataURL('image/png') });
    }
    const sheet = document.createElement('canvas'); sheet.width = 1080; sheet.height = 760;
    const sc = sheet.getContext('2d'); sc.fillStyle = '#0B0F14'; sc.fillRect(0, 0, 1080, 760);
    sc.fillStyle = '#F5F7FA'; sc.font = '22px sans-serif';
    sc.fillText('Edivo — official raster derivatives', 32, 38);
    sc.fillStyle = '#101010'; sc.fillRect(24, 64, 480, 180);
    sc.drawImage(target, 40, 76, 448, 448 * target.height / target.width);
    sc.fillStyle = '#F7F9FC'; sc.fillRect(540, 64, 516, 180);
    sc.fillStyle = '#101010'; sc.fillRect(558, 82, 480, 144);
    sc.drawImage(target, 574, 92, 448, 448 * target.height / target.width);
    sc.fillStyle = '#A9B3BF'; sc.font = '16px sans-serif';
    sc.fillText('Native16 / 32 / 48px', 32, 286);
    let sx = 32;
    for (const size of [16, 32, 48]) {
      sc.drawImage(icon.canvas, sx, 308, size, size); sx += size + 24;
    }
    sc.fillStyle = '#A9B3BF'; sc.fillText('Pixel inspection16px ×8 and32px ×4', 304, 286);
    const pixel = (size, x) => {
      const c = document.createElement('canvas'); c.width = c.height = size;
      const cx = c.getContext('2d'); cx.imageSmoothingQuality = 'high';
      cx.drawImage(icon.canvas, 0, 0, size, size);
      sc.imageSmoothingEnabled = false; sc.drawImage(c, x, 308, 128, 128);
      sc.imageSmoothingEnabled = true;
    };
    pixel(16, 304); pixel(32, 464);
    sc.fillStyle = '#A9B3BF'; sc.fillText('Transparent rounded corners on light / dark surfaces', 32, 486);
    sc.fillStyle = '#F7F9FC'; sc.fillRect(24, 510, 220, 220);
    sc.drawImage(icon.canvas, 38, 524, 192, 192);
    sc.drawImage(icon.canvas, 280, 524, 192, 192);
    sc.fillStyle = '#A9B3BF'; sc.fillText('Raster conversion; no vector reconstruction', 528, 560);
    const payload = { status: 'passed', browser: navigator.userAgent,
      logo: { source: [w, h], bright_bounds: { left, top, right, bottom },
        threshold: 80, clearspace_pixels: clearspace, crop,
        scale: 960 / crop.width, scaled_content_height: scaledHeight,
        width: target.width, height: target.height, webp_quality: 0.92,
        uniform_scaling: true, geometry_recreated: false },
      icon: { source: [iw, ih], corner_band_fraction: 0.125,
        neutral_minimum: 32, neutral_maximum_channel_difference: 24,
        white_matte_cutoff: 244, edge_matte_rgb: [16, 16, 16],
        connected_corner_pixels: tail, fully_transparent_pixels: transparent,
        partial_alpha_pixels: partial, original_green_pixels_preserved: greenPixelCount,
        symbol_recreated: false }, outputs, contact_sheet: sheet.toDataURL('image/png') };
    document.documentElement.innerHTML = '<head></head><body><pre id="brand-result"></pre></body>';
    document.getElementById('brand-result').textContent = JSON.stringify(payload);
  } catch (error) {
    document.documentElement.innerHTML = '<head></head><body><pre id="brand-result"></pre></body>';
    document.getElementById('brand-result').textContent = JSON.stringify({ status: 'failed', code: error.message });
  }
});
</script></body></html>`;

const browserFlags = [
  '--headless',
  '--no-sandbox',
  '--disable-gpu',
  '--disable-extensions',
  '--disable-background-networking',
  '--disable-component-update',
  '--disable-default-apps',
  '--disable-sync',
  '--no-first-run',
  '--timeout=15000',
  '--virtual-time-budget=2000',
  '--user-data-dir=/tmp/edivo-brand-chromium-profile',
  '--dump-dom',
];
const temporary = mkdtempSync(join(tmpdir(), 'edivo-brand-'));
let rendered;
try {
  writeFileSync(join(temporary, 'brand-source.html'), html, { flag: 'wx', mode: 0o600 });
  if (docker) {
    const launch = `const f=require('fs'),p=require('path'),c=require('child_process');
const folders=f.readdirSync('/ms-playwright');
const candidates=[...folders.filter(x=>x.startsWith('chromium_headless_shell-')).flatMap(x=>[
  p.join('/ms-playwright',x,'chrome-headless-shell-linux64/chrome-headless-shell'),
  p.join('/ms-playwright',x,'chrome-linux64/headless_shell'),p.join('/ms-playwright',x,'chrome-linux/headless_shell')]),
  ...folders.filter(x=>x.startsWith('chromium-')).flatMap(x=>[
  p.join('/ms-playwright',x,'chrome-linux64/chrome'),p.join('/ms-playwright',x,'chrome-linux/chrome')])];
const binary=candidates.find(x=>f.existsSync(x)); if(!binary) throw Error('chromium_not_found');
process.stdout.write(c.execFileSync(binary,${JSON.stringify([...browserFlags, 'file:///work/brand-source.html'])},
  {encoding:'utf8',timeout:60000,maxBuffer:8*1024*1024,stdio:['ignore','pipe','pipe']}));`;
    rendered = execFileSync(
      'docker',
      [
        'run',
        '--pull=never',
        '--rm',
        '--network',
        'none',
        '--user',
        `${process.getuid()}:${process.getgid()}`,
        '--cap-drop',
        'ALL',
        '--security-opt',
        'no-new-privileges:true',
        '--shm-size',
        '256m',
        '--cpus',
        '2',
        '--memory',
        '768m',
        '--pids-limit',
        '128',
        '--mount',
        `type=bind,source=${temporary},target=/work,readonly`,
        '--workdir',
        '/work',
        image,
        'node',
        '-e',
        launch,
      ],
      { encoding: 'utf8', timeout: 90000, maxBuffer: 8 * 1024 * 1024 },
    );
  } else {
    rendered = execFileSync(
      chrome,
      [
        ...browserFlags.filter((flag) => !flag.startsWith('--user-data-dir=')),
        `--user-data-dir=${join(temporary, 'profile')}`,
        `file://${temporary}/brand-source.html`,
      ],
      {
        encoding: 'utf8',
        timeout: 60000,
        maxBuffer: 8 * 1024 * 1024,
      },
    );
  }
} finally {
  // This is only the generator-created private scratch folder, never app storage.
  rmSync(temporary, { recursive: true });
}
const match = rendered.match(/<pre id="brand-result">([\s\S]*?)<\/pre>/);
if (!match) throw new Error('Renderer did not produce its controlled result.');
const payload = JSON.parse(match[1]);
if (payload.status !== 'passed') throw new Error(payload.code || 'Asset derivation failed.');
const manifest = {
  release: 'VIS-01',
  status: 'passed',
  generator: 'frontend/scripts/brand-assets.mjs',
  renderer: docker
    ? { mode: 'isolated_docker_canvas', image, network: 'none' }
    : { mode: 'local_canvas' },
  browser: payload.browser,
  fidelity:
    'Official JPEG pixels, cropped/resampled raster derivatives; no vector reconstruction or generative redraw.',
  sources: Object.fromEntries(
    sourceFiles.map((path, i) => [path, { bytes: sourceBytes[i].length, sha256: sourceHashes[i] }]),
  ),
  logo: payload.logo,
  icon: payload.icon,
  outputs: {},
};
const outputBytes = new Map();
for (const output of payload.outputs) {
  const buffer = Buffer.from(output.data.split(',')[1], 'base64');
  if (
    !output.data.startsWith(output.path.endsWith('.webp') ? 'data:image/webp;' : 'data:image/png;')
  ) {
    throw new Error('Renderer output type differs from requested asset.');
  }
  outputBytes.set(output.path, buffer);
  manifest.outputs[`frontend/public/${output.path}`] = {
    width: output.width,
    height: output.height,
    bytes: buffer.length,
    sha256: sha256(buffer),
  };
}
const frames = [16, 32, 48].map((size) => ({
  size,
  bytes: outputBytes.get(`favicon-${size}x${size}.png`),
}));
const header = Buffer.alloc(6 + 16 * frames.length);
header.writeUInt16LE(1, 2);
header.writeUInt16LE(frames.length, 4);
let offset = header.length;
frames.forEach(({ size, bytes }, i) => {
  const at = 6 + 16 * i;
  header[at] = header[at + 1] = size;
  header.writeUInt16LE(1, at + 4);
  header.writeUInt16LE(32, at + 6);
  header.writeUInt32LE(bytes.length, at + 8);
  header.writeUInt32LE(offset, at + 12);
  offset += bytes.length;
});
const ico = Buffer.concat([header, ...frames.map(({ bytes }) => bytes)]);
outputBytes.set('favicon.ico', ico);
manifest.outputs['frontend/public/favicon.ico'] = {
  sizes: frames.map(({ size }) => [size, size]),
  encoding: 'ICO with 32-bit PNG frames',
  bytes: ico.length,
  sha256: sha256(ico),
};
for (const [path, bytes] of outputBytes) exclusiveOrIdentical(join(publicRoot, path), bytes);
const contact = Buffer.from(payload.contact_sheet.split(',')[1], 'base64');
exclusiveOrIdentical(join(evidenceRoot, 'assets-contact-sheet.png'), contact);
manifest.contact_sheet = {
  path: 'docs/design-refresh/evidence/assets-contact-sheet.png',
  width: 1080,
  height: 760,
  bytes: contact.length,
  sha256: sha256(contact),
};
sourceFiles.forEach((path, i) => {
  if (sha256(readFileSync(join(root, path))) !== sourceHashes[i])
    throw new Error('Original source changed.');
});
exclusiveOrIdentical(
  join(evidenceRoot, 'asset-manifest.json'),
  Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`),
);
console.log(
  JSON.stringify({
    status: 'passed',
    assets: outputBytes.size,
    logo: [payload.logo.width, payload.logo.height],
    manifest: 'docs/design-refresh/evidence/asset-manifest.json',
  }),
);
