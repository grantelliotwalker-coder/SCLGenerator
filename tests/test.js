#!/usr/bin/env node
// Tests for sclgen.html. Run: node tests/test.js        (add --fix to refresh embedded copies)
//  1. The copies of repo files embedded in sclgen.html match the files.
//  2. Both examples regenerate byte for byte to the files in output/.
//  3. Every spec in tests/cases/ gives exactly the messages in its .expected file
//     (recorded from the original Python sclgen.py before it was retired).
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const { loadEngine, HTML } = require('./engine');

const ROOT = path.join(__dirname, '..');
const EMBEDS = ['AI_INSTRUCTIONS.md', 'schema/block_spec.schema.json',
  'examples/pump_run_monitor.json', 'examples/template_threshold.json'];
const fix = process.argv.includes('--fix');
let failed = 0, passed = 0;
const ok = (cond, name, detail) => {
  if (cond) passed++;
  else { failed++; console.log(`FAIL ${name}${detail ? '\n' + detail : ''}`); }
};

// 1. embedded copies
let html = fs.readFileSync(HTML, 'utf8');
for (const name of EMBEDS) {
  const file = fs.readFileSync(path.join(ROOT, name), 'utf8');
  if (/<\/script|<!--|<script/i.test(file)) { ok(false, `embed ${name}`, 'file contains text that cannot go inside <script>'); continue; }
  const re = new RegExp(`(<script type="[^"]+" id="embed-${name.replace(/[.\/]/g, '\\$&')}">)([\\s\\S]*?)(</script>)`);
  const m = re.exec(html);
  if (!m) { ok(false, `embed ${name}`, 'block not found'); continue; }
  const want = '\n' + file;
  if (m[2] === want) { ok(true); continue; }
  if (fix) { html = html.replace(re, (a, b, c, d) => b + want + d); console.log(`fixed embed ${name}`); passed++; }
  else ok(false, `embed ${name}`, 'out of date: run node tests/test.js --fix');
}
if (fix) fs.writeFileSync(HTML, html, 'utf8');

const engine = loadEngine();

// 2. examples regenerate to output/
for (const ex of fs.readdirSync(path.join(ROOT, 'examples')).filter((f) => f.endsWith('.json'))) {
  const r = engine.run(fs.readFileSync(path.join(ROOT, 'examples', ex), 'utf8'));
  ok(r.code && r.errors.length === 0 && r.warnings.length === 0, `example ${ex} is valid`,
    [...r.errors, ...r.warnings].map((p) => p.line).join('\n') || r.crash);
  if (!r.code) continue;
  const want = fs.readFileSync(path.join(ROOT, 'output', r.fileName), 'utf8');
  ok(r.fileText === want, `example ${ex} matches output/${r.fileName}`);
}

// 3. recorded cases
const casesDir = path.join(__dirname, 'cases');
for (const f of fs.readdirSync(casesDir).filter((x) => x.endsWith('.json')).sort()) {
  const spec = path.join(casesDir, f);
  const expFile = spec.replace(/\.json$/, '.expected');
  let got;
  try {
    got = execFileSync(process.execPath, [path.join(__dirname, 'cli.js'), spec, '--check'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
  } catch (e) { got = e.stdout + (e.stderr ? '' : ''); }
  const want = fs.readFileSync(expFile, 'utf8');
  ok(got === want, `case ${f}`, `--- expected\n${want}--- got\n${got}`);
}

console.log(`${passed} passed, ${failed} failed`);
process.exitCode = failed ? 1 : 0;
