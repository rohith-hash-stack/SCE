// Layer 4 Node tracer runtime. Loaded with `mocha --require <this file>`, or
// through NODE_OPTIONS="--require <this file>" for runners that fork (Vitest
// 0.32 `--no-threads`), while the focal function is instrumented on disk
// (harness/experiments/layer4/source_edit.py instrument_js) to call
// globalThis.__l4.enter() on entry and globalThis.__l4.exit(token) in a
// `finally`. Records, per outermost focal call:
//   ancestors   - repo frames below the focal function (Error().stack; Vitest
//                 and --enable-source-maps rewrite frames to original lines)
//   chains      - each call's caller frames in order ("~" outside the repo),
//                 for call depths
//   descendants - repo functions V8 executed while it was on the stack
//                 (precise coverage, call counts, reset at entry, read at exit),
//                 positions mapped through each script's source map - inline
//                 (Vitest/esbuild) or an external .map file (rollup dist/) -
//                 back to the original .ts/.js line
//   tests       - the Mocha test (root hooks) or Vitest test (expect.getState)
//   raw_stack   - the first focal call's full stack, for source-map checks
// Locations are `relpath:line:name`; harness/experiments/layer4/trace_node.py
// maps them to Prism qualified names. Env: L4_ROOT, L4_OUT, L4_FOCAL_FILE
// (relpath), L4_FOCAL_LINE, L4_FOCAL_ASYNC, L4_MULTI=1 (one output file per
// process: `${L4_OUT}.${pid}.json`, written only by processes that ran the
// focal function).
// Limit: for an async focal function the `finally` runs when its promise
// settles, so work interleaved by the event loop in between is counted too
// (manifest flag `async_window`).
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const inspector = require("node:inspector");
const { fileURLToPath } = require("node:url");

const ROOT = path.resolve(process.env.L4_ROOT || process.cwd()) + path.sep;
const OUT = process.env.L4_OUT;
const MULTI = process.env.L4_MULTI === "1";
const FOCAL_FILE = process.env.L4_FOCAL_FILE || "";
const FOCAL_LINE = +(process.env.L4_FOCAL_LINE || 0);
const SELF = __filename;
const CHAIN_FRAMES = 24;  // caller frames kept per call for depths: "~" = a frame outside the repo
Error.stackTraceLimit = 200;

const session = new inspector.Session();
session.connect();
function post(method, params) {
  let result, error;
  session.post(method, params || {}, (e, r) => { error = e; result = r; });  // same-thread session: synchronous
  if (error) throw error;
  return result;
}
post("Profiler.enable");
post("Profiler.startPreciseCoverage", { callCount: true, detailed: false });
let debuggerOn = false;

const state = {
  calls: 0, depth: 0, ancestors: {}, chains: {}, descendants: {}, tests: new Set(), rawStack: null,
  asyncWindow: process.env.L4_FOCAL_ASYNC === "1", currentTest: null,
  mapping: { mapped: 0, unmapped: 0, generated: 0, files: {} },
};

function toPath(file) { return file && file.startsWith("file://") ? fileURLToPath(file) : file; }

function repoRelative(file) {
  file = toPath(file);
  if (!file || !file.startsWith(ROOT)) return null;
  const rel = file.slice(ROOT.length).split(path.sep).join("/");
  if (rel.includes("node_modules/") || /(^|\/)(test|tests|__tests__)\//.test(rel) || /\.(test|spec)\.[cm]?[jt]sx?$/.test(rel)) return null;
  return rel;
}

function lineStarts(text) {
  const starts = [0];
  for (let i = 0; i < text.length; i++) if (text.charCodeAt(i) === 10) starts.push(i + 1);
  return starts;
}

function position(starts, offset) {  // 0-based line, column
  let lo = 0, hi = starts.length - 1;
  while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (starts[mid] <= offset) lo = mid; else hi = mid - 1; }
  return { line: lo, col: offset - starts[lo] };
}

// ---- source maps (base64 VLQ, no dependencies) ----
const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
const B64I = Object.fromEntries([...B64].map((c, i) => [c, i]));
function decodeMappings(mappings) {
  const lines = [];
  let src = 0, srcLine = 0, srcCol = 0;
  for (const lineText of mappings.split(";")) {
    const segs = [];
    let genCol = 0;
    for (const seg of lineText.split(",")) {
      if (!seg) continue;
      const vals = [];
      let value = 0, shift = 0;
      for (const ch of seg) {
        const digit = B64I[ch];
        value += (digit & 31) << shift;
        if (digit & 32) { shift += 5; continue; }
        vals.push(value & 1 ? -(value >> 1) : value >> 1);
        value = 0; shift = 0;
      }
      genCol += vals[0];
      if (vals.length >= 4) {
        src += vals[1]; srcLine += vals[2]; srcCol += vals[3];
        segs.push([genCol, src, srcLine]);
      }
    }
    lines.push(segs);
  }
  return lines;
}

const maps = new Map();  // scriptId or file -> {starts, lines, sources} | null
function scriptSource(scriptId, file) {
  try {
    if (!debuggerOn) { post("Debugger.enable"); debuggerOn = true; }
    return post("Debugger.getScriptSource", { scriptId }).scriptSource;
  } catch (e) {
    try { return fs.readFileSync(file, "utf8"); } catch (e2) { return null; }
  }
}
function loadMap(scriptId, file) {
  const key = scriptId || file;
  if (maps.has(key)) return maps.get(key);
  let entry = null;
  const text = scriptSource(scriptId, file);
  if (text != null) {
    const refs = [...text.matchAll(/\/\/[#@] sourceMappingURL=(\S+)\s*$/gm)];
    const m = refs.length ? refs[refs.length - 1] : null;  // the last one wins (vite-node appends its own)
    let json = null;
    try {
      if (m && m[1].startsWith("data:")) json = JSON.parse(Buffer.from(m[1].split(",")[1], "base64").toString("utf8"));
      else if (m) json = JSON.parse(fs.readFileSync(path.resolve(path.dirname(file), m[1]), "utf8"));
    } catch (e) { json = null; }
    const base = json ? path.resolve(path.dirname(file), json.sourceRoot || "") : null;
    entry = {
      text,
      starts: lineStarts(text),
      lines: json ? decodeMappings(json.mappings || "") : null,
      sources: json ? json.sources.map(s => (s.startsWith("file://") ? fileURLToPath(s) : path.resolve(base, s))) : null,
    };
  }
  maps.set(key, entry);
  return entry;
}
// vite-node's SSR transform turns every `export` into a generated live-binding
// getter, `Object.defineProperty(__vi_ssr_exports__, "f", { ..., get(){ return f }})`,
// appended after the declaration: it runs whenever an importer reads `f` and is
// no function of the repository (mapped, it would land on the line after the
// declaration and fold into whatever function holds it).
const SSR_EXPORT_GETTER = /get\s*\(\s*\)\s*\{\s*return\s+[\w$.]+\s*;?\s*\}/y;
function generatedGetter(scriptId, file, offset) {
  const map = loadMap(scriptId, file);
  if (!map || !map.text) return false;
  SSR_EXPORT_GETTER.lastIndex = offset;
  return SSR_EXPORT_GETTER.test(map.text) && /__vi_ssr_exports__|__vite_ssr_exports__/.test(map.text);
}
function original(scriptId, file, offset) {  // -> {file, line (1-based)}
  const map = loadMap(scriptId, file);
  if (!map) return { file, line: 0 };
  const pos = position(map.starts, offset);
  if (map.lines) {
    const segs = map.lines[pos.line] || [];
    let best = null;
    for (const s of segs) { if (s[0] <= pos.col) best = s; else break; }
    if (!best && segs.length) best = segs[0];
    if (best) { state.mapping.mapped++; return { file: map.sources[best[1]], line: best[2] + 1 }; }
  }
  state.mapping.unmapped++;
  return { file, line: pos.line + 1 };
}

function bump(map, key) { map[key] = (map[key] || 0) + 1; }

function stackFrames() {
  const frames = [];
  for (const line of (new Error().stack || "").split("\n").slice(1)) {
    const m = line.match(/at (?:async )?(?:(.*?) \()?(.*?):(\d+):\d+\)?$/);
    if (m) frames.push({ name: (m[1] || "<anonymous>").replace(/^(?:Object|Function|Module)\./, ""), file: toPath(m[2]), line: +m[3] });
  }
  return frames;
}

function currentTest() {
  if (state.currentTest) return state.currentTest;
  try {  // Vitest/Jest globals
    const s = globalThis.expect && globalThis.expect.getState && globalThis.expect.getState();
    if (s && s.currentTestName) return `${s.testPath ? path.relative(ROOT, s.testPath) + " > " : ""}${s.currentTestName}`;
  } catch (e) { /* not a Vitest worker */ }
  return null;
}

globalThis.__l4 = {
  enter() {
    state.calls++;
    if (state.depth++ > 0) return { outer: false };
    const test = currentTest();
    if (test) state.tests.add(test);
    const frames = stackFrames().filter(f => f.file !== SELF);
    if (!state.rawStack) state.rawStack = frames.slice(0, 15).map(f => `${f.file}:${f.line}:${f.name}`);
    // frames[0] is the focal function itself
    const chain = [];
    for (const f of frames.slice(1)) {
      const rel = repoRelative(f.file);
      if (rel) bump(state.ancestors, `${rel}:${f.line}:${f.name}`);
      if (chain.length < CHAIN_FRAMES) chain.push(rel ? `${rel}:${f.line}:${f.name}` : "~");
    }
    bump(state.chains, chain.join("|"));  // caller order, for call depths (trace_node.map_manifest)
    post("Profiler.takePreciseCoverage");  // reset call counts at entry
    return { outer: true };
  },
  exit(token) {
    state.depth--;
    if (!token || !token.outer) return;
    const { result } = post("Profiler.takePreciseCoverage");
    for (const script of result) {
      const file = toPath(script.url);
      if (!file || file === SELF || !file.startsWith(ROOT)) continue;
      for (const fn of script.functions) {
        const count = fn.ranges.length ? fn.ranges[0].count : 0;
        if (count <= 0) continue;
        if (generatedGetter(script.scriptId, file, fn.ranges[0].startOffset)) { state.mapping.generated++; continue; }
        const loc = original(script.scriptId, file, fn.ranges[0].startOffset);
        const rel = repoRelative(loc.file);
        if (!rel) continue;
        state.mapping.files[rel] = (state.mapping.files[rel] || 0) + 1;
        if (rel === FOCAL_FILE && loc.line === FOCAL_LINE) continue;  // the focal function itself
        // anonymous callbacks are kept: mapping folds them into their enclosing function
        bump(state.descendants, `${rel}:${loc.line}:${fn.functionName || "<anonymous>"}`);
      }
    }
  },
};

function write() {
  if (!OUT || (MULTI && state.calls === 0)) return;
  const sorted = o => Object.fromEntries(Object.entries(o).sort());
  const target = MULTI ? `${OUT}.${process.pid}.json` : OUT;
  fs.writeFileSync(target, JSON.stringify({
    focal_file: FOCAL_FILE, focal_calls: state.calls, tests: [...state.tests].sort(),
    ancestors: sorted(state.ancestors), chains: sorted(state.chains), descendants: sorted(state.descendants), async_window: state.asyncWindow,
    raw_stack: state.rawStack, source_mapping: { mapped: state.mapping.mapped, unmapped: state.mapping.unmapped,
      generated_getters: state.mapping.generated,
      files: sorted(state.mapping.files) },
  }, null, 1));
}
process.on("exit", write);

// Mocha root hooks (picked up when loaded with `mocha --require`)
exports.mochaHooks = {
  beforeEach() { state.currentTest = this.currentTest ? this.currentTest.fullTitle() : null; },
  afterEach() { state.currentTest = null; },
};
exports.state = state;
