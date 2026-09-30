// The Dose-response section (renderDose): the fixture (DMSO vs Drug) has no titration and shows the
// empty state; a synthetic D.dose payload (the shape doseresponse.report_payload writes) exercises the
// table, class filter, text filter, sorting, the curve drawn for a clicked row or point, the potency vs
// effect scatter, the report search marking curves, and the CSV export.
import { test } from "node:test";
import assert from "node:assert/strict";

import { loadReport, baseData, withData, blobText } from "../lib/harness.mjs";

const BASE = baseData();

function series(name, idx) {
  const n = idx.length;
  const pick = (a) => idx.map((_, k) => a[k % a.length]);
  return {
    name, unit: "nM", doses: [1e-9, 1e-8, 1e-7, 1e-6], samples: [0, 1, 2, 3, 4, 5],
    sdose: [0, 0, 0, 1e-8, 1e-7, 1e-6], controls: ["DMSO"], i: idx,
    cls: pick(["down", "up", "not", "unclear"]), n: pick([7]), pec50: pick([7.2, 6.1, 8.0, 5.5]),
    ciL: pick([7.0, 5.8, null, 4.0]), ciR: pick([7.4, 6.4, null, 7.0]), ec50: pick([63.1, 794, 10, 3162]),
    slope: pick([1.2, 0.9, 0.5, 3]), front: pick([1, 1, 1, 1]), back: pick([0.25, 3.1, 1.02, 1.3]),
    fc: pick([-1.9, 1.6, 0.02, 0.3]), p: pick([1e-6, 3e-5, 0.6, 0.04]), q: pick([1e-5, 1e-4, 0.8, 0.1]),
    rel: pick([5.1, 4.2, 0.1, 0.9]), r2: pick([0.97, 0.93, 0.02, 0.4]),
    y: idx.map((_, k) => [0.02, -0.03, 0.01, k % 2 ? 0.4 : -0.5, k % 2 ? 1.1 : -1.4, k % 2 ? 1.6 : -2.0]),
    skipped: 0,
  };
}

const DOSE = { ran: true, found: true, alpha: 0.05, fcLim: 0.45, reason: "",
  series: [series("Cmpd", [0, 1, 2, 3, 4, 5, 6, 7]), series("Other", [8, 9, 10])] };

test("fixture without a titration: section hidden, empty state says why", async () => {
  const { document, errors } = await loadReport();
  assert.deepEqual(errors, []);
  assert.equal(document.querySelector("#dose").hidden, true);
  assert.equal(document.querySelector("#navdose").hidden, true);
  assert.match(document.querySelector("#dosebody .empty").textContent, /No doses were found/);
});

test("too few doses: the section shows the reason", async () => {
  const data = withData(BASE, { dose: { ran: false, found: true, reason: "The design has fewer than 4 doses per compound" } });
  const { document, errors } = await loadReport({ data });
  assert.deepEqual(errors, []);
  assert.equal(document.querySelector("#dose").hidden, false);
  assert.match(document.querySelector("#dosebody").textContent, /fewer than 4 doses/);
});

test("curves: table, scatter, curve, filters, sorting, series", async () => {
  const { window, document, errors } = await loadReport({ data: withData(BASE, { dose: DOSE }) });
  assert.deepEqual(errors, []);
  const body = document.querySelector("#dosebody");
  assert.equal(document.querySelector("#dose").hidden, false, "section shown");
  assert.equal(document.querySelector("#navdose").hidden, false, "nav link shown");
  const rows = () => [...body.querySelectorAll("#dosetable tbody tr[data-k]")];
  assert.equal(rows().length, 8);
  assert.equal(body.querySelectorAll("#dosescatter circle[data-k]").length, 8, "one point per curve");
  assert.ok(body.querySelector("#dosecurve svg path"), "the first curve is drawn");
  assert.match(body.querySelector("#dosehead").textContent, new RegExp(BASE.f.label[0] || BASE.f.id[0]));
  assert.match(body.querySelector("#dosecurve svg").textContent, /ctrl/, "control shown at the left");
  assert.equal(body.querySelectorAll("#dosecurve circle").length, 6, "every measured point, controls included");
  assert.ok(body.querySelector("#dosescatter .tools button"), "SVG / PNG export");
  // click a row: its curve is drawn
  rows()[1].click();
  const k = +rows().find((tr) => tr.classList.contains("focus")).dataset.k;
  assert.match(body.querySelector("#dosehead").textContent, new RegExp(BASE.f.label[k] || BASE.f.id[k]));
  // a point in the scatter too
  body.querySelector("#dosescatter circle[data-k='2']").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  assert.match(body.querySelector("#dosehead").textContent, /not/);
  assert.match(body.querySelector("#dosehead").textContent, /don't read its pEC50/);
  // class filter
  const cls = body.querySelector("#dcls");
  cls.value = "down"; cls.dispatchEvent(new window.Event("change"));
  assert.equal(rows().length, 2);
  assert.equal(body.querySelectorAll("#dosescatter circle[data-k]").length, 2);
  cls.value = "all"; cls.dispatchEvent(new window.Event("change"));
  // text filter
  const q = body.querySelector("#dq");
  q.value = (BASE.f.label[3] || BASE.f.id[3]).toLowerCase(); q.dispatchEvent(new window.Event("input"));
  assert.ok(rows().length >= 1 && rows().length < 8);
  q.value = ""; q.dispatchEvent(new window.Event("input"));
  // sort by pEC50 (descending first)
  body.querySelector("#dosetable th[data-k='pec50']").click();
  const pec = rows().map((tr) => parseFloat(tr.children[2].textContent));
  assert.deepEqual(pec, [...pec].sort((a, b) => b - a));
  // the second compound
  const ser = body.querySelector("#dseries");
  ser.value = "1"; ser.dispatchEvent(new window.Event("change"));
  assert.equal(rows().length, 3);
  assert.deepEqual(errors, []);
});

test("the report search marks curves and draws the first match", async () => {
  const { window, document, errors } = await loadReport({ data: withData(BASE, { dose: DOSE }) });
  const name = BASE.f.label[5] || BASE.f.id[5];
  const box = document.querySelector("#search");
  box.value = name; box.dispatchEvent(new window.Event("input"));
  const body = document.querySelector("#dosebody");
  assert.match(body.querySelector("#dosehl").textContent, /match the search/);
  assert.match(body.querySelector("#dosehead h4").textContent, new RegExp(name));
  assert.ok(body.querySelector("#dosescatter circle[stroke-width='1.6']"), "matched point ringed");
  assert.deepEqual(errors, []);
});

test("CSV export of the filtered curves", async () => {
  const { window, document } = await loadReport({ data: withData(BASE, { dose: DOSE }) });
  const cls = document.querySelector("#dcls");
  cls.value = "up"; cls.dispatchEvent(new window.Event("change"));
  document.querySelector("#dcsv").click();
  const csv = await blobText(window, window.__blobs.at(-1));
  const lines = csv.trim().split("\n");
  assert.match(lines[0], /^series,id,label,class,pEC50/);
  assert.equal(lines.length, 3, "header + the two up curves");
  assert.ok(lines.slice(1).every((l) => l.includes('"up"')));
});

test("dark theme redraw keeps the section", async () => {
  const { document, errors } = await loadReport({ data: withData(BASE, { dose: DOSE }) });
  document.querySelector("#theme").click();
  assert.ok(document.querySelector("#dosecurve svg path"));
  assert.deepEqual(errors, []);
});
