// Loads the generator engine out of sclgen.html so Node can run it.
// The page itself stays a single self-contained file.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const HTML = path.join(__dirname, '..', 'sclgen.html');

function loadEngine() {
  const html = fs.readFileSync(HTML, 'utf8');
  const m = /\/\* ENGINE START \*\/([\s\S]*?)\/\* ENGINE END \*\//.exec(html);
  if (!m) throw new Error('engine markers not found in sclgen.html');
  const ctx = vm.createContext({ BigInt, Number, Map, Set, Array, Object, String, RegExp, Error, JSON, Math, parseInt });
  vm.runInContext(m[1] + '\nthis.SCLGEN = SCLGEN;', ctx, { filename: 'sclgen.html' });
  return ctx.SCLGEN;
}

module.exports = { loadEngine, HTML };
