#!/usr/bin/env node
/* global Buffer, console, process */
/** Extend the local Canvas brand pipeline: green symbol only, with real alpha. */
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const sourcePath = 'frontend/public/brand/edivo-icon.png';
const source = readFileSync(join(root, sourcePath));
const hash = (bytes) => createHash('sha256').update(bytes).digest('hex');
const html = `<!doctype html><img id="source" src="data:image/png;base64,${source.toString('base64')}"><script>
addEventListener('load',()=>{
 const img=document.getElementById('source'),c=document.createElement('canvas');
 c.width=img.naturalWidth;c.height=img.naturalHeight;
 const ctx=c.getContext('2d',{willReadFrequently:true});ctx.drawImage(img,0,0);
 const pixels=ctx.getImageData(0,0,c.width,c.height);
 for(let i=0;i<pixels.data.length;i+=4){
  const [r,g,b]=pixels.data.slice(i,i+3),chroma=g-Math.max(r,b);
  const alpha=Math.max(0,Math.min(1,(chroma-10)/50));
  pixels.data[i+3]=Math.round(alpha*255);
  if(!alpha){pixels.data[i]=pixels.data[i+1]=pixels.data[i+2]=0;continue;}
  for(let channel=0;channel<3;channel++)
   pixels.data[i+channel]=Math.max(0,Math.min(255,Math.round((pixels.data[i+channel]-16*(1-alpha))/alpha)));
 }
 ctx.putImageData(pixels,0,0);
 const outputs=[16,32,48].map(size=>{
  const target=document.createElement('canvas');target.width=target.height=size;
  const t=target.getContext('2d');t.imageSmoothingEnabled=true;t.imageSmoothingQuality='high';
  t.drawImage(c,0,0,size,size);
  return {size,data:target.toDataURL('image/png')};
 });
 document.body.innerHTML='<pre id="result"></pre>';
 document.getElementById('result').textContent=JSON.stringify({source_size:[c.width,c.height],outputs});
});</script>`;
const temporary = mkdtempSync(join(tmpdir(), 'edivo-favicon-'));
let result;
try {
  writeFileSync(join(temporary, 'source.html'), html, { mode: 0o600, flag: 'wx' });
  const flags = [
    '--no-sandbox',
    '--disable-gpu',
    '--disable-background-networking',
    '--timeout=15000',
    '--virtual-time-budget=2000',
    '--dump-dom',
    'file:///input/source.html',
  ];
  const launch = `const f=require('fs'),p=require('path'),c=require('child_process');
const dirs=f.readdirSync('/ms-playwright').filter(x=>x.startsWith('chromium_headless_shell-'));
const choices=dirs.flatMap(x=>['chrome-headless-shell-linux64/chrome-headless-shell','chrome-linux64/headless_shell','chrome-linux/headless_shell'].map(b=>p.join('/ms-playwright',x,b)));
const binary=choices.find(x=>f.existsSync(x));if(!binary)throw Error('headless_shell_not_found');
process.stdout.write(c.execFileSync(binary,${JSON.stringify(flags)},{encoding:'utf8',maxBuffer:1048576,timeout:30000,stdio:['ignore','pipe','pipe']}));`;
  const output = execFileSync(
    'docker',
    [
      'run',
      '--rm',
      '--pull=never',
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
      '--shm-size',
      '256m',
      '--volume',
      `${temporary}:/input:ro`,
      'mcr.microsoft.com/playwright:v1.58.2-noble',
      'node',
      '-e',
      launch,
    ],
    { encoding: 'utf8', timeout: 60000, maxBuffer: 1048576 },
  );
  const raw = output.match(/<pre id="result">([\s\S]*?)<\/pre>/)?.[1];
  if (!raw) throw new Error('Favicon pipeline returned no output');
  result = JSON.parse(raw);
} finally {
  rmSync(temporary, { recursive: true, force: true });
}
const files = new Map();
for (const item of result.outputs) {
  if (!item.data.startsWith('data:image/png;base64,')) throw new Error('Unexpected favicon type');
  files.set(
    `favicon-green-${item.size}x${item.size}.png`,
    Buffer.from(item.data.split(',')[1], 'base64'),
  );
}
const frames = [16, 32, 48].map((size) => ({
  size,
  bytes: files.get(`favicon-green-${size}x${size}.png`),
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
files.set('favicon-green.ico', Buffer.concat([header, ...frames.map((frame) => frame.bytes)]));
const manifest = {
  source: sourcePath,
  source_sha256: hash(source),
  source_size: result.source_size,
  method:
    'Existing official icon pixels; Canvas green chroma matte removal, no redraw; transparent PNG/ICO.',
  outputs: {},
};
function saveNewOrIdentical(target, bytes) {
  if (existsSync(target)) {
    if (!readFileSync(target).equals(bytes))
      throw new Error('Existing favicon differs; review before replacing');
  } else writeFileSync(target, bytes, { flag: 'wx' });
}
for (const [name, bytes] of files) {
  saveNewOrIdentical(join(root, 'frontend/public', name), bytes);
  manifest.outputs[name] = { bytes: bytes.length, sha256: hash(bytes) };
}
const evidence = join(root, 'docs/design-refresh/evidence/transparent-favicon');
mkdirSync(evidence, { recursive: true });
saveNewOrIdentical(
  join(evidence, 'manifest.json'),
  Buffer.from(JSON.stringify(manifest, null, 2) + '\n'),
);
if (hash(readFileSync(join(root, sourcePath))) !== manifest.source_sha256)
  throw new Error('Icon source changed');
console.log(
  JSON.stringify({
    status: 'passed',
    assets: files.size,
    manifest: 'docs/design-refresh/evidence/transparent-favicon/manifest.json',
  }),
);
