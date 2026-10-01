// Figure export (report.js "figure export", D62): the SVG / PNG / Export… buttons of each chart, the export
// dialog (size presets, text, marks, palettes, background, title, legend, names), the style kept in the
// browser and saved / loaded as JSON (untrusted input), PNG with its print size and description, copy as an
// image, and "Export for slides": a store-only .zip that is unzipped here and whose CRCs are checked.
// jsdom has no layout, no canvas and no clipboard: the harness stands in for them (fakePng, stubs below).
import { test } from "node:test";
import assert from "node:assert/strict";

import { loadReport, baseData, withData, blobText, blobBytes, unzip, pngChunks, fakePng, until } from "../lib/harness.mjs";

const BASE = baseData();
const KEY = "ionomos.export.v1";

const lastDownload = (window) => window.__downloads.at(-1);
async function svgOf(window, click) {
  const n = window.__downloads.length;
  click();
  assert.equal(window.__downloads.length, n + 1, "one file downloaded");
  const d = lastDownload(window);
  return { name: d.name, text: await blobText(window, d.blob) };
}
function parse(window, text) {
  const doc = new window.DOMParser().parseFromString(text, "image/svg+xml");
  assert.equal(doc.querySelector("parsererror"), null, "the SVG is well-formed XML");
  return doc.documentElement;
}
function set(document, id, value, type = "change") {
  const el = document.querySelector("#" + id), window = document.defaultView;
  if (el.type === "checkbox") el.checked = value; else el.value = value;
  el.dispatchEvent(new window.Event(type, { bubbles: true }));
}
/** Open the dialog from Options and return a function that downloads the shown figure as SVG. */
function openDialog(document) {
  document.querySelector("#xopen").click();
  const window = document.defaultView;
  return async () => parse(window, (await svgOf(window, () => document.querySelector("#xsvg").click())).text);
}
const texts = (root) => [...root.querySelectorAll("text")].map((t) => t.textContent);
const fills = (root) => [...root.querySelectorAll("[fill]")].map((e) => e.getAttribute("fill"));

test("every chart has SVG, PNG and Export… with an explanation; so does the heatmap", async () => {
  const { document, errors } = await loadReport();
  assert.deepEqual(errors, []);
  for (const host of ["#volcano", "#phist", "#heatmap"]) {
    const tools = document.querySelector(host).querySelector(".tools");
    assert.ok(tools, `${host} has export buttons`);
    assert.deepEqual([...tools.querySelectorAll("button")].map((b) => b.textContent), ["SVG", "PNG", "Export…"]);
    for (const b of tools.querySelectorAll("button")) assert.ok(b.title.length > 20, "each button says what it does");
  }
});

test("SVG of the volcano: the slide size, text as text, colours written out, the cut-offs inside", async () => {
  const { window, document, errors } = await loadReport();
  const { name, text } = await svgOf(window, () => document.querySelector("#volcano .tools button").click());
  assert.deepEqual(errors, []);
  assert.equal(name, "volcano_Drug_vs_DMSO.svg");
  assert.ok(text.startsWith('<?xml version="1.0" encoding="UTF-8"?>'));
  const root = parse(window, text);
  assert.equal(root.getAttribute("width"), "1280");
  assert.equal(root.getAttribute("height"), "720", "a chart that can take any height fills the 16:9 slide");
  assert.match(root.getAttribute("viewBox"), /^0 0 822\.86 462\.86$/, "drawn at 12 units per 9 pt, shown at 14 pt");
  assert.equal(root.getAttribute("font-family"), "Arial, Helvetica, sans-serif", "a named font with fallbacks");
  assert.ok(!/var\(|<style|class=|style=|foreignObject|transparent|cursor/i.test(text), "nothing that needs the page or CSS");
  assert.ok(root.querySelectorAll("text").length > 10, "text stays text");
  for (const f of fills(root)) assert.match(f, /^(#[0-9a-f]{6}|none|rgb\(\d+, ?\d+, ?\d+\))$/, `a plain colour: ${f}`);
  assert.equal(root.querySelector("rect").getAttribute("fill"), "#ffffff", "white background first");
  const t = texts(root);
  assert.equal(t[0], "Drug vs DMSO", "the comparison is the title");
  assert.equal(t[1], "Volcano plot · Fixture_Exp");
  assert.ok(t.some((x) => /^Up \d+$/.test(x)) && t.some((x) => /^Down \d+$/.test(x)) && t.includes("Not significant"), "legend with the counts");
  assert.ok(t.some((x) => /^Fixture_Exp · hits: \|log2FC\| ≥ 1 and adjusted p ≤ 0\.05 · limma moderated t-test/.test(x)), "the cut-offs under the figure");
  assert.equal(root.querySelector("title").textContent, "Volcano plot: Drug vs DMSO");
  const desc = root.querySelector("desc").textContent;
  for (const want of ["Figure: Volcano plot", "Experiment: Fixture_Exp", "Comparison: Drug vs DMSO", "Cut-offs: |log2FC| ≥ 1 and adjusted p ≤ 0.05",
    "Analysis: limma moderated t-test; normalisation: median", "Export style: 16:9 slide (1280 × 720 px), text 14 pt Arial, palette default", "Made by the Ionomos report, 20"]) {
    assert.ok(desc.includes(want), `<desc> says: ${want}`);
  }
  assert.ok(root.querySelectorAll("circle").length > 80, "the points");
  // the page is back as it was: not at the export size, still with its buttons
  assert.notEqual(document.querySelector("#volcano svg").getAttribute("width"), "798");
  assert.ok(document.querySelector("#volcano .tools button"));
});

test("an exported figure carries the cut-offs and filters as they are set, not the saved ones", async () => {
  const { window, document } = await loadReport();
  set(document, "lfc", "2", "input");
  set(document, "adj", false);
  set(document, "hideimp", true);
  assert.match(document.querySelector("#viewnote").textContent, /^Hits here: \|log2FC\| ≥ 2 and p ≤ 0\.05 \(4-fold or more\); imputation-driven hits ignored · limma/);
  const root = parse(window, (await svgOf(window, () => document.querySelector("#volcano .tools button").click())).text);
  assert.match(root.querySelector("desc").textContent, /Cut-offs: \|log2FC\| ≥ 2 and p ≤ 0\.05; imputation-driven hits ignored/);
  // the heatmap rests on the saved cut-offs and says so
  const hm = parse(window, (await svgOf(window, () => document.querySelector("#heatmap .tools button").click())).text);
  assert.match(hm.querySelector("desc").textContent, /Cut-offs: \|log2FC\| ≥ 1 and adjusted p ≤ 0\.05 \(the report's saved cut-offs\)/);
});

test("the heatmap (a canvas on screen) exports as SVG cells, with names when they fit", async () => {
  const { window, document, errors } = await loadReport();
  const { name, text } = await svgOf(window, () => document.querySelector("#heatmap .tools button").click());
  assert.deepEqual(errors, []);
  assert.equal(name, "heatmap.svg");
  const root = parse(window, text), hm = BASE.qc.heatmap;
  assert.ok(root.querySelectorAll("rect").length >= hm.rows.length * hm.cols.length, "a cell per value");
  assert.ok(texts(root).includes(BASE.samples[hm.cols[0]]), "sample names on top");
  assert.ok(texts(root).some((t) => /^below the protein's mean$/.test(t)), "legend");
  assert.ok(texts(root).includes("DMSO") && texts(root).includes("Drug"), "the conditions of the colour strip");
});

test("the dialog: figures listed, preview, and every control changes the file", async () => {
  const { window, document, errors } = await loadReport();
  const svg = openDialog(document);
  const dlg = document.querySelector("#xdlg");
  assert.equal(dlg.hidden, false);
  assert.equal(dlg.querySelector("[role=dialog]").getAttribute("aria-modal"), "true");
  const figs = [...document.querySelectorAll("#xfig option")].map((o) => o.textContent);
  for (const want of ["Volcano: Drug vs DMSO", "p-values: Drug vs DMSO", "Heatmap of significant features", "QC: PCA", "QC: Correlation", "QC: Identifications"]) assert.ok(figs.includes(want), `figure listed: ${want}`);
  assert.ok(!figs.some((f) => /scorecard/i.test(f)), "the scorecard is a table, not a figure");
  assert.match(document.querySelector("#xprev img").getAttribute("src"), /^data:image\/svg\+xml;charset=utf-8,%3C%3Fxml/, "a preview, as a picture");
  assert.match(document.querySelector("#xinfo").textContent, /^1280 × 720 px · text 14 pt · \|log2FC\| ≥ 1/);
  assert.match(document.querySelector("#xpx").textContent, /PNG: 2560 × 1440 px/);
  for (const el of dlg.querySelectorAll("label.ctl, .chips > button, .filebtn")) assert.ok(el.title, `explained: ${el.textContent.trim().slice(0, 30)}`);
  assert.equal(document.querySelector("#xcustom").hidden, true, "own colours only with the custom palette");
  assert.equal(document.querySelector("#xw").disabled, true, "width and height only with the custom size");

  // size presets set the text size that suits them; mm sizes are written in mm
  set(document, "xsize", "col1");
  assert.equal(document.querySelector("#xfont").value, "7");
  let root = await svg();
  assert.equal(root.getAttribute("width"), "85mm");
  assert.equal(root.getAttribute("height"), "70mm");
  set(document, "xsize", "slide43");
  root = await svg();
  assert.deepEqual([root.getAttribute("width"), root.getAttribute("height")], ["960", "720"]);
  set(document, "xsize", "custom");
  assert.equal(document.querySelector("#xw").disabled, false);
  set(document, "xw", "900"); set(document, "xh", "500"); set(document, "xfont", "10");
  root = await svg();
  assert.deepEqual([root.getAttribute("width"), root.getAttribute("height")], ["900", "500"]);
  set(document, "xunit", "mm"); set(document, "xw", "120"); set(document, "xh", "90");
  root = await svg();
  assert.deepEqual([root.getAttribute("width"), root.getAttribute("height")], ["120mm", "90mm"]);
  set(document, "xsize", "slide169");

  // font
  set(document, "xfam", "Times New Roman");
  assert.equal((await svg()).getAttribute("font-family"), "'Times New Roman', Times, serif");
  set(document, "xfam", "other");
  assert.equal(document.querySelector("#xfamtext").hidden, false);
  set(document, "xfamtext", "Source Sans 3");
  assert.equal((await svg()).getAttribute("font-family"), "'Source Sans 3', Arial, Helvetica, sans-serif");
  set(document, "xfam", "Arial");

  // palettes
  const up = (r) => [...r.querySelectorAll("circle")].map((c) => c.getAttribute("fill"));
  assert.ok(up(await svg()).includes("#e34948"));
  set(document, "xpal", "colorblind");
  root = await svg();
  assert.ok(up(root).includes("#d55e00") && up(root).includes("#0072b2") && !up(root).includes("#e34948"), "Okabe-Ito up / down");
  set(document, "xpal", "grey");
  root = await svg();
  for (const f of fills(root)) if (f[0] === "#") assert.ok(f.slice(1, 3) === f.slice(3, 5) && f.slice(3, 5) === f.slice(5, 7), `grey only: ${f}`);
  set(document, "xpal", "custom");
  assert.equal(document.querySelector("#xcustom").hidden, false);
  set(document, "xup", "#123456"); set(document, "xdown", "#abcdef"); set(document, "xns", "#777777");
  root = await svg();
  assert.ok(up(root).includes("#123456") && up(root).includes("#abcdef") && up(root).includes("#777777"));
  set(document, "xpal", "default");

  // background
  set(document, "xbg", "dark");
  root = await svg();
  assert.equal(root.querySelector("rect").getAttribute("fill"), "#1a1a19");
  assert.ok(up(root).includes("#e66767"), "the dark palette's up colour");
  set(document, "xbg", "transparent");
  root = await svg();
  assert.notEqual(root.firstElementChild.nextElementSibling.nextElementSibling.tagName, "rect", "no background rectangle");
  assert.ok(document.querySelector("#xprev").classList.contains("clear"));
  set(document, "xbg", "light");

  // title, subtitle, legend, the cut-offs line
  set(document, "xtitletext", "My <b>title</b> & co", "input");
  set(document, "xsubtext", "a subtitle", "input");
  root = await svg();
  assert.deepEqual(texts(root).slice(0, 2), ["My <b>title</b> & co", "a subtitle"], "typed text is text");
  assert.equal(root.querySelector("b"), null);
  set(document, "xtitle", false); set(document, "xsub", false); set(document, "xlegend", false); set(document, "xnote", false);
  root = await svg();
  assert.ok(!texts(root).some((t) => /My <b>|a subtitle|^Up \d+$|hits: \|log2FC/.test(t)), "all four switched off");
  assert.match(root.querySelector("desc").textContent, /Cut-offs: \|log2FC\| ≥ 1/, "the file still records the cut-offs");
  assert.deepEqual([root.getAttribute("width"), root.getAttribute("height")], ["1280", "720"], "the plot takes the room");
  set(document, "xtitle", true); set(document, "xsub", true); set(document, "xlegend", true); set(document, "xnote", true);

  // names on the plot
  const c = BASE.comps[0], hits = BASE.f.label.filter((_, i) => c.q[i] != null && c.q[i] <= 0.05 && Math.abs(c.fc[i]) >= 1);
  const named = (r) => texts(r).filter((t) => hits.includes(t)).length;
  assert.ok(named(await svg()) >= 3, "the top hits are named as in the report");
  set(document, "xlabels", "none");
  assert.equal(named(await svg()), 0);
  set(document, "xlabels", "top");
  assert.equal(document.querySelector("#xnlab").disabled, false);
  set(document, "xnlab", "2");
  assert.equal(named(await svg()), 2);
  set(document, "xlabels", "screen");

  // lines and points
  const r0 = +[...(await svg()).querySelectorAll("circle")].at(-1).getAttribute("r");
  set(document, "xpoint", "2"); set(document, "xline", "3");
  root = await svg();
  assert.equal(+[...root.querySelectorAll("circle")].at(-1).getAttribute("r"), r0 * 2);
  assert.ok([...root.querySelectorAll("line[stroke-dasharray]")].every((l) => l.getAttribute("stroke-width") === "3"), "lines three times as thick");

  // another figure
  set(document, "xfig", String(figs.indexOf("QC: PCA")));
  root = await svg();
  assert.equal(lastDownload(window).name, "PCA.svg");
  assert.equal(texts(root)[0], "PCA of the samples");
  assert.ok(texts(root).includes("DMSO") && texts(root).includes("Drug"), "the condition legend");
  assert.equal(root.querySelectorAll("circle").length, BASE.samples.length * 1 + 2, "a point per sample and two legend dots");
  assert.deepEqual(errors, []);

  // closing puts the page back; Escape closes too
  document.querySelector("#xclose").click();
  assert.equal(dlg.hidden, true);
  assert.ok(document.querySelector("#qc .tabs button.on"), "the QC tab that was open is still open");
  document.querySelector("#xopen").click();
  document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape" }));
  assert.equal(dlg.hidden, true);
});

test("the style is kept in the browser, starts from the lab's defaults and resets to them", async () => {
  const lab = { size: "slide43", palette: "colorblind", font_family: "Calibri", font_pt: 16, figures: ["volcano"] };
  let page = await loadReport({ data: withData(BASE, { exportDefaults: lab }), storage: {} });
  let { window, document } = page;
  let svg = openDialog(document);
  assert.equal(document.querySelector("#xsize").value, "slide43", "the lab's size");
  assert.equal(document.querySelector("#xpal").value, "colorblind");
  assert.equal(document.querySelector("#xfam").value, "Calibri");
  assert.equal(document.querySelector("#xfont").value, "16");
  assert.equal(window.localStorage.getItem(KEY), null, "nothing is stored until something is changed");
  set(document, "xpal", "grey"); set(document, "xres", "d300"); set(document, "xzipfmt", "svg");
  const stored = JSON.parse(window.localStorage.getItem(KEY));
  assert.equal(stored.palette, "grey");
  assert.equal(stored.png_dpi, 300);
  assert.equal(stored.zip_format, "svg");
  // the next report opened in this browser uses it, whatever its own lab defaults are
  page = await loadReport({ storage: { [KEY]: stored } });
  ({ window, document } = page);
  svg = openDialog(document);
  assert.equal(document.querySelector("#xpal").value, "grey");
  assert.equal(document.querySelector("#xsize").value, "slide43");
  assert.equal(document.querySelector("#xres").value, "d300");
  assert.equal((await svg()).getAttribute("width"), "960");
  // reset: back to this report's lab defaults (here the built-in ones), and forgotten
  document.querySelector("#xreset").click();
  assert.equal(document.querySelector("#xpal").value, "default");
  assert.equal(document.querySelector("#xsize").value, "slide169");
  assert.equal(JSON.parse(window.localStorage.getItem(KEY)), null);
  assert.match(document.querySelector("#xmsg").textContent, /lab defaults/);
  assert.equal((await svg()).getAttribute("width"), "1280");
  // rubbish in storage or in the lab defaults is ignored key by key
  page = await loadReport({ data: withData(BASE, { exportDefaults: "nonsense" }), storage: { [KEY]: { size: "poster", font_pt: 9000, palette: "grey", up: "red", junk: 1 } } });
  assert.deepEqual(page.errors, []);
  openDialog(page.document);
  assert.equal(page.document.querySelector("#xsize").value, "slide169");
  assert.equal(page.document.querySelector("#xfont").value, "14");
  assert.equal(page.document.querySelector("#xpal").value, "grey", "the valid key is kept");
});

test("save style / load style: a small JSON; a loaded file is untrusted and checked key by key", async () => {
  const { window, document, errors } = await loadReport({ storage: {} });
  const svg = openDialog(document);
  set(document, "xpal", "colorblind"); set(document, "xsize", "half");
  document.querySelector("#xsave").click();
  assert.equal(lastDownload(window).name, "ionomos_export_style.json");
  const saved = JSON.parse(await blobText(window, lastDownload(window).blob));
  assert.equal(saved.ionomos_export_style, 1);
  assert.equal(saved.palette, "colorblind");
  assert.equal(saved.size, "half");
  assert.equal(saved.font_pt, 12);
  assert.deepEqual(Object.keys(saved).sort(), ["background", "down", "figures", "font_family", "font_pt", "height", "ionomos_export_style", "label_count", "labels", "legend", "line_scale",
    "neutral", "note", "palette", "png_dpi", "png_scale", "point_scale", "size", "subtitle", "title", "unit", "up", "width", "zip_format"]);
  const load = async (text, name = "style.json") => {
    const input = document.querySelector("#xload");
    Object.defineProperty(input, "files", { configurable: true, value: [new window.File([text], name, { type: "application/json" })] });
    input.dispatchEvent(new window.Event("change", { bubbles: true }));
    await until(() => document.querySelector("#xmsg").textContent !== "…");
    return document.querySelector("#xmsg").textContent;
  };
  const before = window.localStorage.getItem(KEY);
  const refuse = async (text, re) => {
    document.querySelector("#xmsg").textContent = "…";
    assert.match(await load(text), re);
    assert.equal(window.localStorage.getItem(KEY), before, "nothing changed");
  };
  await refuse("not json {", /not JSON; nothing was changed/);
  await refuse(JSON.stringify({ palette: "grey" }), /not an Ionomos export style; nothing was changed/);
  await refuse(JSON.stringify([1, 2]), /not an Ionomos export style/);
  await refuse("null", /not an Ionomos export style/);
  await refuse(JSON.stringify({ ionomos_export_style: 1, pad: "x".repeat(30000) }), /too large/);
  // a hostile file: only the valid keys are taken, the rest is named as text
  const hostile = '{"ionomos_export_style":1,"palette":"grey","size":"<script>window.__xss=1</script>","font_family":"x\\" onload=\\"window.__xss=1","up":"javascript:window.__xss=1",' +
    '"font_pt":"14","title":"yes","width":1e99,"figures":["volcano","<img src=x onerror=window.__xss=1>"],"__proto__":{"polluted":true},"<img src=x onerror=window.__xss=1>":1,"background":"dark"}';
  document.querySelector("#xmsg").textContent = "…";
  const msg = await load(hostile);
  assert.match(msg, /^Style loaded\. Not understood and left out: /);
  for (const k of ["size", "font_family", "up", "font_pt", "title", "width", "figures", "__proto__", "<img src=x onerror=window.__xs"]) assert.ok(msg.includes(k), `left out: ${k}`);
  assert.equal(document.querySelector("#xmsg").children.length, 0, "the message is text");
  assert.equal(document.querySelector("#xdlg img[src='x']"), null);
  assert.equal(window.__xss, undefined);
  assert.equal({}.polluted, undefined);
  assert.equal(window.Object.prototype.polluted, undefined, "no prototype pollution");
  assert.equal(document.querySelector("#xpal").value, "grey");
  assert.equal(document.querySelector("#xbg").value, "dark");
  assert.equal(document.querySelector("#xsize").value, "slide169", "a bad value falls back to the lab default");
  assert.equal(document.querySelector("#xfam").value, "Arial");
  const now = JSON.parse(window.localStorage.getItem(KEY));
  assert.equal(now.up, "#e34948");
  assert.deepEqual(now.figures, []);
  const text = (await svgOf(window, () => document.querySelector("#xsvg").click())).text;
  assert.ok(!/onload|javascript:|<script|<img/i.test(text), "nothing of it reaches a file");
  parse(window, text);
  void svg;
  assert.deepEqual(errors, []);
});

test("PNG: the pixel size follows the resolution, the file carries its print size and the cut-offs", async () => {
  const { window, document, errors } = await loadReport();
  const made = fakePng(window);
  document.querySelector("#volcano .tools button:nth-child(2)").click();
  await until(() => window.__downloads.length === 1);
  assert.equal(lastDownload(window).name, "volcano_Drug_vs_DMSO.png");
  assert.deepEqual(made[0], { width: 2560, height: 1440 }, "2× of a 1280 × 720 slide");
  let chunks = pngChunks(await blobBytes(window, lastDownload(window).blob));
  assert.deepEqual(chunks.map((c) => c.type), ["IHDR", "pHYs", "iTXt", "IDAT", "IEND"]);
  assert.ok(chunks.every((c) => c.crcOk), "every chunk's CRC is right");
  const phys = (c) => new DataView(c.data.buffer, c.data.byteOffset, 9);
  assert.equal(phys(chunks[1]).getUint32(0), Math.round(192 / 0.0254), "192 dots per inch: 1280 px wide prints as a 13.3 inch slide");
  assert.equal(phys(chunks[1]).getUint8(8), 1, "per metre");
  const itxt = new TextDecoder().decode(chunks[2].data);
  assert.ok(itxt.startsWith("Description\0\0\0\0\0Figure: Volcano plot\nExperiment: Fixture_Exp\nComparison: Drug vs DMSO\nCut-offs: |log2FC| ≥ 1 and adjusted p ≤ 0.05"));
  // 300 dpi for print, from the dialog
  document.querySelector("#xopen").click();
  set(document, "xsize", "col2"); set(document, "xres", "d300");
  assert.match(document.querySelector("#xpx").textContent, /PNG: 2126 × 1299 px/);
  document.querySelector("#xpng").click();
  await until(() => window.__downloads.length === 2);
  assert.deepEqual(made.at(-1), { width: 2126, height: 1299 }, "180 mm × 110 mm at 300 dpi");
  chunks = pngChunks(await blobBytes(window, lastDownload(window).blob));
  assert.equal(phys(chunks[1]).getUint32(0), Math.round(300 / 0.0254));
  assert.deepEqual(errors, []);
});

test("copy as an image: a PNG on the clipboard where the browser allows it, a plain message where not", async () => {
  const { window, document } = await loadReport();
  fakePng(window);
  document.querySelector("#xopen").click();
  document.querySelector("#xcopy").click();
  await until(() => document.querySelector("#xmsg").textContent);
  assert.match(document.querySelector("#xmsg").textContent, /does not let a page copy pictures; use Download PNG/);
  // with a clipboard
  const written = [];
  window.ClipboardItem = class { constructor(o) { this.o = o; } };
  Object.defineProperty(window.navigator, "clipboard", { configurable: true, value: { write: async (items) => { written.push(await items[0].o["image/png"]); } } });
  document.querySelector("#xmsg").textContent = "";
  document.querySelector("#xcopy").click();
  await until(() => document.querySelector("#xmsg").textContent);
  assert.match(document.querySelector("#xmsg").textContent, /Copied: paste it into a slide/);
  assert.equal(written.length, 1);
  assert.equal(written[0].type, "image/png");
  assert.deepEqual(pngChunks(await blobBytes(window, written[0])).map((c) => c.type), ["IHDR", "pHYs", "iTXt", "IDAT", "IEND"]);
});

test("Export for slides: one click, a store-only .zip with every figure, the tables, the style and a README", async () => {
  const { window, document, errors } = await loadReport({ storage: { [KEY]: { zip_format: "svg" } } });
  const comp = document.querySelector("#comp").value, tab = document.querySelector("#qc .tabs button.on").dataset.k;
  document.querySelector("#slides").click();
  await until(() => window.__downloads.length === 1);
  assert.deepEqual(errors, []);
  const dl = lastDownload(window);
  assert.equal(dl.name, "Fixture_Exp_figures.zip");
  assert.equal(dl.blob.type, "application/zip");
  const bytes = await blobBytes(window, dl.blob);
  assert.deepEqual([...bytes.subarray(0, 4)], [0x50, 0x4b, 3, 4], "starts with a local file header");
  const files = unzip(bytes);
  assert.ok(files.every((f) => f.crcOk), "every entry's CRC-32 matches its bytes");
  assert.ok(files.every((f) => f.method === 0), "stored, not compressed");
  assert.ok(files.every((f) => f.flags === 0x0800), "names flagged as UTF-8");
  const names = files.map((f) => f.name);
  assert.equal(new Set(names.map((n) => n.toLowerCase())).size, names.length, "no two names differ only in case");
  for (const n of names) {
    assert.match(n, /^Fixture_Exp_figures\/((figures|tables)\/)?[A-Za-z0-9][A-Za-z0-9._+-]*\.(svg|csv|json|txt)$/, `safe on Windows: ${n}`);
    assert.ok(n.length < 140);
  }
  const figs = names.filter((n) => n.includes("/figures/")).map((n) => n.replace(/^.*\/\d\d_/, ""));
  for (const want of ["volcano_Drug_vs_DMSO.svg", "pvalues_Drug_vs_DMSO.svg", "heatmap.svg", "PCA.svg", "correlation.svg", "distributions.svg", "identifications.svg", "cv_DMSO.svg", "cv_Drug.svg"]) {
    assert.ok(figs.includes(want), `in the zip: ${want} (has ${figs.join(", ")})`);
  }
  assert.ok(names.some((n) => /\/figures\/01_volcano_Drug_vs_DMSO\.svg$/.test(n)), "numbered, the volcano first");
  const text = (n) => new TextDecoder("utf-8", { ignoreBOM: true }).decode(files.find((f) => f.name.endsWith(n)).data);
  for (const f of files.filter((x) => x.name.endsWith(".svg"))) parse(window, new TextDecoder().decode(f.data));
  // tables
  const csv = text("tables/results_Drug_vs_DMSO.csv").replace(/^﻿/, "").split("\n");
  assert.match(csv[0], /^"id","label","description","log2fc","ci_low","ci_high","p","adj_p","significant","imputation_driven"/);
  assert.equal(csv.filter(Boolean).length - 1, BASE.comps[0].p.filter((p) => p != null).length, "every tested feature, not only the page shown");
  assert.match(text("tables/samples.csv"), /"sample","condition","replicate","status"/);
  // the style, ready to load elsewhere or to hand to `ionomos export --style`
  const style = JSON.parse(text("export_style.json"));
  assert.equal(style.ionomos_export_style, 1);
  assert.equal(style.zip_format, "svg");
  // the README lists each file with what it is and the cut-offs
  const readme = text("README.txt");
  assert.ok(readme.startsWith("﻿Figures and tables from the Ionomos report\r\n"), "readable in Notepad: a BOM and CRLF");
  assert.match(readme, /Experiment: {2}Fixture_Exp\r\n/);
  assert.match(readme, /In the report as it was open[^\r]*\|log2FC\| ≥ 1 and adjusted p ≤ 0\.05\r\n/);
  assert.match(readme, /Saved with the report[^\r]*\|log2FC\| ≥ 1 and adjusted p ≤ 0\.05\r\n/);
  assert.match(readme, /16:9 slide \(1280 × 720 px\), text 14 pt Arial, palette default, light background/);
  for (const n of names) if (!n.endsWith("README.txt")) assert.ok(readme.includes("  " + n.replace("Fixture_Exp_figures/", "") + " "), `README lists ${n}`);
  assert.match(readme, /figures\/01_volcano_Drug_vs_DMSO\.svg +Volcano plot, Drug vs DMSO\. Cut-offs: \|log2FC\| ≥ 1 and adjusted p ≤ 0\.05\r\n/);
  assert.match(readme, /heatmap\.svg +Heatmap of significant features\. Cut-offs: [^\r]*\(the report's saved cut-offs\)/);
  // the page is as it was
  assert.equal(document.querySelector("#comp").value, comp);
  assert.equal(document.querySelector("#qc .tabs button.on").dataset.k, tab);
  assert.match(document.querySelector("#slidesmsg").textContent, /\d+ figures and the tables are in the \.zip/);
});

test("the zip holds PNG too when asked (the default), and says so when the browser cannot make them", async () => {
  const page = await loadReport();
  const made = fakePng(page.window);
  page.document.querySelector("#opts").click();
  page.document.querySelector("#xzipnow").click();
  await until(() => page.window.__downloads.length === 1);
  const files = unzip(await blobBytes(page.window, lastDownload(page.window).blob));
  const svgs = files.filter((f) => f.name.endsWith(".svg")), pngs = files.filter((f) => f.name.endsWith(".png"));
  assert.ok(svgs.length >= 10 && pngs.length === svgs.length, "an SVG and a PNG of each figure");
  assert.equal(made.length, pngs.length);
  assert.ok(files.every((f) => f.crcOk));
  for (const f of pngs) assert.deepEqual(pngChunks(f.data).map((c) => c.type), ["IHDR", "pHYs", "iTXt", "IDAT", "IEND"]);
  assert.match(page.document.querySelector("#optmsg").textContent, /figures and the tables/);
  // PNG only, in a browser whose canvas gives nothing: the figure is still there, as SVG
  const p2 = await loadReport({ storage: { [KEY]: { zip_format: "png" } } });
  fakePng(p2.window);
  p2.window.HTMLCanvasElement.prototype.toBlob = (cb) => cb(null);
  p2.document.querySelector("#slides").click();
  await until(() => p2.window.__downloads.length === 1);
  const f2 = unzip(await blobBytes(p2.window, lastDownload(p2.window).blob));
  assert.ok(f2.some((f) => f.name.endsWith("01_volcano_Drug_vs_DMSO.svg")) && !f2.some((f) => f.name.endsWith(".png")));
  assert.match(new TextDecoder().decode(f2.find((f) => f.name.endsWith("README.txt")).data), /SVG: this browser could not make the PNG/);
});

test("a low-confidence comparison says so in its figure, whatever is switched off", async () => {
  const data = withData(BASE, { comps: [withData(BASE.comps[0], { conf: "low", confNote: "one sample" })] });
  const { window, document } = await loadReport({ data, storage: { [KEY]: { title: false, subtitle: false, legend: false, note: false } } });
  const root = parse(window, (await svgOf(window, () => document.querySelector("#volcano .tools button").click())).text);
  assert.deepEqual(texts(root).slice(0, 1), ["LOW CONFIDENCE: a group has one sample; p-values borrowed"]);
  assert.match(root.querySelector("desc").textContent, /LOW CONFIDENCE/);
});

test("a chart with its own redraw (a protein's values) exports through the dialog too", async () => {
  const { window, document, errors } = await loadReport();
  document.querySelector("#table tbody tr[data-i]").click();
  const strip = document.querySelector("#strip");
  assert.ok(strip.querySelector(".tools"), "the values chart has export buttons");
  strip.querySelector(".tools button:nth-child(3)").click();
  assert.equal(document.querySelector("#xdlg").hidden, false);
  assert.match(document.querySelector("#xfig option").textContent, /^This chart: Values per condition/);
  const { name, text } = await svgOf(window, () => document.querySelector("#xsvg").click());
  assert.match(name, /^values_.+\.svg$/);
  const root = parse(window, text);
  assert.equal(texts(root)[0], "Values per condition");
  assert.ok(document.querySelector("#strip svg"), "the chart is drawn again on the page");
  assert.deepEqual(errors, []);
});

test("friendlier options: every control explained, the cut-offs in words, reset to lab defaults", async () => {
  const { document, errors } = await loadReport();
  for (const el of document.querySelectorAll("#differential-body > .bar label.ctl, #optpanel label.ctl, #opts, #reset, #optreset, #xopen, #xzipnow, #slides")) {
    assert.ok((el.title || "").length > 15, `a tooltip on: ${el.textContent.trim().slice(0, 30) || el.id}`);
  }
  for (const b of document.querySelectorAll("#qc > .tabs button")) assert.ok(b.title.length > 15, `QC tab ${b.textContent} says what it shows`);
  assert.deepEqual([...document.querySelectorAll("#optpanel h4")].map((h) => h.firstChild.textContent), ["Plot", "Hits", "Highlight groups", "Figures for slides"]);
  for (const id of ["report.plotoptions", "report.hitfilters", "report.groups", "report.export"]) assert.ok(document.querySelector(`#optpanel h4 > button.qhelp[data-help='${id}']`), `? for ${id}`);
  assert.match(document.querySelector("#viewnote").textContent, /^Hits here: \|log2FC\| ≥ 1 and adjusted p ≤ 0\.05 \(2-fold or more\) · limma moderated t-test/);
  assert.equal(document.querySelector("#foldhint").textContent, "= 2-fold");
  assert.equal(document.querySelector("#ptsizev").textContent, "×1.0");
  assert.equal(document.querySelector("#labsizev").textContent, "11.5 px");
  // change everything, then reset
  set(document, "lfc", "0.58", "input"); set(document, "alpha", "0.01", "input"); set(document, "adj", false);
  set(document, "labels", "3", "input"); set(document, "ptsize", "2", "input"); set(document, "labsize", "16", "input");
  set(document, "labmatch", false); set(document, "lines", false); set(document, "markonoff", false); set(document, "hideimp", true); set(document, "minpep", "2", "input");
  assert.equal(document.querySelector("#foldhint").textContent, "= 1.49-fold");
  assert.equal(document.querySelector("#ptsizev").textContent, "×2.0");
  assert.match(document.querySelector("#viewnote").textContent, /\|log2FC\| ≥ 0\.58 and p ≤ 0\.01 .*imputation-driven hits ignored, hits need ≥ 2/);
  assert.equal(document.querySelector("#volcano svg line[stroke-dasharray]"), null, "cut-off lines off");
  document.querySelector("#optreset").click();
  assert.equal(document.querySelector("#lfc").value, String(BASE.settings.log2fc));
  assert.equal(document.querySelector("#alpha").value, String(BASE.settings.alpha));
  assert.equal(document.querySelector("#adj").checked, true);
  assert.equal(document.querySelector("#labels").value, String(BASE.settings.top_labels));
  assert.equal(document.querySelector("#ptsize").value, "1");
  assert.equal(document.querySelector("#labsize").value, "11.5");
  assert.equal(document.querySelector("#hideimp").checked, false);
  assert.equal(document.querySelector("#minpep").value, "0");
  for (const id of ["labmatch", "lines", "markonoff"]) assert.equal(document.querySelector("#" + id).checked, true);
  assert.ok(document.querySelector("#volcano svg line[stroke-dasharray]"), "cut-off lines back");
  assert.match(document.querySelector("#viewnote").textContent, /^Hits here: \|log2FC\| ≥ 1 and adjusted p ≤ 0\.05 \(2-fold or more\) · /);
  assert.equal(document.querySelector("#cutnote").textContent, "");
  assert.match(document.querySelector("#optmsg").textContent, /lab defaults/);
  assert.deepEqual(errors, []);
});

test("a report without comparisons still exports its QC figures; one without anything says so", async () => {
  const { window, document, errors } = await loadReport({ data: withData(BASE, { comps: [] }), storage: { [KEY]: { zip_format: "svg" } } });
  document.querySelector("#slides").click();
  await until(() => window.__downloads.length === 1);
  const names = unzip(await blobBytes(window, lastDownload(window).blob)).map((f) => f.name);
  assert.ok(names.some((n) => n.endsWith("_PCA.svg")) && !names.some((n) => /volcano/.test(n)));
  assert.deepEqual(errors, []);
});

test("the page theme does not leak into a figure; 10,000 features export in good time", async () => {
  const nF = 10000, nS = 50;
  const fc = Array.from({ length: nF }, (_, i) => (((i * 37) % 200) - 100) / 40);
  const p = Array.from({ length: nF }, (_, i) => (i % 50 === 0 ? 1e-9 : 0.5));
  const data = withData(BASE, {
    v: Array.from({ length: nF }, (_, i) => Array.from({ length: nS }, (_, j) => 12 + ((i * 7 + j * 13) % 97) / 10)),
    samples: Array.from({ length: nS }, (_, j) => `s${j + 1}`), cond: Array.from({ length: nS }, (_, j) => (j % 2 ? "Drug" : "DMSO")), rep: Array.from({ length: nS }, (_, j) => (j >> 1) + 1),
    f: { id: Array.from({ length: nF }, (_, i) => `prot${i}`), label: Array.from({ length: nF }, (_, i) => `GENE${i}`), desc: Array.from({ length: nF }, () => "") },
    imp: null, qc: {}, enr: [], gsea: [],
    comps: [{ name: "Drug vs DMSO", slug: "Drug_vs_DMSO", t1: "Drug", t2: "DMSO", fc, p, q: p.map((x) => (x < 0.5 ? 1e-8 : 0.6)), ciL: fc, ciR: fc, nt: fc.map(() => 25), nc: fc.map(() => 25), a: fc.map(() => 12), onoff: [], conf: "" }],
  });
  const { window, document, errors } = await loadReport({ data, storage: { [KEY]: { zip_format: "svg" } } });
  document.documentElement.setAttribute("data-theme", "dark");
  const t0 = Date.now();
  const root = parse(window, (await svgOf(window, () => document.querySelector("#volcano .tools button").click())).text);
  assert.equal(root.querySelector("rect").getAttribute("fill"), "#ffffff", "a light figure from a dark page");
  assert.equal(root.querySelectorAll("circle").length, nF + 3, "every feature and the three legend dots");
  document.querySelector("#slides").click();
  await until(() => window.__downloads.length === 2, 20000);
  const files = unzip(await blobBytes(window, lastDownload(window).blob));
  assert.ok(files.every((f) => f.crcOk));
  assert.ok(files.some((f) => f.name.endsWith("tables/results_Drug_vs_DMSO.csv") && f.data.length > 1e6), "the full table");
  assert.ok(Date.now() - t0 < 20000, `took ${Date.now() - t0} ms`);
  assert.deepEqual(errors, []);
});
