/** Read-only live Chromium checks for the transparent, theme-aware header logo. */
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

const require = createRequire(import.meta.url);
const modulePath = process.env.EDIVO_PLAYWRIGHT_MODULE;
if (!modulePath) throw new Error('EDIVO_PLAYWRIGHT_MODULE is required');
const { chromium } = require(modulePath);
const base = process.env.EDIVO_BROWSER_BASE_URL || 'http://127.0.0.1:5173';
const origin = new URL(base).origin;
if (!['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) throw new Error('Local QA only');
const index = process.argv.indexOf('--output');
const output = path.resolve(
  index < 0 ? 'evidence/transparent-brand/browser' : process.argv[index + 1],
);
if (fs.existsSync(output)) throw new Error('Use a NEW evidence output directory');
fs.mkdirSync(output, { recursive: true });
const report = {
  status: 'failed',
  started_at: new Date().toISOString(),
  real_api: true,
  read_only_http: true,
  browser: null,
  checks: [],
  screenshots: [],
  observations: [],
  page_errors: [],
  blocked_mutations: [],
  external_requests: [],
};
const check = (name, passed, detail) => {
  report.checks.push({ name, passed: !!passed, ...(detail === undefined ? {} : { detail }) });
  if (!passed) throw new Error(name);
};
let browser;
try {
  browser = await chromium.launch({
    executablePath: process.env.EDIVO_CHROMIUM_EXECUTABLE,
    headless: true,
    args: ['--no-sandbox'],
  });
  report.browser = browser.version();
  for (const width of [1366, 375, 390]) {
    for (const theme of ['dark', 'light']) {
      const key = `${width}-${theme}`;
      const context = await browser.newContext({
        viewport: { width, height: 900 },
        colorScheme: theme,
        reducedMotion: 'reduce',
      });
      try {
        await context.addInitScript((theme) => localStorage.setItem('edivo-theme', theme), theme);
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
        await page.goto(`${base}/#/projects`, { waitUntil: 'networkidle' });
        await page.waitForFunction(() => {
          const img = document.querySelector('.brand-logo');
          return img?.complete && img.naturalWidth > 0;
        });
        const metrics = await page.locator('.brand').evaluate((brand) => {
          const img = brand.querySelector('img');
          const cs = getComputedStyle(brand);
          const r = img.getBoundingClientRect();
          return {
            theme: document.documentElement.dataset.theme,
            source: img.getAttribute('src'),
            natural_width: img.naturalWidth,
            natural_height: img.naturalHeight,
            width: r.width,
            height: r.height,
            background: cs.backgroundColor,
            border: [
              cs.borderTopWidth,
              cs.borderRightWidth,
              cs.borderBottomWidth,
              cs.borderLeftWidth,
            ],
            overflow_width: document.documentElement.scrollWidth,
            viewport_width: innerWidth,
            href: brand.getAttribute('href'),
            accessible_name: brand.getAttribute('aria-label'),
          };
        });
        report.observations.push({ key, ...metrics });
        check(
          `${key}_correct_theme_asset`,
          metrics.source === `/brand/edivo-logo-${theme}-transparent.png`,
        );
        check(`${key}_image_loaded`, metrics.natural_width > 0 && metrics.natural_height > 0);
        const pixels = await page.locator('.brand-logo').evaluate((img) => {
          const canvas = document.createElement('canvas');
          canvas.width = img.naturalWidth;
          canvas.height = img.naturalHeight;
          const ctx = canvas.getContext('2d');
          ctx.drawImage(img, 0, 0);
          const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
          let transparent = 0;
          for (let i = 3; i < data.length; i += 4) if (data[i] === 0) transparent++;
          const pixel = (x, y) => Array.from(ctx.getImageData(x, y, 1, 1).data);
          let wordmark;
          for (let y = 0; y < canvas.height && !wordmark; y++) {
            for (let x = 330; x < 880; x++) {
              const i = (y * canvas.width + x) * 4;
              if (data[i + 3] === 255) {
                wordmark = Array.from(data.slice(i, i + 4));
                break;
              }
            }
          }
          return {
            corners: [pixel(0, 0), pixel(959, 0), pixel(0, 325), pixel(959, 325)],
            transparent_fraction: transparent / (data.length / 4),
            wordmark,
            green_symbol: pixel(50, 150),
          };
        });
        check(
          `${key}_png_background_is_alpha`,
          pixels.corners.every((p) => p[3] === 0) && pixels.transparent_fraction > 0.7,
          pixels,
        );
        check(
          `${key}_wordmark_color_matches_theme`,
          pixels.wordmark[3] === 255 &&
            (theme === 'light'
              ? pixels.wordmark.slice(0, 3).join(',') === '15,23,32'
              : pixels.wordmark.slice(0, 3).every((channel) => channel >= 220)),
          pixels.wordmark,
        );
        check(`${key}_no_brand_background`, metrics.background === 'rgba(0, 0, 0, 0)');
        check(
          `${key}_no_brand_border`,
          metrics.border.every((value) => value === '0px'),
        );
        check(
          `${key}_proportions_preserved`,
          Math.abs(
            metrics.width / metrics.height - metrics.natural_width / metrics.natural_height,
          ) < 0.02,
        );
        check(
          `${key}_no_horizontal_overflow`,
          metrics.overflow_width <= metrics.viewport_width + 1,
        );
        check(
          `${key}_institutional_link_preserved`,
          metrics.href === '#main-content' && metrics.accessible_name === 'Edivo — início',
        );
        await page.evaluate(() => {
          scrollTo(0, 0);
          if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
        });
        const file = `${key}-header.png`;
        await page.screenshot({
          path: path.join(output, file),
          clip: { x: 0, y: 0, width, height: 130 },
          animations: 'disabled',
        });
        report.screenshots.push({
          path: file,
          sha256: crypto
            .createHash('sha256')
            .update(fs.readFileSync(path.join(output, file)))
            .digest('hex'),
        });
        const nextTheme = theme === 'dark' ? 'light' : 'dark';
        await page
          .getByRole('button', {
            name: nextTheme === 'light' ? 'Ativar tema claro' : 'Ativar tema escuro',
            exact: true,
          })
          .click();
        await page.waitForFunction((nextTheme) => {
          const img = document.querySelector('.brand-logo');
          return (
            document.documentElement.dataset.theme === nextTheme &&
            img.getAttribute('src') === `/brand/edivo-logo-${nextTheme}-transparent.png` &&
            img.complete &&
            img.naturalWidth > 0
          );
        }, nextTheme);
        check(
          `${key}_toggle_updates_asset_and_persists`,
          await page.evaluate(
            (nextTheme) => localStorage.getItem('edivo-theme') === nextTheme,
            nextTheme,
          ),
        );
      } finally {
        await context.close();
      }
    }
  }
  check('no_mutating_http_attempts', report.blocked_mutations.length === 0);
  check('no_external_requests', report.external_requests.length === 0);
  check('no_uncaught_page_errors', report.page_errors.length === 0);
  report.status = 'passed';
} catch (error) {
  report.failure = error.message;
  process.exitCode = 1;
} finally {
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
