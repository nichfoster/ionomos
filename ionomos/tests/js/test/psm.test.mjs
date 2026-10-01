// The Search quality QC tab (qcPsm): absent for the fixture (a DIA-NN matrix with no psm.tsv or stats.tsv); a
// synthetic D.qc.psm payload (the shape psmqc.report_payload writes) exercises the table with its flags, the four
// charts, DIA-NN's own summary, the notes, the help and escaping.
import { test } from "node:test";
import assert from "node:assert/strict";

import { loadReport, baseData, withData } from "../lib/harness.mjs";

const BASE = baseData();

const run = (name, o = {}) => ({ run: name, sample: name, psms: 24000, pep: 16000, prot: 3100, ppm: [-1.2, 0.8, 2.1, 3.3, 5.6], ppmN: 23900,
  mc: [20000, 3500, 500], mcRate: 0.1667, mcMean: 0.19, z: [60.5, 32.5, 7], zMean: 2.47, len: 13, flags: [], ...o });
const PSM = {
  runs: [run("DMSO_1"), run("DMSO_2", { ppm: [-16, -13.5, -12.25, -11, -8], flags: ["mass error"] }),
    run("Drug_1", { mc: [9000, 12000, 3000], mcRate: 0.625, flags: ["missed cleavages"] }),
    run("Drug_2", { ppm: null, ppmN: 0, mc: null, mcRate: null, mcMean: null, len: null, pep: null })],
  z: [2, 3, 4], len: { lo: 7, n: [10, 40, 90, 120, 80, 30, 5] },
  diann: [], limits: { ppm: 10, missed: 0.5, minPsms: 100 }, notes: [],
};
const DIANN = [{ run: "DMSO_1", precursors: 41772, proteins: 6209, ms1_ppm: 1.7369, ms2_ppm: 2.9236, missed: 0.1075, charge: 2.4406, fwhm: 0.1544 }];
const DATA = withData(BASE, { qc: { psm: PSM } });

function open(document) {
  const b = document.querySelector("#qc .tabs button[data-k='psm']");
  if (b) b.click();
  return document.querySelector("#qc .tabs button[data-k='psm']");  // the tabs are redrawn on a click
}
function choose(window, value) {
  const e = window.document.querySelector("#psmsel");
  e.value = value;
  e.dispatchEvent(new window.Event("change"));
}
const cells = (tr) => [...tr.children].map((c) => c.textContent.trim());

test("fixture without psm.tsv: no Search quality tab", async () => {
  const { document, errors } = await loadReport();
  assert.deepEqual(errors, []);
  assert.equal(BASE.qc.psm, undefined);
  assert.equal(document.querySelector("#qc .tabs button[data-k='psm']"), null);
});

test("the table: a row per run, flagged numbers marked, what is missing shown as a dash", async () => {
  const { document, errors } = await loadReport({ data: DATA });
  const tab = open(document);
  assert.equal(tab.textContent, "Search quality");
  assert.equal(tab.className, "on");
  const body = document.querySelector("#qcbody");
  assert.match(body.querySelector(".sub").textContent, /flagged when its median mass error is 10 ppm or more from 0, or 50% or more of its PSMs have a missed cleavage \(wide limits, not yet the lab's own\)/);
  assert.equal(body.querySelector("a[href='psm_qc.tsv']").textContent, "psm_qc.tsv");
  const head = [...document.querySelectorAll("#psmtable th")].map((t) => t.textContent);
  assert.deepEqual(head, ["Run", "PSMs", "Peptides", "Proteins", "mass error (ppm)", "middle half", "missed cleavage", "2+", "3+", "4+", "length", "Flags"], "no Sample column when it repeats the run");
  const rows = [...document.querySelectorAll("#psmtable tbody tr")];
  assert.equal(rows.length, 4);
  assert.deepEqual(cells(rows[0]), ["DMSO_1", "24,000", "16,000", "3,100", "+2.10", "0.80 to 3.30", "16.7%", "60.5%", "32.5%", "7.0%", "13", ""]);
  assert.deepEqual(cells(rows[1]).slice(4, 6).concat(cells(rows[1])[11]), ["-12.25", "-13.50 to -11.00", "mass error"]);
  assert.deepEqual([...rows[1].querySelectorAll("td.zbad")].map((c) => c.textContent), ["-12.25"]);
  assert.deepEqual([...rows[2].querySelectorAll("td.zbad")].map((c) => c.textContent), ["62.5%"]);
  assert.equal(rows[0].querySelector("td.zbad"), null);
  assert.deepEqual(cells(rows[3]).slice(2, 7), ["–", "3,100", "–", "–", "–"]);
  assert.equal(cells(rows[3])[10], "–");
  assert.equal(document.querySelector("#psmdiann"), null, "no DIA-NN table without DIA-NN runs");
  assert.deepEqual(errors, []);
});

test("the four charts: mass error boxes, stacked missed cleavages and charges, peptide lengths", async () => {
  const { window, document, errors } = await loadReport({ data: DATA });
  open(document);
  const chart = () => document.querySelector("#psmchart");
  assert.deepEqual([...document.querySelectorAll("#psmsel option")].map((o) => o.textContent), ["Mass error", "Missed cleavages", "Charge states", "Peptide length"]);
  const boxes = [...chart().querySelectorAll("rect[data-run]")];
  assert.deepEqual(boxes.map((b) => b.dataset.run), ["0", "1", "2"], "a box per run with mass errors");
  assert.match(boxes[1].querySelector("title").textContent, /DMSO_2\nmedian -12\.25 ppm · middle half -13\.50 to -11\.00 · 5th to 95th percentile -16\.00 to -8\.00 · 23,900 PSMs/);
  assert.notEqual(boxes[1].getAttribute("fill"), boxes[0].getAttribute("fill"), "the flagged run is drawn in another colour");
  assert.match(chart().querySelector("svg").textContent, /precursor mass error \(ppm\).*DMSO_1.*Drug_2/s);
  choose(window, "mc");
  let bars = [...chart().querySelectorAll("rect[data-k]")];
  assert.equal(bars.length, 9, "three parts for each of the three runs with counts");
  assert.match(bars[7].querySelector("title").textContent, /Drug_1: 1 · 50\.0%/);
  assert.deepEqual([...chart().querySelectorAll(".legend > span")].map((s) => s.textContent), ["no missed cleavage", "1", "2 or more"]);
  choose(window, "z");
  bars = [...chart().querySelectorAll("rect[data-k]")];
  assert.equal(bars.length, 12);
  assert.match(bars[0].querySelector("title").textContent, /DMSO_1: 2\+ · 60\.5%/);
  assert.deepEqual([...chart().querySelectorAll(".legend > span")].map((s) => s.textContent), ["2+", "3+", "4+"]);
  choose(window, "len");
  const hist = [...chart().querySelectorAll("svg rect")];
  assert.equal(hist.length, 7);
  assert.equal(hist[3].querySelector("title").textContent, "10 amino acids: 120 PSMs");
  assert.match(chart().querySelector("svg").textContent, /peptide length \(amino acids\), all runs/);
  assert.equal(document.querySelector("#psmsel").value, "len", "the choice is kept");
  document.querySelector("#theme").click();
  assert.ok(document.querySelector("#psmchart svg"), "redrawn after a theme change");
  assert.deepEqual(errors, []);
});

test("many runs: no names under the bars, every run still drawn; samples get a column when they differ", async () => {
  const runs = Array.from({ length: 96 }, (_, k) => run("plex" + (1 + (k % 4)) + "_F" + (1 + Math.floor(k / 4)), { sample: "plex" + (1 + (k % 4)) }));
  const { document, errors } = await loadReport({ data: withData(BASE, { qc: { psm: { ...PSM, runs } } }) });
  open(document);
  assert.equal(document.querySelectorAll("#psmchart rect[data-run]").length, 96);
  assert.equal(document.querySelectorAll("#psmchart svg text[transform*='rotate(60)']").length, 0, "the tooltip names the run instead");
  assert.match(document.querySelector("#psmchart rect[data-run='0'] title").textContent, /^plex1_F1\n/);
  assert.equal(document.querySelectorAll("#psmtable th")[1].textContent, "Sample");
  assert.deepEqual(cells(document.querySelector("#psmtable tbody tr")).slice(0, 2), ["plex1_F1", "plex1"]);
  assert.deepEqual(errors, []);
});

test("DIA-NN's own summary: a table, alone or under the PSM table; notes are shown", async () => {
  const only = await loadReport({ data: withData(BASE, { qc: { psm: { ...PSM, runs: [], z: [], diann: DIANN, notes: ["A_1/psm.tsv is 5,000 MB, over the 4,096 MB limit; not read"] } } }) });
  open(only.document);
  const body = only.document.querySelector("#qcbody");
  assert.equal(body.querySelector("#psmtable"), null);
  assert.equal(body.querySelector("#psmchart"), null);
  assert.doesNotMatch(body.querySelector(".sub").textContent, /flagged/);
  assert.match(body.textContent, /over the 4,096 MB limit; not read/);
  assert.deepEqual(cells(body.querySelector("#psmdiann tbody tr")), ["DMSO_1", "41,772", "6,209", "1.74", "2.92", "0.107", "2.44", "0.154"]);
  assert.match(body.textContent, /DIA-NN's own number \(Median\.Mass\.Acc\).*no run is flagged on it/);
  assert.deepEqual(only.errors, []);
  const both = await loadReport({ data: withData(BASE, { qc: { psm: { ...PSM, diann: DIANN } } }) });
  open(both.document);
  assert.ok(both.document.querySelector("#psmtable") && both.document.querySelector("#psmdiann"));
  assert.deepEqual(both.errors, []);
});

test("the tab has its ?, and a search with PSMs but no quantities still shows it", async () => {
  const { window, document, errors } = await loadReport({ data: DATA });
  open(document);
  const b = document.querySelector("#qcbody > .sub > button.qhelp");
  assert.ok(b, "? on the Search quality tab");
  assert.equal(b.dataset.help, "qc.psm");
  b.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true }));
  assert.match(document.querySelector("#qcbody > .sub + .helppanel").textContent, /Search quality.*psm\.tsv/s);
  assert.deepEqual(errors, []);
  const bare = { ...BASE, samples: [], cond: [], conditions: [], rep: [], f: { id: [], label: [], desc: [] }, v: [], imp: null, comps: [], qc: { psm: PSM } };
  delete bare.F;
  const r = await loadReport({ data: bare });
  open(r.document);
  assert.equal(r.document.querySelectorAll("#psmtable tbody tr").length, 4);
  assert.ok(r.document.querySelector("#psmchart svg"));
  assert.deepEqual(r.errors, []);
});

test("hostile run and sample names and notes are escaped", async () => {
  const evil = "<img src=x onerror=alert(1)>";
  const data = withData(BASE, { qc: { psm: { ...PSM, runs: [run(evil, { sample: evil + "s", flags: [evil] }), run("ok")], diann: [{ ...DIANN[0], run: evil }], notes: [evil] } } });
  const { window, document, errors } = await loadReport({ data });
  open(document);
  for (const c of ["ppm", "mc", "z", "len"]) {
    choose(window, c);
    assert.equal(document.querySelector("#qcbody img"), null, c);
  }
  assert.ok(document.querySelector("#psmtable").textContent.includes(evil), "shown as written");
  assert.deepEqual(errors, []);
});
