/** Read-only Chromium verification of the green symbol favicon and true alpha. */
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

const require = createRequire(import.meta.url);
if (!process.env.EDIVO_PLAYWRIGHT_MODULE) throw new Error('EDIVO_PLAYWRIGHT_MODULE is required');
const { chromium } = require(process.env.EDIVO_PLAYWRIGHT_MODULE);
const base = process.env.EDIVO_BROWSER_BASE_URL || 'http://127.0.0.1:5173';
const origin = new URL(base).origin;
if (!['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) throw new Error('Local QA only');
const index = process.argv.indexOf('--output');
const output = path.resolve(
  index < 0 ? 'evidence/transparent-favicon/browser' : process.argv[index + 1],
);
if (fs.existsSync(output)) throw new Error('Use a NEW evidence output directory');
fs.mkdirSync(output, { recursive: true });
const report = {
  status: 'failed',
  started_at: new Date().toISOString(),
  browser: null,
  real_application_document: true,
  read_only_get_head: true,
  browser_chrome_tab_visual_inspection_claimed: false,
  checks: [],
  assets: [],
  screenshots: [],
  page_errors: [],
  external_requests: [],
  blocked_mutations: [],
};
const check = (name, passed, detail) => {
  report.checks.push({ name, passed: !!passed, ...(detail === undefined ? {} : { detail }) });
  if (!passed) throw new Error(name);
};
const hash = (buffer) => crypto.createHash('sha256').update(buffer).digest('hex');
let browser;
let context;
try {
  browser = await chromium.launch({
    executablePath: process.env.EDIVO_CHROMIUM_EXECUTABLE,
    headless: true,
    args: ['--no-sandbox'],
  });
  report.browser = browser.version();
  context = await browser.newContext({ viewport: { width: 800, height: 420 } });
  await context.route('**/*', async (route) => {
    const request = route.request();
    if (!['GET', 'HEAD'].includes(request.method())) {
      report.blocked_mutations.push({ method: request.method(), url: request.url() });
      return route.abort('blockedbyclient');
    }
    if (/^https?:/.test(request.url()) && new URL(request.url()).origin !== origin) {
      report.external_requests.push(request.url());
      return route.abort('blockedbyclient');
    }
    return route.continue();
  });
  const page = await context.newPage();
  page.on('pageerror', (error) => report.page_errors.push(error.message));
  await page.goto(base, { waitUntil: 'networkidle' });
  const links = await page
    .locator('link[rel="icon"]')
    .evaluateAll((nodes) =>
      nodes.map((node) => ({ href: node.getAttribute('href'), sizes: node.getAttribute('sizes') })),
    );
  report.favicon_links = links;
  const pngs = new Map();
  for (const size of [16, 32, 48]) {
    const pathname = `/favicon-green-${size}x${size}.png`;
    const link = links.find((item) => item.href?.startsWith(`${pathname}?`));
    check(`${size}_fresh_versioned_html_link`, link?.href === `${pathname}?v=vis01-green`, link);
    const response = await context.request.get(`${base}${link.href}`, {
      headers: { 'Cache-Control': 'no-cache', Pragma: 'no-cache' },
    });
    check(`${size}_http_200`, response.status() === 200);
    const bytes = await response.body();
    check(`${size}_png_content_type`, /image\/png/.test(response.headers()['content-type'] || ''));
    check(
      `${size}_png_header_dimensions`,
      bytes.readUInt32BE(16) === size && bytes.readUInt32BE(20) === size,
    );
    pngs.set(size, bytes);
    const pixels = await page.evaluate(async (base64) => {
      const image = new Image();
      image.src = `data:image/png;base64,${base64}`;
      await image.decode();
      const canvas = document.createElement('canvas');
      canvas.width = image.naturalWidth;
      canvas.height = image.naturalHeight;
      const ctx = canvas.getContext('2d');
      ctx.drawImage(image, 0, 0);
      const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
      let transparent = 0;
      let opaque = 0;
      let nonGreenOpaque = 0;
      for (let i = 0; i < data.length; i += 4) {
        if (data[i + 3] === 0) transparent++;
        if (data[i + 3] > 200) {
          opaque++;
          if (!(data[i + 1] > data[i] && data[i + 1] > data[i + 2])) nonGreenOpaque++;
        }
      }
      const alpha = (x, y) => data[(y * canvas.width + x) * 4 + 3];
      return {
        width: canvas.width,
        height: canvas.height,
        corners_alpha: [
          alpha(0, 0),
          alpha(canvas.width - 1, 0),
          alpha(0, canvas.height - 1),
          alpha(canvas.width - 1, canvas.height - 1),
        ],
        transparent_fraction: transparent / (canvas.width * canvas.height),
        opaque_pixels: opaque,
        non_green_opaque_pixels: nonGreenOpaque,
      };
    }, bytes.toString('base64'));
    report.assets.push({ path: link.href, bytes: bytes.length, sha256: hash(bytes), ...pixels });
    check(
      `${size}_corners_alpha_zero`,
      pixels.corners_alpha.every((alpha) => alpha === 0),
    );
    check(
      `${size}_background_transparent`,
      pixels.transparent_fraction > 0.5,
      pixels.transparent_fraction,
    );
    check(
      `${size}_only_green_symbol_opaque`,
      pixels.opaque_pixels > 8 && pixels.non_green_opaque_pixels === 0,
      pixels,
    );
  }
  const icoLink = links.find((item) => item.href?.startsWith('/favicon-green.ico?'));
  check('ico_fresh_versioned_html_link', icoLink?.href === '/favicon-green.ico?v=vis01-green');
  const icoResponse = await context.request.get(`${base}${icoLink.href}`, {
    headers: { 'Cache-Control': 'no-cache', Pragma: 'no-cache' },
  });
  check('ico_http_200', icoResponse.status() === 200);
  const ico = await icoResponse.body();
  check(
    'ico_header_three_frames',
    ico.readUInt16LE(0) === 0 && ico.readUInt16LE(2) === 1 && ico.readUInt16LE(4) === 3,
  );
  const frames = [];
  for (let frame = 0; frame < 3; frame++) {
    const p = 6 + frame * 16;
    const width = ico[p] || 256;
    const height = ico[p + 1] || 256;
    const length = ico.readUInt32LE(p + 8);
    const offset = ico.readUInt32LE(p + 12);
    const png = ico.subarray(offset, offset + length);
    const matches = width === height && pngs.has(width) && png.equals(pngs.get(width));
    frames.push({ width, height, bytes: length, sha256: hash(png) });
    check(`ico_frame_${width}_matches_alpha_png`, matches);
  }
  report.ico = { path: icoLink.href, bytes: ico.length, sha256: hash(ico), frames };

  // A diagnostic contact sheet renders the real assets on light/dark surfaces.
  // This intentionally replaces only this test page's DOM, never app code/data.
  const rows = ['#f7f9fc', '#0b0f14'].map((background) => {
    const color = background === '#0b0f14' ? '#f5f7fa' : '#0f1720';
    return `<section style="background:${background};color:${color};padding:24px;border-radius:12px;margin-top:12px"><strong>${background === '#0b0f14' ? 'Dark' : 'Light'} surface — actual native 16 / 32 / 48 px</strong><div style="display:flex;align-items:center;gap:32px;margin-top:20px">${[16, 32, 48].map((size) => `<img alt="Edivo ${size}px" src="/favicon-green-${size}x${size}.png?v=vis01-green" width="${size}" height="${size}">`).join('')}</div></section>`;
  });
  await page.evaluate((rows) => {
    document.body.innerHTML = `<main style="box-sizing:border-box;padding:16px;width:800px;font:14px sans-serif;color:#0f1720;background:#dce3ea"><h1 style="font-size:20px;margin:0">Edivo — transparent green favicon</h1><p style="margin:6px 0 0">Diagnostic sheet; not browser-tab chrome.</p>${rows.join('')}</main>`;
  }, rows);
  await page.waitForFunction(() =>
    Array.from(document.images).every((img) => img.complete && img.naturalWidth > 0),
  );
  const file = 'native-favicon-light-dark.png';
  await page.screenshot({ path: path.join(output, file), animations: 'disabled' });
  report.screenshots.push({ path: file, sha256: hash(fs.readFileSync(path.join(output, file))) });
  check('no_mutating_http_attempts', report.blocked_mutations.length === 0);
  check('no_external_requests', report.external_requests.length === 0);
  check('no_uncaught_page_errors', report.page_errors.length === 0);
  report.status = 'passed';
} catch (error) {
  report.failure = error.message;
  process.exitCode = 1;
} finally {
  await context?.close();
  await browser?.close();
  report.finished_at = new Date().toISOString();
  fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2) + '\n', {
    flag: 'wx',
  });
  console.log(
    JSON.stringify({
      status: report.status,
      checks: report.checks.length,
      screenshots: report.screenshots.length,
      output,
    }),
  );
}
