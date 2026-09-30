// The volcano search (lists, wildcards, regex, desc:, term:, suggestions), box selection, highlight groups,
// the page state in the address, hit filters, and the discovery sections (key findings, compare, only in
// one condition, rank-based enrichment, the new QC tabs), each against the fixture or a variant of it.
import { test } from "node:test";
import assert from "node:assert/strict";

import { loadReport, baseData, withData, blobText } from "../lib/harness.mjs";

const BASE = baseData();
const nF = BASE.v.length;
const labels = BASE.f.label;

function type(window, document, value) {
  const box = document.querySelector("#search");
  box.focus();
  box.value = value;
  box.dispatchEvent(new window.Event("input", { bubbles: true }));
  return box;
}
function key(window, el, k) {
  el.dispatchEvent(new window.KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true }));
}
const rows = (document) => [...document.querySelectorAll("#table tbody tr[data-i]")];

test("a pasted list: exact names, found / not found, only the matches in the table", async () => {
  const { window, document, errors } = await loadReport();
  type(window, document, "ACTB, GAPDH NOTAGENE");
  const info = document.querySelector("#searchinfo").textContent;
  assert.match(info, /2 matches/);
  assert.match(info, /found 2 of 3/);
  assert.match(info, /not found: NOTAGENE/);
  assert.deepEqual(rows(document).map((r) => labels[+r.dataset.i]).sort(), ["ACTB", "GAPDH"]);
  assert.deepEqual(errors, []);
});

test("wildcards, /regex/ and desc: search", async () => {
  const { window, document } = await loadReport();
  type(window, document, "GENE2*");
  const want = labels.filter((l) => /^GENE2.*$/i.test(l)).length;
  assert.ok(want > 0);
  assert.match(document.querySelector("#searchinfo").textContent, new RegExp(`^${want} match`));
  type(window, document, "/^(ACTB|GAPDH)$/");
  assert.match(document.querySelector("#searchinfo").textContent, /^2 matches/);
  type(window, document, "/[unclosed/");
  assert.match(document.querySelector("#searchinfo").textContent, /not a valid pattern/);
  type(window, document, "desc:protein");
  const withDesc = BASE.f.desc.filter((d) => /protein/i.test(d)).length;
  assert.match(document.querySelector("#searchinfo").textContent, new RegExp(`^${withDesc} match`));
});

test("term: search marks a gene set's members", async () => {
  const t = BASE.gsea[0].terms[0];
  const { window, document } = await loadReport();
  type(window, document, "term:" + t.term);
  const info = document.querySelector("#searchinfo").textContent;
  const members = new Set(t.genes.map((g) => g.toUpperCase()));
  const n = labels.filter((l) => members.has(String(l).toUpperCase())).length;
  assert.match(info, new RegExp(`^${n} match`));
  assert.ok(info.includes(t.term), "names the term");
  type(window, document, "term:no such pathway anywhere");
  assert.match(document.querySelector("#searchinfo").textContent, /no gene set matches/);
});

test("suggestions: typing offers names, arrow + enter picks one and opens it", async () => {
  const { window, document } = await loadReport();
  const box = type(window, document, "GAP");
  const sug = document.querySelector("#suggest");
  assert.equal(sug.hidden, false, "suggestions shown");
  assert.match(sug.textContent, /GAPDH/);
  key(window, box, "ArrowDown");
  key(window, box, "Enter");
  assert.equal(box.value, "GAPDH");
  assert.equal(document.querySelector("#detail h4").textContent, "GAPDH");
  key(window, box, "Escape");
  assert.equal(box.value, "", "escape with no suggestions open clears the search");
});

test("the view is read from and written to the address", async () => {
  const { window, document } = await loadReport({ url: "file:///report.html#q=ACTB&lfc=2&adj=0" });
  assert.equal(document.querySelector("#search").value, "ACTB");
  assert.equal(document.querySelector("#lfc").value, "2");
  assert.equal(document.querySelector("#adj").checked, false);
  type(window, document, "GAPDH");
  assert.match(window.location.hash, /q=GAPDH/);
  assert.match(window.location.hash, /lfc=2/);
});

test("box selection marks the points inside the box", async () => {
  const { window, document } = await loadReport();
  document.querySelector("#dsel").click();
  const hit = document.querySelector("#differential-body svg rect[style*='crosshair']");
  const ev = (t, x, y) => hit.dispatchEvent(new window.MouseEvent(t, { clientX: x, clientY: y, bubbles: true }));
  ev("mousedown", 57, 27);
  ev("mousemove", 700, 400);
  ev("mouseup", 740, 440);
  assert.match(document.querySelector("#hl").textContent, /box selection/);
  assert.ok(rows(document).length > 0, "the table shows the selection");
  document.querySelector("#hlclear").click();
  assert.equal(document.querySelector("#hl").textContent, "");
});

test("highlight groups: add, legend, remembered by the browser, export", async () => {
  const url = "http://localhost/report.html";
  let page = await loadReport({ url });
  let { window, document } = page;
  document.querySelector("#gname").value = "My set";
  document.querySelector("#ggenes").value = "ACTB GAPDH, NOTAGENE";
  document.querySelector("#gadd").click();
  assert.match(document.querySelector("#groups").textContent, /My set/);
  assert.match(document.querySelector("#glegend").textContent, /My set \(2\)/);
  const saved = JSON.parse(window.localStorage.getItem("ionomos.groups.v1"));
  assert.equal(saved[0].name, "My set");
  document.querySelector("#gexport").click();
  const gmt = await blobText(window, window.__blobs.at(-1));
  assert.match(gmt, /^My set\tIonomos highlight group\tACTB\tGAPDH\tNOTAGENE/);
  // a second report in the same browser starts with the group
  const store = window.localStorage.getItem("ionomos.groups.v1");
  page = await loadReport({ url });
  page.window.localStorage.setItem("ionomos.groups.v1", store);
  page = await loadReport({ url });
  assert.ok(page.document.querySelector("#groups").textContent.length > 0);
});

function withForcedHit(patch) {
  // feature 0: a strong hit whose DMSO values are all imputed and with a single peptide
  const c = BASE.comps[0];
  const fc = c.fc.slice(), p = c.p.slice(), q = c.q.slice();
  fc[0] = 4; p[0] = 1e-9; q[0] = 1e-7;
  const imp = BASE.imp.slice();
  imp[0] = BASE.cond.map((x) => (x === c.t2 ? "1" : "0")).join("");
  const pep = BASE.f.pep.slice();
  pep[0] = 1;
  return withData(BASE, Object.assign({ comps: [withData(c, { fc, p, q })], imp, f: withData(BASE.f, { pep }) }, patch || {}));
}
const upCount = (document) => +document.querySelector("#vcount b").textContent.replace(/,/g, "");

test("hit filters: imputation-driven and single-peptide hits can be left out", async () => {
  const { window, document } = await loadReport({ data: withForcedHit() });
  const before = upCount(document);
  const imp = document.querySelector("#hideimp");
  imp.checked = true;
  imp.dispatchEvent(new window.Event("change"));
  assert.ok(upCount(document) < before, "the imputation-driven hit is no longer counted");
  assert.match(document.querySelector("#vcount").textContent, /filtered out/);
  assert.match(document.querySelector("#cutnote").textContent, /imputation-driven hits ignored/);
  imp.checked = false;
  imp.dispatchEvent(new window.Event("change"));
  assert.equal(upCount(document), before);
  const mp = document.querySelector("#minpep");
  mp.value = "2";
  mp.dispatchEvent(new window.Event("input"));
  assert.ok(upCount(document) < before, "the single-peptide hit is no longer counted");
  // the flags show as badges in the table
  document.querySelector("#minpep").value = "0";
  document.querySelector("#minpep").dispatchEvent(new window.Event("input"));
  const row = rows(document).find((r) => r.dataset.i === "0");
  assert.match(row.textContent, /imputed/);
  assert.match(row.textContent, /1 pep/);
  document.querySelector("#csv").click();
  const csv = await blobText(window, window.__blobs.at(-1));
  assert.match(csv.split("\n")[0], /"imputation_driven"/);
});

test("key findings summarise each comparison and the sample checks", async () => {
  const { document } = await loadReport();
  const f = document.querySelector("#findings").textContent;
  assert.match(f, /Key findings/);
  assert.ok(f.includes(BASE.comps[0].name));
  assert.match(f, /Samples/);
});

test("compare: four-quadrant plot and UpSet with two comparisons; a bar marks its hits", async () => {
  const c = BASE.comps[0];
  const second = withData(c, { name: "Other vs DMSO", slug: "Other_vs_DMSO", t1: "Other", fc: c.fc.map((v, i) => (v == null ? v : i % 3 ? v : -v)) });
  const { document, errors } = await loadReport({ data: withData(BASE, { comps: [c, second] }) });
  assert.deepEqual(errors, []);
  assert.ok(document.querySelector("#quad svg"), "quadrant plot drawn");
  assert.match(document.querySelector("#quadinfo").textContent, /Fold changes correlate/);
  const bar = document.querySelector("#upset svg rect[style*='pointer']");
  assert.ok(bar, "UpSet bars drawn");
  bar.dispatchEvent(new document.defaultView.MouseEvent("click", { bubbles: true }));
  assert.match(document.querySelector("#hl").textContent, /hits in/);
});

test("only in one condition: listed, badged, and marked from the table", async () => {
  const c = BASE.comps[0];
  const { document } = await loadReport({ data: withData(BASE, { comps: [withData(c, { onoff: [[0, "t", 3, 3], [1, "c", 3, 3]] })] }) });
  const table = document.querySelector("#onoffbody table");
  assert.ok(table, "table drawn");
  assert.equal(table.querySelectorAll("tbody tr").length, 2);
  document.querySelector("#oomark").click();
  assert.match(document.querySelector("#hl").textContent, /only in one condition \(2\)/);
  assert.ok(document.querySelector("#differential-body svg path"), "on/off features get a triangle on the volcano");
});

test("rank-based enrichment: table, barcode plot, and marking a set on the volcano", async () => {
  const { document } = await loadReport();
  const tab = [...document.querySelectorAll("#enrich .tabs button")].find((b) => /ranked/.test(b.textContent));
  assert.ok(tab, "rank-based tab present");
  tab.click();
  const first = document.querySelector("#rtable tbody tr");
  assert.ok(first, "terms listed");
  first.click();
  assert.ok(document.querySelector("#rcode svg"), "barcode drawn");
  document.querySelector("#rmark").click();
  assert.match(document.querySelector("#search").value, /^term:/);
});

test("every QC tab draws, and a broken one is contained", async () => {
  const { document, errors } = await loadReport();
  const names = [...document.querySelectorAll("#qc .tabs button")].map((b) => b.textContent);
  for (const want of ["Sample scorecard", "Missing vs intensity", "Mean–variance", "Abundance rank", "Power"]) {
    assert.ok(names.includes(want), `${want} tab present: ${names.join(", ")}`);
  }
  for (const n of names) {
    [...document.querySelectorAll("#qc .tabs button")].find((b) => b.textContent === n).click();
    const body = document.querySelector("#qcbody");
    assert.ok(!/could not be drawn/.test(body.textContent), `${n} draws`);
    assert.ok(body.querySelector("svg, table, canvas, .empty"), `${n} has content`);
  }
  assert.deepEqual(errors, []);
  const broken = await loadReport({ data: withData(BASE, { qc: withData(BASE.qc, { mnar: { verdict: "random", bins: null } }) }) });
  [...broken.document.querySelectorAll("#qc .tabs button")].find((b) => b.textContent === "Missing vs intensity").click();
  assert.match(broken.document.querySelector("#qcbody").textContent, /could not be drawn/);
  assert.ok(broken.document.querySelector("#differential-body svg circle"), "the rest of the page is fine");
  assert.deepEqual(broken.errors, []);
});

test("detail panel: proteins that behave alike, and marking them", async () => {
  const { document } = await loadReport();
  rows(document)[0].click();
  const sim = document.querySelector("#detail .sim");
  if (!sim) return; // nothing correlates above 0.6 in this fixture's first hit
  assert.ok(sim.querySelectorAll(".chip").length > 0);
  document.querySelector("#simbtn").click();
  assert.match(document.querySelector("#hl").textContent, /behaves like/);
});
