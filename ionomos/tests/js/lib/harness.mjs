// Dev-only test harness for downstream/assets/report.js.
//
// report.js is an IIFE that reads its data from the `#ionomos-data` JSON
// script tag and wires setup() onto DOMContentLoaded (or runs it at once when
// parsing already finished). We load it against jsdom in two modes:
//
//   loadReport()    — parse the fixture without running scripts, then
//                     window.eval() the report source. Data can be swapped
//                     per test. Browser APIs jsdom lacks (canvas 2d context,
//                     matchMedia, scrollIntoView, object URLs) are stubbed
//                     via beforeParse, i.e. before any script runs.
//   loadReportRaw() — run the fixture HTML exactly as shipped
//                     (runScripts: "dangerously"), data untouched. Used by
//                     the escaping tests to exercise the real HTML
//                     tokenisation of the shipped file.
//
// Both loaders await DOMContentLoaded (jsdom fires it asynchronously) and
// collect page errors (uncaught exceptions, console.error) via a
// VirtualConsole; tests assert `errors` is empty.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";

const HERE = dirname(fileURLToPath(import.meta.url));
export const FIXTURE = join(HERE, "..", "fixture.html");

export function readFixture() {
  return readFileSync(FIXTURE, "utf8");
}

/** The embedded report data (already JSON-parsed). */
export function baseData() {
  const m = readFixture().match(
    /<script id='ionomos-data' type='application\/json'>([\s\S]*?)<\/script>/
  );
  if (!m) throw new Error("no ionomos-data script in fixture.html");
  return JSON.parse(m[1]);
}

/** The inlined report.js source, exactly as report.py ships it. */
export function reportJs() {
  const m = readFixture().match(
    /<script id='ionomos-data'[^>]*>[\s\S]*?<\/script>\s*<script>([\s\S]*?)<\/script>/
  );
  if (!m) throw new Error("no inline report script after the data script");
  return m[1];
}

// jsdom draws nothing: give every canvas a context that accepts any call.
// A Proxy keeps this robust against whatever the report draws next.
function makeCtx(canvas) {
  return new Proxy({ canvas }, {
    get(target, prop) {
      if (prop in target) return target[prop];
      return () => {};
    },
    set() {
      return true;
    },
  });
}

function stubBrowser(win, errors) {
  win.HTMLCanvasElement.prototype.getContext = function () {
    return makeCtx(this);
  };
  // jsdom has no matchMedia; the theme setup only listens for scheme changes.
  win.matchMedia = (q) => ({
    matches: false,
    media: q,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
  if (!win.Element.prototype.scrollIntoView) win.Element.prototype.scrollIntoView = () => {};
  // jsdom has no layout engine: report.js's hover maths divides by
  // getBoundingClientRect().width, which is 0 and turns coordinates into
  // Infinity (no tooltip ever fires). Report the element's own width/height
  // attributes — the SVG roots carry them — as a plausible client rect.
  // jsdom HAS getBoundingClientRect but always returns zeros; report.js's
  // hover maths divides by that width and turns coordinates into Infinity
  // (no tooltip ever fires). Report the element's own width/height
  // attributes — the SVG roots carry them — as a plausible client rect.
  win.Element.prototype.getBoundingClientRect = function () {
    const w = Number(this.getAttribute && this.getAttribute("width")) || 640;
    const h = Number(this.getAttribute && this.getAttribute("height")) || 400;
    return { x: 0, y: 0, left: 0, top: 0, right: w, bottom: h, width: w, height: h, toJSON() {} };
  };
  // download() hits object URLs; capture the blobs so tests can read them.
  win.__blobs = [];
  win.URL.createObjectURL = (blob) => {
    win.__blobs.push(blob);
    return `blob:fake-${win.__blobs.length}`;
  };
  win.URL.revokeObjectURL = () => {};
  if (errors) {
    win.addEventListener("error", (e) => errors.push(`uncaught: ${e.error?.stack || e.message}`));
  }
}

/** Escape `</` the way report.py does before putting JSON inside a script tag. */
function embedData(data) {
  return JSON.stringify(data).replaceAll("</", "<\\/");
}

function waitReady(win) {
  if (win.document.readyState !== "loading") return Promise.resolve();
  return new Promise((resolve) => {
    win.addEventListener("DOMContentLoaded", () => setTimeout(resolve, 0));
  });
}

/**
 * Load the report with `data` (defaults to the fixture's own payload).
 * Returns { window, document, errors } after setup() has run.
 */
export async function loadReport({ data } = {}) {
  const html = readFixture();
  const js = reportJs();
  const shell =
    data === undefined
      ? html.replace(/<script>[\s\S]*<\/script>\s*<\/body>/, "</body>") // strip the code script, keep the data script
      : html
          .replace(/<script>[\s\S]*<\/script>\s*<\/body>/, "</body>")
          .replace(
            /(<script id='ionomos-data' type='application\/json'>)[\s\S]*?(<\/script>)/,
            (_m, open, close) => open + embedData(data) + close
          );
  const errors = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errors.push(String(e.detail?.message || e.message || e)));
  vc.on("error", (...a) => errors.push(a.map(String).join(" ")));
  const dom = new JSDOM(shell, {
    runScripts: "outside-only",
    url: "file:///report.html",
    virtualConsole: vc,
    beforeParse: (win) => stubBrowser(win, errors),
  });
  const { window } = dom;
  window.eval(js); // registers setup on DOMContentLoaded (or runs it at once)
  await waitReady(window);
  return { window, document: window.document, errors };
}

/**
 * Run the fixture exactly as a browser would (inline scripts executed by the
 * HTML parser, data script untouched).
 */
export async function loadReportRaw({ html } = {}) {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errors.push(String(e.detail?.message || e.message || e)));
  vc.on("error", (...a) => errors.push(a.map(String).join(" ")));
  const dom = new JSDOM(html ?? readFixture(), {
    runScripts: "dangerously",
    url: "file:///report.html",
    virtualConsole: vc,
    beforeParse: (win) => stubBrowser(win, errors),
  });
  const { window } = dom;
  await waitReady(window);
  return { window, document: window.document, errors };
}

/** Read a jsdom Blob as text (FileReader works on jsdom Blobs; .text() may not). */
export function blobText(win, blob) {
  return new Promise((resolve, reject) => {
    const fr = new win.FileReader();
    fr.onload = () => resolve(String(fr.result));
    fr.onerror = () => reject(fr.error);
    fr.readAsText(blob);
  });
}

/**
 * Shallow merge for building payload variants: plain objects merge one level,
 * anything else replaces.
 */
export function withData(base, patch) {
  const out = { ...base };
  for (const [k, v] of Object.entries(patch)) {
    out[k] =
      v && typeof v === "object" && !Array.isArray(v) && !Array.isArray(base?.[k])
        ? { ...(base[k] ?? {}), ...v }
        : v;
  }
  return out;
}
