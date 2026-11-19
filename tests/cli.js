#!/usr/bin/env node
// Command-line wrapper around the engine in sclgen.html, for tests and batch use.
// Prints the same messages the original sclgen.py did.
//   node tests/cli.js spec.json [-o out.scl] [--check]
const fs = require('fs');
const path = require('path');
const { loadEngine } = require('./engine');

// Returns {out, code, fileText, fileName, crash} for a spec's text, printing nothing.
function cliRun(text, specName, check, engine) {
  const lines = [];
  const log = (x) => lines.push(x);
  const r = (engine || loadEngine()).run(text);
  const done = (code, extra) => Object.assign({ out: lines.length ? lines.join('\n') + '\n' : '', code }, extra);
  if (r.jsonError) {
    const j = r.jsonError;
    log(`ERROR   ${specName}: not valid JSON (line ${j.lineno}, col ${j.colno}): ${j.message}`);
    return done(1);
  }
  // like the Python original: a failure while checking prints nothing; while generating, the warnings first
  if (r.crashStage === 'validate') return done(1, { crash: r.crash });
  for (const p of [...r.errors, ...r.warnings]) log(p.line);
  if (r.crash) return done(1, { crash: r.crash });
  if (r.errors.length) {
    log(`\n${r.errors.length} error(s), ${r.warnings.length} warning(s). Nothing written.`);
    return done(1);
  }
  if (r.internal) {
    for (const p of r.internal) log(p.line);
    log('Internal error: generated block is unbalanced. Nothing written.');
    return done(1);
  }
  if (check) { log(`OK: spec valid, ${r.warnings.length} warning(s).`); return done(0); }
  return done(0, { fileText: r.fileText, fileName: r.fileName, lineCount: r.lineCount, warnings: r.warnings.length });
}

function main(argv) {
  let spec = null, out = null, check = false;
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--check') check = true;
    else if (argv[i] === '-o' || argv[i] === '--output') out = argv[++i];
    else spec = argv[i];
  }
  if (!spec) { console.error('usage: node tests/cli.js spec.json [-o out.scl] [--check]'); return 2; }
  let text;
  try { text = fs.readFileSync(spec, 'utf8'); } catch (e) { console.log(`ERROR   ${e.message}`); return 1; }
  const res = cliRun(text, path.basename(spec), check);
  process.stdout.write(res.out);
  if (res.crash) console.error(`CRASH ${res.crash}`);
  if (res.code || check || res.crash) return res.code;
  const dest = out || path.join(path.dirname(spec), res.fileName);
  fs.writeFileSync(dest, res.fileText, 'utf8');
  console.log(`OK: wrote ${dest} (${res.lineCount} lines, ${res.warnings} warning(s)).`);
  return 0;
}

module.exports = { cliRun };
if (require.main === module) process.exitCode = main(process.argv.slice(2));
