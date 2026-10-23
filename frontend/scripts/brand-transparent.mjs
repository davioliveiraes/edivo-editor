#!/usr/bin/env node
/* global Buffer, console, process */
/** Deterministic variants of the existing raster logo; never redraw the wordmark. */
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const source = readFileSync(join(root, 'frontend/public/brand/edivo-logo.png'));
const evidence = join(root, 'docs/design-refresh/evidence/transparent-brand');
const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');
const html = `<!doctype html><meta charset="utf-8"><img id="source" src="data:image/png;base64,${source.toString('base64')}"><script>
addEventListener('load', () => {
  const img = document.getElementById('source');
  const canvas = document.createElement('canvas');
  canvas.width = img.naturalWidth; canvas.height = img.naturalHeight;
  const ctx = canvas.getContext('2d', {willReadFrequently:true});
  ctx.drawImage(img,0,0);
  const original = ctx.getImageData(0,0,canvas.width,canvas.height);
  const out = ctx.createImageData(canvas.width,canvas.height);
  const clamp = (n) => Math.max(0,Math.min(1,n));
  let transparent=0, green=0, neutral=0;
  for(let i=0;i<original.data.length;i+=4){
    const [r,g,b] = original.data.slice(i,i+3);
    const chroma = g-Math.max(r,b);
    const isGreen = chroma>10;
    const alpha = isGreen ? clamp((chroma-10)/50) : clamp((Math.max(r,g,b)-40)/180);
    out.data[i+3]=Math.round(alpha*255);
    if(!alpha){transparent++; continue;}
    if(isGreen)green++;else neutral++;
    for(let c=0;c<3;c++)
      out.data[i+c]=Math.max(0,Math.min(255,Math.round((original.data[i+c]-16*(1-alpha))/alpha)));
  }
  ctx.putImageData(out,0,0);
  const dark=canvas.toDataURL('image/png');
  for(let i=0;i<out.data.length;i+=4){
    const chroma=original.data[i+1]-Math.max(original.data[i],original.data[i+2]);
    if(out.data[i+3] && chroma<=10){
      out.data[i]=15;out.data[i+1]=23;out.data[i+2]=32;
    }
  }
  ctx.putImageData(out,0,0);
  const light=canvas.toDataURL('image/png');
  const result={width:canvas.width,height:canvas.height,transparent_pixels:transparent,
    green_pixels:green,wordmark_pixels:neutral,dark,light};
  document.body.innerHTML='<pre id="result"></pre>';
  document.getElementById('result').textContent=JSON.stringify(result);
});
</script>`;
const temporary = mkdtempSync(join(tmpdir(), 'edivo-transparent-'));
let result;
try {
  writeFileSync(join(temporary, 'source.html'), html, { flag: 'wx', mode: 0o600 });
  const browserArgs = [
    '--no-sandbox',
    '--disable-gpu',
    '--disable-background-networking',
    '--disable-dev-shm-usage',
    '--timeout=15000',
    '--virtual-time-budget=2000',
    '--dump-dom',
    'file:///input/source.html',
  ];
  const launch = `const f=require('fs'),p=require('path'),c=require('child_process');
const dirs=f.readdirSync('/ms-playwright').filter(x=>x.startsWith('chromium_headless_shell-'));
const choices=dirs.flatMap(x=>['chrome-headless-shell-linux64/chrome-headless-shell','chrome-linux64/headless_shell','chrome-linux/headless_shell'].map(b=>p.join('/ms-playwright',x,b)));
const binary=choices.find(x=>f.existsSync(x));if(!binary)throw Error('headless_shell_not_found');
process.stdout.write(c.execFileSync(binary,${JSON.stringify(browserArgs)},{encoding:'utf8',maxBuffer:8*1024*1024,timeout:30000,stdio:['ignore','pipe','pipe']}));`;
  const output = execFileSync(
    'docker',
    [
      'run',
      '--rm',
      '--network',
      'none',
      '--user',
      `${process.getuid()}:${process.getgid()}`,
      '--cpus',
      '1',
      '--memory',
      '768m',
      '--pids-limit',
      '128',
      '--cap-drop',
      'ALL',
      '--security-opt',
      'no-new-privileges:true',
      '--volume',
      `${temporary}:/input:ro`,
      'mcr.microsoft.com/playwright:v1.58.2-noble',
      'node',
      '-e',
      launch,
    ],
    { encoding: 'utf8', maxBuffer: 8 * 1024 * 1024, timeout: 60000 },
  );
  const raw = output.match(/<pre id="result">([\s\S]*?)<\/pre>/)?.[1];
  if (!raw) throw new Error('Logo processing did not return a result');
  result = JSON.parse(raw.replaceAll('&amp;', '&').replaceAll('&lt;', '<').replaceAll('&gt;', '>'));
} finally {
  rmSync(temporary, { recursive: true, force: true });
}
const assets = {};
for (const theme of ['dark', 'light']) {
  const target = join(root, `frontend/public/brand/edivo-logo-${theme}-transparent.png`);
  const bytes = Buffer.from(result[theme].split(',')[1], 'base64');
  if (existsSync(target)) {
    if (!readFileSync(target).equals(bytes))
      throw new Error('Existing transparent logo differs; review before replacing.');
  } else writeFileSync(target, bytes, { flag: 'wx' });
  assets[theme] = {
    path: `frontend/public/brand/edivo-logo-${theme}-transparent.png`,
    bytes: bytes.length,
    sha256: sha256(bytes),
  };
}
mkdirSync(evidence, { recursive: true });
const manifest = {
  source: 'frontend/public/brand/edivo-logo.png',
  source_sha256: sha256(source),
  method: 'Deterministic raster alpha from existing Canvas asset. No wordmark reconstruction.',
  dimensions: [result.width, result.height],
  transparent_pixels: result.transparent_pixels,
  green_pixels: result.green_pixels,
  wordmark_pixels: result.wordmark_pixels,
  assets,
};
const target = join(evidence, 'manifest.json');
const bytes = Buffer.from(JSON.stringify(manifest, null, 2) + '\n');
if (existsSync(target)) {
  if (!readFileSync(target).equals(bytes)) throw new Error('Existing transparent manifest differs');
} else writeFileSync(target, bytes, { flag: 'wx' });
console.log(JSON.stringify({ status: 'passed', ...manifest }));
