// The Run order QC tab (qcRun, D78): absent for the fixture (no raw files, so no acquisition times); a synthetic
// D.qc.run payload (the shape runorder.report_payload writes) exercises the summary, the confounding line, the
// chart with its condition strip and Theil-Sen line, the trend and sample tables, the help and escaping.
import { test } from "node:test";
import assert from "node:assert/strict";

import { loadReport, baseData, withData } from "../lib/harness.mjs";

const BASE = baseData();
const ORDER = ["DMSO_1", "Drug_1", "DMSO_2", "Drug_2", "DMSO_3", "Drug_3"];
const sample = (s, k) => ({ s: s, c: s.split("_")[0], o: k + 1, t: "2026-09-30T0" + (8 + k) + ":00:00", src: "raw header", approx: false, nf: 1 });
const RUN = {
  samples: ORDER.map(sample),
  metrics: [
    { key: "ids", label: "identifications", unit: "", v: [3100, 3080, 3050, 3070, 3040, 3060], flag: false, judge: "rel", limit: 0.1, med: 3065, tau: -0.556, p: 0.11, slope: -10, change: -50, rho: -0.5, n: 6 },
    { key: "ppm", label: "median precursor mass error", unit: "ppm", v: [-2, -1.4, -0.8, -0.2, 0.4, 1.0], flag: true, judge: "abs", limit: 3, med: -0.5, tau: 1, p: 0.0028, slope: 0.6, change: 3, rho: 1, n: 6 },
    { key: "missing", label: "missing values", unit: "%", v: [10, 11, null, 12, 13, 14], flag: false, judge: "abs", limit: 5, med: 12 },
  ],
  confound: { eta2: 0.086, p: 0.62, changes: 5, expected: 3, flag: false, positions: { DMSO: "1, 3, 5", Drug: "2, 4, 6" } },
  sources: { "raw header": 6 }, approx: false, limits: { alpha: 0.01, eta2: 0.6, min: 6 }, notes: [],
};
const DATA = withData(BASE, { qc: { run: RUN } });

function open(document) {
  const b = document.querySelector("#qc .tabs button[data-k='run']");
  if (b) b.click();
  return document.querySelector("#qc .tabs button[data-k='run']");
}
const cells = (tr) => [...tr.children].map((c) => c.textContent.trim());

test("fixture without acquisition times: no Run order tab", async () => {
  const { document, errors } = await loadReport();
  assert.deepEqual(errors, []);
  assert.equal(BASE.qc.run, undefined);
  assert.equal(document.querySelector("#qc .tabs button[data-k='run']"), null);
});

test("the tab: summary, conditions in the run, the flagged number chosen first, both tables", async () => {
  const { document, errors } = await loadReport({ data: DATA });
  const tab = open(document);
  assert.equal(tab.textContent, "Run order");
  const body = document.querySelector("#qcbody");
  assert.match(body.querySelector(".sub").textContent, /6 sample\(s\), the times from the raw files' own header\. .*tested within each condition.*p < 0\.01/s);
  assert.match(body.textContent, /Conditions in the run: DMSO: runs 1, 3, 5 · Drug: runs 2, 4, 6\. The condition explains 9% of where a sample sits/);
  assert.equal(document.querySelector("#runsel").value, "ppm", "a flagged number is shown first");
  assert.deepEqual([...document.querySelectorAll("#runsel option")].map((o) => o.textContent),
    ["identifications", "median precursor mass error (flagged)", "missing values"]);
  const trend = [...document.querySelectorAll("#runtrend tbody tr")].map(cells);
  assert.deepEqual(trend[0], ["identifications", "-0.56", "0.110", "−2% (−50)", "10%", ""]);
  assert.deepEqual(trend[1], ["median precursor mass error", "+1.00", "0.0028", "+3.00 ppm", "3.0 ppm", "drift"]);
  assert.deepEqual(trend[2].slice(0, 3), ["missing values", "–", "not tested"]);
  const rows = [...document.querySelectorAll("#runtable tbody tr")].map(cells);
  assert.deepEqual(rows.map((r) => r[1]), ORDER);
  assert.deepEqual(rows[0], ["1", "DMSO_1", "DMSO", "2026-09-30 08:00:00", "the raw files' own header", "3,100", "-2.00", "10.0%"]);
  assert.equal(rows[2][7], "–");
  assert.deepEqual(errors, []);
});

test("the chart: a point per sample, the condition strip, the slope line; another number redraws it", async () => {
  const { window, document, errors } = await loadReport({ data: DATA });
  open(document);
  const chart = () => document.querySelector("#runchart");
  assert.equal(chart().querySelectorAll("svg circle").length, 6);
  const strip = [...chart().querySelectorAll("svg rect")];
  assert.equal(strip.length, 6, "a strip cell per run");
  assert.equal(strip[0].querySelector("title").textContent, "run 1: DMSO_1 (DMSO)");
  assert.notEqual(strip[0].getAttribute("fill"), strip[1].getAttribute("fill"));
  const line = [...chart().querySelectorAll("svg line")].find((l) => l.getAttribute("stroke-width") === "2");
  assert.ok(line, "the flagged trend is a solid line");
  assert.match(chart().textContent, /run \(order of acquisition\).*median precursor mass error \(ppm\)/s);
  assert.match(chart().querySelector(".muted").textContent, /τ \+1\.00, p 0\.0028, change over the run \+3\.00 ppm — flagged/);
  assert.ok(chart().querySelector(".tools button"), "export buttons");
  const sel = document.querySelector("#runsel");
  sel.value = "missing";
  sel.dispatchEvent(new window.Event("change"));
  assert.equal(chart().querySelectorAll("svg circle").length, 5, "a sample without the number is left out");
  assert.match(chart().querySelector(".muted").textContent, /not tested \(needs 6 samples with a time\)/);
  assert.equal(document.querySelector("#runsel").value, "missing", "the choice is kept");
  assert.deepEqual(errors, []);
});

test("blocks are said as a warning; approximate times and notes are shown", async () => {
  const blocked = { ...RUN, confound: { eta2: 0.771, p: 0.1, changes: 1, expected: 3, flag: true, positions: { DMSO: "1–3", Drug: "4–6" } },
    sources: { "file time": 6 }, approx: true, notes: ["the order of some samples is from their raw files' modification time, which is approximate"] };
  const { document, errors } = await loadReport({ data: withData(BASE, { qc: { run: blocked } }) });
  open(document);
  const body = document.querySelector("#qcbody");
  const warn = body.querySelector("p.zbad");
  assert.match(warn.textContent, /The conditions were run in blocks\. DMSO: runs 1–3 · Drug: runs 4–6\. The condition explains 77%.*cannot tell it from the biology/);
  assert.match(body.querySelector(".sub").textContent, /the raw files' modification time \(approximate\)/);
  assert.match(body.textContent, /which is approximate/);
  assert.deepEqual(errors, []);
});

test("the tab has its ?, names are escaped", async () => {
  const evil = { ...RUN, samples: RUN.samples.map((s, k) => (k ? s : { ...s, s: "<img src=x onerror=alert(1)>" })) };
  const { document, errors } = await loadReport({ data: withData(BASE, { qc: { run: evil } }) });
  open(document);
  const b = document.querySelector("#qcbody > .sub > button.qhelp");
  assert.ok(b, "? on the Run order tab");
  assert.equal(b.dataset.help, "qc.run");
  assert.equal(document.querySelector("#runtable img"), null);
  assert.match(document.querySelector("#runtable tbody tr").textContent, /<img src=x/);
  assert.deepEqual(errors, []);
});
