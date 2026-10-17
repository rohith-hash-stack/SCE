// Layer 4 Node tracer runtime. Loaded with `mocha --require <this file>`
// (or a Vitest setupFile) while the focal function is instrumented on disk
// (harness/experiments/layer4/source_edit.py instrument_js) to call
// globalThis.__l4.enter() on entry and globalThis.__l4.exit(token) in a
// `finally`. Records, per outermost focal call:
//   ancestors   - repo frames below the focal function (Error().stack)
//   descendants - repo functions V8 executed while it was on the stack
//                 (precise coverage, call counts, reset at entry, read at exit)
//   tests       - the Mocha test running (root hooks)
// Raw locations are `relpath:line:name`; harness/experiments/layer4/trace_node.py
// maps them to Prism qualified names. Env: L4_ROOT, L4_OUT, L4_FOCAL_FILE (relpath).
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
const FOCAL_FILE = process.env.L4_FOCAL_FILE || "";
const FOCAL_LINE = +(process.env.L4_FOCAL_LINE || 0);
const SELF = __filename;
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

const state = { calls: 0, depth: 0, ancestors: {}, descendants: {}, tests: new Set(),
  asyncWindow: process.env.L4_FOCAL_ASYNC === "1", currentTest: null };
const lineCache = new Map();

function repoRelative(file) {
  if (!file) return null;
  if (file.startsWith("file://")) file = fileURLToPath(file);
  if (!file.startsWith(ROOT)) return null;
  const rel = file.slice(ROOT.length).split(path.sep).join("/");
  if (rel.startsWith("node_modules/") || /(^|\/)(test|tests|__tests__)\//.test(rel) || /\.(test|spec)\.[cm]?[jt]sx?$/.test(rel)) return null;
  return rel;
}

function lineOf(file, offset) {
  let starts = lineCache.get(file);
  if (!starts) {
    let text = "";
    try { text = fs.readFileSync(file, "utf8"); } catch (e) { return 0; }
    starts = [0];
    for (let i = 0; i < text.length; i++) if (text.charCodeAt(i) === 10) starts.push(i + 1);
    lineCache.set(file, starts);
  }
  let lo = 0, hi = starts.length - 1;
  while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (starts[mid] <= offset) lo = mid; else hi = mid - 1; }
  return lo + 1;
}

function bump(map, key) { map[key] = (map[key] || 0) + 1; }

function stackFrames() {
  const frames = [];
  for (const line of (new Error().stack || "").split("\n").slice(1)) {
    const m = line.match(/at (?:async )?(?:(.*?) \()?(.*?):(\d+):\d+\)?$/);
    if (m) frames.push({ name: (m[1] || "<anonymous>").replace(/^(?:Object|Function|Module)\./, ""), file: m[2], line: +m[3] });
  }
  return frames;
}

globalThis.__l4 = {
  enter() {
    state.calls++;
    if (state.depth++ > 0) return { outer: false };
    if (state.currentTest) state.tests.add(state.currentTest);
    const frames = stackFrames().filter(f => f.file !== SELF);
    // frames[0] is the focal function itself
    for (const f of frames.slice(1)) {
      const rel = repoRelative(f.file);
      if (rel) bump(state.ancestors, `${rel}:${f.line}:${f.name}`);
    }
    post("Profiler.takePreciseCoverage");  // reset call counts at entry
    return { outer: true };
  },
  exit(token) {
    state.depth--;
    if (!token || !token.outer) return;
    const { result } = post("Profiler.takePreciseCoverage");
    for (const script of result) {
      const file = script.url.startsWith("file://") ? fileURLToPath(script.url) : script.url;
      const rel = repoRelative(file);
      if (!rel) continue;
      for (const fn of script.functions) {
        const count = fn.ranges.length ? fn.ranges[0].count : 0;
        if (count <= 0 || file === SELF) continue;
        const line = lineOf(file, fn.ranges[0].startOffset);
        if (rel === FOCAL_FILE && line === FOCAL_LINE) continue;  // the focal function itself
        // anonymous callbacks are kept: mapping folds them into their enclosing function
        bump(state.descendants, `${rel}:${line}:${fn.functionName || "<anonymous>"}`);
      }
    }
  },
};

function write() {
  if (!OUT) return;
  const sorted = o => Object.fromEntries(Object.entries(o).sort());
  fs.writeFileSync(OUT, JSON.stringify({
    focal_file: FOCAL_FILE, focal_calls: state.calls, tests: [...state.tests].sort(),
    ancestors: sorted(state.ancestors), descendants: sorted(state.descendants), async_window: state.asyncWindow,
  }, null, 1));
}
process.on("exit", write);

// Mocha root hooks (picked up when loaded with `mocha --require`)
exports.mochaHooks = {
  beforeEach() { state.currentTest = this.currentTest ? this.currentTest.fullTitle() : null; },
  afterEach() { state.currentTest = null; },
};
exports.state = state;
