// Every export button on the page (round-4 audit): each chart's SVG and PNG on every QC tab and section, the
// dialog's buttons, the table's CSV and copy buttons, the groups' .gmt, the zip from the top bar and from
// Options. Each file must be named safely and parse; a press must never throw.
import { test } from "node:test";
import assert from "node:assert/strict";

import { loadReport, baseData, withData, blobText, blobBytes, fakePng, until } from "../lib/harness.mjs";

const BASE = baseData();
const SAFE = /^[A-Za-z0-9][A-Za-z0-9._+-]*\.(svg|png|csv|gmt|json|zip)$/;

function parseSvg(window, text, what) {
  const doc = new window.DOMParser().parseFromString(text, "image/svg+xml");
  assert.equal(doc.querySelector("parsererror"), null, `${what}: well-formed SVG`);
  assert.equal(doc.documentElement.tagName, "svg");
  return doc.documentElement;
}

/** Press SVG and PNG under every chart that is on the page now; returns how many charts were done. */
async function pressAll(window, document, where) {
  let n = 0;
  for (const tools of document.querySelectorAll(where + " .tools")) {
    const label = tools.parentElement.id || tools.parentElement.className;
    const svgB = () => [...tools.parentElement.querySelectorAll(":scope > .tools button")].find((b) => b.textContent === "SVG");
    const pngB = () => [...tools.parentElement.querySelectorAll(":scope > .tools button")].find((b) => b.textContent === "PNG");
    const host = tools.parentElement;
    if (!svgB()) continue;
    const k = window.__downloads.length;
    svgB().click();
    assert.equal(window.__downloads.length, k + 1, `${label}: SVG downloads one file`);
    const d = window.__downloads.at(-1);
    assert.match(d.name, SAFE, `${label}: ${d.name}`);
    parseSvg(window, await blobText(window, d.blob), label);
    // the chart's buttons were drawn again by the export; press PNG on the new ones
    const p = [...host.querySelectorAll(":scope > .tools button")].find((b) => b.textContent === "PNG");
    if (p) {
      p.click();
      await until(() => window.__downloads.length === k + 2);
      const png = window.__downloads.at(-1);
      assert.match(png.name, /\.png$/);
      const bytes = await blobBytes(window, png.blob);
      assert.deepEqual([...bytes.subarray(1, 4)], [0x50, 0x4e, 0x47], `${label}: a PNG`);
    }
    n++;
  }
  return n;
}

test("every chart's SVG and PNG, on every QC tab and section, gives a safe, well-formed file", async () => {
  const { window, document, errors } = await loadReport({ before: (w) => fakePng(w) });
  let n = await pressAll(window, document, "#differential, #compare, #heat, #enrichment");
  const tabs = [...document.querySelectorAll("#qc .tabs button")].map((b) => b.dataset.k);
  for (const k of tabs) {
    document.querySelector(`#qc .tabs button[data-k='${k}']`).click();
    n += await pressAll(window, document, "#qc");
  }
  // the enrichment's other tab
  const other = [...document.querySelectorAll("#enrich .tabs button")].find((b) => !b.classList.contains("on"));
  if (other) { other.click(); n += await pressAll(window, document, "#enrichment"); }
  // a feature's values (its own redraw)
  document.querySelector("#table tbody tr[data-i]").click();
  n += await pressAll(window, document, "#detail");
  assert.ok(n >= 15, `${n} charts exported`);
  assert.deepEqual(errors, []);
});

test("the table's CSV, copy buttons, the search's buttons, the groups' .gmt and the on/off copy", async () => {
  const { window, document, errors } = await loadReport();
  const copied = [];
  Object.defineProperty(window.navigator, "clipboard", { value: { writeText: (s) => { copied.push(s); return Promise.resolve(); } }, configurable: true });
  document.querySelector("#csv").click();
  const csv = window.__downloads.at(-1);
  assert.match(csv.name, SAFE);
  assert.match(await blobText(window, csv.blob), /^"id","label"/);
  document.querySelector("#copyup").click();
  document.querySelector("#copydown").click();
  const box = document.querySelector("#search");
  box.value = "ACTB, GAPDH";
  box.dispatchEvent(new window.Event("input", { bubbles: true }));
  document.querySelector("#sq-copy").click();
  document.querySelector("#sq-pin").click();
  document.querySelector("#sq-group").click();
  assert.equal(document.querySelector("#optpanel").hidden, false, "Save as group opens the options");
  document.querySelector("#gadd").click();
  document.querySelector("#gexport").click();
  assert.match(window.__downloads.at(-1).name, /^ionomos_groups\.gmt$/);
  const oo = document.querySelector("#oocopy");
  if (oo) oo.click();
  document.querySelector("#copybtn") && document.querySelector("#copybtn").click();
  await new Promise((r) => setTimeout(r, 0));
  assert.ok(copied.length >= 3, `${copied.length} copies`);
  assert.ok(copied.some((s) => /ACTB\nGAPDH|GAPDH\nACTB/.test(s)), "the search's names");
  assert.deepEqual(errors, []);
});

test("the export dialog's buttons: SVG, PNG, copy, save style, reset, and the zip; the Options zip too", async () => {
  const { window, document, errors } = await loadReport({ storage: { "ionomos.export.v1": { zip_format: "svg" } }, before: (w) => fakePng(w) });
  document.querySelector("#xopen").click();
  const before = window.__downloads.length;
  document.querySelector("#xsvg").click();
  parseSvg(window, await blobText(window, window.__downloads.at(-1).blob), "dialog SVG");
  document.querySelector("#xpng").click();
  await until(() => window.__downloads.length === before + 2);
  document.querySelector("#xcopy").click();
  await until(() => /paste|does not let/.test(document.querySelector("#xmsg").textContent));
  document.querySelector("#xsave").click();
  const style = JSON.parse(await blobText(window, window.__downloads.at(-1).blob));
  assert.equal(style.ionomos_export_style, 1);
  document.querySelector("#xreset").click();
  assert.match(document.querySelector("#xmsg").textContent, /lab defaults/);
  // every figure in the list draws a preview
  const sel = document.querySelector("#xfig");
  for (const o of [...sel.options]) {
    sel.value = o.value;
    sel.dispatchEvent(new window.Event("change", { bubbles: true }));
    assert.ok(document.querySelector("#xprev img") || /nothing to draw/.test(document.querySelector("#xprev").textContent), o.textContent);
  }
  document.querySelector("#xzip").click();
  await until(() => window.__downloads.at(-1).name.endsWith(".zip"));
  document.querySelector("#xclose").click();
  const n = window.__downloads.length;
  document.querySelector("#xzipnow").click();
  await until(() => window.__downloads.length === n + 1);
  assert.match(window.__downloads.at(-1).name, /^Fixture_Exp_figures\.zip$/);
  // pressing twice while one is being made gives one zip
  document.querySelector("#slides").click();
  document.querySelector("#slides").click();
  await until(() => window.__downloads.length === n + 2);
  await new Promise((r) => setTimeout(r, 300));
  assert.equal(window.__downloads.length, n + 2, "one zip for two presses");
  assert.deepEqual(errors, []);
});

test("with hostile names, every export still names its files safely", async () => {
  const evil = `<b>"x"</b> & 'y' ` + "é".repeat(200) + " CON";
  const data = withData(BASE, { title: evil, comps: BASE.comps.map((c) => ({ ...c, name: evil, slug: evil })) });
  const { window, document, errors } = await loadReport({ data, storage: { "ionomos.export.v1": { zip_format: "svg" } }, before: (w) => fakePng(w) });
  await pressAll(window, document, "#differential");
  document.querySelector("#csv").click();
  document.querySelector("#slides").click();
  await until(() => window.__downloads.at(-1).name.endsWith(".zip"));
  for (const d of window.__downloads) {
    assert.match(d.name, SAFE, d.name);
    assert.ok(d.name.length <= 120, d.name);
    assert.ok(!/^(con|nul|prn|aux)\b/i.test(d.name), d.name);
  }
  assert.deepEqual(errors, []);
});
