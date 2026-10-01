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
  // ...and download() clicks an <a download>: record the file name with its blob instead of letting jsdom
  // try to navigate to it, so tests can check what a file is called.
  win.__downloads = [];
  const click = win.HTMLAnchorElement.prototype.click;
  win.HTMLAnchorElement.prototype.click = function () {
    if (!this.hasAttribute("download")) return click.call(this);
    const k = /^blob:fake-(\d+)$/.exec(this.href);
    win.__downloads.push({ name: this.download, blob: k ? win.__blobs[+k[1] - 1] : null });
  };
  if (errors) {
    win.addEventListener("error", (e) => errors.push(`uncaught: ${e.error?.stack || e.message}`));
  }
}

/** Escape `</` and `<!--` the way report.py does before putting JSON inside a script tag. */
export function embedData(data) {
  return JSON.stringify(data).replaceAll("</", "<\\/").replaceAll("<!--", "<\\u0021--");
}

function waitReady(win) {
  if (win.document.readyState !== "loading") return Promise.resolve();
  return new Promise((resolve) => {
    win.addEventListener("DOMContentLoaded", () => setTimeout(resolve, 0));
  });
}

/**
 * Load the report with `data` (defaults to the fixture's own payload).
 * `storage` ({ key: value }) is put in localStorage before the report script runs (what an earlier report
 * left in this browser); `before(window)` runs then too, for stubs a test needs in place from the start.
 * Returns { window, document, errors } after setup() has run.
 */
export async function loadReport({ data, url, storage, before } = {}) {
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
    url: url || (storage ? "http://localhost/report.html" : "file:///report.html"),  // a file:// page has no localStorage in jsdom
    virtualConsole: vc,
    beforeParse: (win) => stubBrowser(win, errors),
  });
  const { window } = dom;
  for (const [k, v] of Object.entries(storage || {})) window.localStorage.setItem(k, typeof v === "string" ? v : JSON.stringify(v));
  if (before) before(window);
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

/** Read a jsdom Blob as bytes. */
export function blobBytes(win, blob) {
  return new Promise((resolve, reject) => {
    const fr = new win.FileReader();
    fr.onload = () => resolve(new Uint8Array(fr.result));
    fr.onerror = () => reject(fr.error);
    fr.readAsArrayBuffer(blob);
  });
}

/** CRC-32 (the zip and PNG checksum), written out here so the tests do not check the report's with its own. */
export function crc32(bytes) {
  let c = 0xffffffff;
  for (const b of bytes) {
    c ^= b;
    for (let k = 0; k < 8; k++) c = c & 1 ? (c >>> 1) ^ 0xedb88320 : c >>> 1;
  }
  return (c ^ 0xffffffff) >>> 0;
}

/**
 * Read a store-only .zip the way an unzip program does: find the end record, walk the central directory,
 * and check each entry against its local header. Returns [{ name, data, crc, crcOk, method, flags }].
 */
export function unzip(bytes) {
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const end = bytes.length - 22;
  if (end < 0 || dv.getUint32(end, true) !== 0x06054b50) throw new Error("no end-of-central-directory record");
  const n = dv.getUint16(end + 10, true), cdSize = dv.getUint32(end + 12, true), cdAt = dv.getUint32(end + 16, true);
  if (dv.getUint16(end + 8, true) !== n) throw new Error("entry counts differ");
  if (cdAt + cdSize !== end) throw new Error("the central directory does not end at the end record");
  const dec = new TextDecoder("utf-8", { fatal: true });
  const out = [];
  let p = cdAt;
  for (let i = 0; i < n; i++) {
    if (dv.getUint32(p, true) !== 0x02014b50) throw new Error("bad central header " + i);
    const flags = dv.getUint16(p + 8, true), method = dv.getUint16(p + 10, true), crc = dv.getUint32(p + 16, true);
    const size = dv.getUint32(p + 20, true), usize = dv.getUint32(p + 24, true), nl = dv.getUint16(p + 28, true);
    const extra = dv.getUint16(p + 30, true), comment = dv.getUint16(p + 32, true), at = dv.getUint32(p + 42, true);
    const name = dec.decode(bytes.subarray(p + 46, p + 46 + nl));
    if (dv.getUint32(at, true) !== 0x04034b50) throw new Error("bad local header for " + name);
    const lnl = dv.getUint16(at + 26, true), lextra = dv.getUint16(at + 28, true);
    const lname = dec.decode(bytes.subarray(at + 30, at + 30 + lnl));
    if (lname !== name || dv.getUint32(at + 14, true) !== crc || dv.getUint32(at + 18, true) !== size || size !== usize) {
      throw new Error("local and central headers differ for " + name);
    }
    const data = bytes.subarray(at + 30 + lnl + lextra, at + 30 + lnl + lextra + size);
    out.push({ name, data, crc, crcOk: crc32(data) === crc, method, flags });
    p += 46 + nl + extra + comment;
  }
  if (p !== end) throw new Error("central directory size is wrong");
  return out;
}

/** The chunks of a PNG: [{ type, data, crcOk }]. */
export function pngChunks(bytes) {
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const out = [];
  for (let p = 8; p + 12 <= bytes.length;) {
    const n = dv.getUint32(p), type = String.fromCharCode(...bytes.subarray(p + 4, p + 8));
    out.push({ type, data: bytes.subarray(p + 8, p + 8 + n), crcOk: crc32(bytes.subarray(p + 4, p + 8 + n)) === dv.getUint32(p + 8 + n) });
    p += 12 + n;
  }
  return out;
}

/**
 * jsdom draws no pictures: give the page an Image that "loads" at once and canvases that hand back a small
 * real PNG (header, one data chunk, end), so the report's PNG path (canvas -> bytes -> pHYs + iTXt -> file)
 * runs. Returns the list of canvases the page turned into PNGs ([{ width, height }]).
 */
export function fakePng(win) {
  const chunk = (type, data) => {
    const o = new Uint8Array(12 + data.length), v = new DataView(o.buffer);
    v.setUint32(0, data.length);
    for (let i = 0; i < 4; i++) o[4 + i] = type.charCodeAt(i);
    o.set(data, 8);
    v.setUint32(8 + data.length, crc32(o.subarray(4, 8 + data.length)));
    return o;
  };
  const ihdr = new Uint8Array(13);
  new DataView(ihdr.buffer).setUint32(0, 1);
  new DataView(ihdr.buffer).setUint32(4, 1);
  ihdr[8] = 8; ihdr[9] = 6;
  const parts = [new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk("IHDR", ihdr), chunk("IDAT", new Uint8Array([0x78, 0x9c, 0x63, 0, 1, 0, 0, 5, 0, 1])), chunk("IEND", new Uint8Array(0))];
  const png = new Uint8Array(parts.reduce((a, b) => a + b.length, 0));
  let at = 0;
  for (const x of parts) { png.set(x, at); at += x.length; }
  const made = [];
  win.Image = class {
    set src(v) { this._src = v; setTimeout(() => this.onload && this.onload(), 0); }
    get src() { return this._src; }
  };
  win.HTMLCanvasElement.prototype.toBlob = function (cb) {
    made.push({ width: this.width, height: this.height });
    cb(new win.Blob([png], { type: "image/png" }));
  };
  return made;
}

/** Wait until `test()` is truthy (the report makes PNGs and zips with callbacks). */
export async function until(test, ms = 8000) {
  const t0 = Date.now();
  for (;;) {
    const v = test();
    if (v) return v;
    if (Date.now() - t0 > ms) throw new Error("timed out waiting");
    await new Promise((r) => setTimeout(r, 10));
  }
}
