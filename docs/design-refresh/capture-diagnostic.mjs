/** Isolated screenshot diagnostic: no application service or media access. */
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.EDIVO_PLAYWRIGHT_MODULE);
const output = 'evidence/capture-diagnostic';
fs.mkdirSync(output);
const css = fs.readFileSync('/frontend/src/app/styles.css', 'utf8');
const skipStyles = css.match(/\.skip-link \{[\s\S]*?\n\}/)[0];
const skipFocus = css.match(/\.skip-link:focus \{[\s\S]*?\n\}/)[0];
const browser = await chromium.launch({
  executablePath: process.env.EDIVO_CHROMIUM_EXECUTABLE,
  headless: true,
  args: ['--no-sandbox'],
});
const report = {
  fixture_only: true,
  application_services_used: false,
  actual_skip_link_css: skipStyles + '\n' + skipFocus,
  browser: browser.version(),
  observations: [],
};
try {
  const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
  await page.setContent(`<!doctype html><style>
    :root { --foreground:#f5f7fa; --background:#0b0f14;
      --space-3:12px; --radius-control:10px }
    body { background:#0b0f14; color:#f5f7fa; margin:0; font:14px/1.6 Arial }
    ${skipStyles} ${skipFocus}
    section { margin:3500px 86px 30px; height:2300px; padding:24px;
      border:1px solid #242c36; background:#11161d }
    h2 { margin:0 } p { margin-top:540px }
    </style><a class="skip-link" href="#main-content">Pular para o conteúdo</a>
    <section id="main-content"><h2>Isolated screenshot fixture</h2>
    <p>First reference line</p><p>Second reference line</p></section>`);
  const observe = async (label) => {
    const metrics = await page.evaluate(() => {
      const link = document.querySelector('.skip-link');
      const r = link.getBoundingClientRect();
      return {
        active_element: document.activeElement.tagName,
        focused: link.matches(':focus'),
        focus_visible: link.matches(':focus-visible'),
        transform: getComputedStyle(link).transform,
        rect: { x: r.x, y: r.y, width: r.width, height: r.height, bottom: r.bottom },
        scroll_y: scrollY,
        inner_height: innerHeight,
      };
    });
    report.observations.push({ label, ...metrics });
  };
  await observe('initial');
  await page.screenshot({ path: path.join(output, 'full-page.png'), fullPage: true });
  await observe('after_full_page');
  await page.locator('section').screenshot({
    path: path.join(output, 'locator-section.png'),
    animations: 'disabled',
  });
  await observe('after_locator');
  await page.screenshot({ path: path.join(output, 'viewport-after-locator.png') });
  await page.locator('section').scrollIntoViewIfNeeded();
  await observe('after_scroll');
  await page.screenshot({ path: path.join(output, 'viewport-after-scroll.png') });
  await page.locator('.skip-link').focus();
  await observe('explicit_keyboard_focus');
  await page.screenshot({ path: path.join(output, 'focus-visible.png') });
  report.hidden_during_ordinary_viewport = report.observations
    .filter((r) => r.label !== 'explicit_keyboard_focus')
    .every((r) => !r.focused && r.rect.bottom <= 0);
  report.focused_link_visible =
    report.observations.at(-1).focused && report.observations.at(-1).rect.y === 12;
} finally {
  await browser.close();
  fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2) + '\n', {
    flag: 'wx',
  });
}
console.log(JSON.stringify(report));
