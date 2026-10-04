// The Time course section (renderTime): hidden for the fixture (DMSO vs Drug, no times); a synthetic D.time
// payload (the shape timecourse.report_payload writes) exercises the pattern tiles, the table with its Show
// choices, the pattern filter, sorting, the series switch, the profile with the control series, and escaping.
import { test } from "node:test";
import assert from "node:assert/strict";

import { loadReport, baseData, withData } from "../lib/harness.mjs";

const BASE = baseData();

function series(name, idx, sampleOffset, vs) {
  const pick = (a) => idx.map((_, k) => a[k % a.length]);
  return {
    name, unit: "h", times: [0, 1, 4], labels: ["0 h", "1 h", "4 h"], conds: [name + "_0h", name + "_1h", name + "_4h"],
    samples: [0, 1, 2].map((j) => j + sampleOffset), stime: [0, 1, 4], i: idx,
    cls: pick(["up", "down", "not", "mixed"]), pat: pick([1, 2, null, 1]), F: pick([40, 22, 0.4, 9]),
    p: pick([1e-7, 1e-5, 0.7, 0.001]), q: pick([1e-5, 1e-4, 0.9, 0.01]), tt: pick([8, -6, 0.2, 1]),
    tq: pick([1e-4, 1e-3, 0.9, 0.5]), max: pick([2.1, -1.8, 0.1, 1.4]), peak: pick([2, 2, 1, 1]),
    fc: idx.map((_, k) => [[0, 1, 2.1], [0, -0.9, -1.8], [0, 0.1, 0], [0, 1.4, -1.1]][k % 4]),
    patterns: [{ n: 4, profile: [0, 1.1, 2] }, { n: 2, profile: [0, -0.9, -1.8] }], vs: vs || "",
    iq: vs ? pick([1e-4, 0.3, 0.9, 0.02]) : null, untested: 3,
  };
}
const TIME = { ran: true, found: true, alpha: 0.05, lfc: 1, model: "~0 + condition", reason: "",
  series: [series("Drug", [0, 1, 2, 3, 4, 5, 6, 7], 0, "DMSO"), series("DMSO", [0, 1, 2, 3], 3)] };
const DATA = withData(BASE, { time: TIME });
const rowsOf = (document) => [...document.querySelectorAll("#timetable tbody tr[data-k]")];
function choose(window, sel, value) {
  const e = window.document.querySelector(sel);
  e.value = value;
  e.dispatchEvent(new window.Event(e.tagName === "SELECT" ? "change" : "input"));
}

test("fixture without times: section hidden, empty state says why", async () => {
  const { document, errors } = await loadReport();
  assert.deepEqual(errors, []);
  assert.equal(document.querySelector("#time").hidden, true);
  assert.equal(document.querySelector("#navtime").hidden, true);
  assert.match(document.querySelector("#timebody .empty").textContent, /No times were found/);
});

test("too few time points: the section shows the reason", async () => {
  const { document } = await loadReport({ data: withData(BASE, { time: { ran: false, found: true, reason: "time course Drug: skipped: 2 time points" } }) });
  assert.equal(document.querySelector("#time").hidden, false);
  assert.match(document.querySelector("#timebody").textContent, /2 time points/);
});

test("time course: patterns, table, Show choices, pattern filter, sorting", async () => {
  const { window, document, errors } = await loadReport({ data: DATA });
  assert.deepEqual(errors, []);
  assert.equal(document.querySelector("#navtime").hidden, false);
  const body = document.querySelector("#timebody");
  assert.match(body.textContent, /3 time points: 0 h, 1 h, 4 h · changing: F adj\. p ≤ 0\.05 and \|log2FC\| ≥ 1 against 0 h · 3 not tested/);
  assert.equal(body.querySelector("a[href='time_course.tsv']").textContent, "time_course.tsv");
  const tiles = [...body.querySelectorAll("#timepatterns .tile")];
  assert.deepEqual(tiles.map((t) => t.querySelector(".k").textContent), ["Pattern 1 · 4 features", "Pattern 2 · 2 features"]);
  assert.ok(tiles[0].querySelector("svg path"));
  assert.equal(rowsOf(document).length, 6, "changing features only");
  const head = [...document.querySelectorAll("#timetable th")].map((t) => t.textContent);
  assert.deepEqual(head, ["Gene", "class", "pattern", "F", "p", "adj. p", "trend t", "trend adj. p", "largest log2FC", "at", "differs from DMSO adj. p"]);
  assert.deepEqual([...rowsOf(document)[0].children].slice(1, 10).map((c) => c.textContent.trim()), ["up", "1", "40.00", "1.0e-7", "1.0e-5", "8.00", "1.0e-4", "2.10", "4 h"]);
  for (const [show, n] of [["up", 2], ["down", 2], ["mixed", 2], ["not", 2], ["all", 8], ["differs", 4]]) {
    choose(window, "#tshow", show);
    assert.equal(rowsOf(document).length, n, show);
  }
  choose(window, "#tshow", "changing");
  [...document.querySelectorAll("#timepatterns .tile")][1].click();
  assert.deepEqual(rowsOf(document).map((r) => r.dataset.k), ["1", "5"], "pattern 2");
  assert.match(document.querySelector("#timetable .pager").textContent, /2 features in pattern 2/);
  [...document.querySelectorAll("#timepatterns .tile")][1].click();
  assert.equal(rowsOf(document).length, 6, "clicking again clears the pattern");
  document.querySelector("#timetable th[data-k='max']").click();
  assert.equal(rowsOf(document)[0].children[8].textContent, "2.10", "largest change first, by size");
  choose(window, "#tq", "zzz-nothing");
  assert.match(document.querySelector("#timetable").textContent, /No feature matches/);
});

test("the profile draws replicates, the mean and the control series; series switch", async () => {
  const { window, document, errors } = await loadReport({ data: DATA });
  const prof = document.querySelector("#timeprofile");
  assert.equal(prof.querySelectorAll("circle[data-j]").length, 3, "one point per sample of the series");
  assert.equal(prof.querySelectorAll("circle[data-ctrl]").length, 3, "and the control series' samples");
  assert.equal(prof.querySelectorAll("path").length, 2, "mean line + dashed control line");
  assert.match(prof.querySelector("svg").textContent, /0 h.*1 h.*4 h/s);
  assert.match(prof.querySelector("svg").textContent, /dashed: DMSO/);
  assert.match(document.querySelector("#timehead").textContent, /up.*F 40\.00 · adj\. p 1\.0e-5 · largest change 2\.10 at 4 h · differs from DMSO: adj\. p 1\.0e-4/s);
  document.querySelector("#timetable tr[data-k='1']").click();
  assert.match(document.querySelector("#timehead").textContent, /down/);
  choose(window, "#tseries", "1");
  assert.equal(document.querySelectorAll("#timetable th").length, 10, "no 'differs from' column for the control series");
  assert.equal(document.querySelectorAll("#timeprofile circle[data-ctrl]").length, 0);
  assert.equal([...document.querySelectorAll("#tshow option")].some((o) => o.value === "differs"), false);
  document.querySelector("#theme").click();
  assert.ok(document.querySelector("#timeprofile svg"), "redrawn after a theme change");
  assert.deepEqual(errors, []);
});

test("the report search marks time-course rows; hostile names are escaped", async () => {
  const { window, document, errors } = await loadReport({ data: DATA });
  const box = document.querySelector("#search");
  box.value = BASE.f.label[1];
  box.dispatchEvent(new window.Event("input"));
  assert.match(document.querySelector("#timehl").textContent, /match the search/);
  const evil = "<img src=x onerror=alert(1)>";
  const bad = withData(TIME, { series: [withData(TIME.series[0], { name: evil, labels: [evil, "1 h", "4 h"], vs: evil }), withData(TIME.series[1], { name: evil })] });
  const r = await loadReport({ data: withData(BASE, { time: bad }) });
  assert.deepEqual(r.errors, []);
  assert.equal(r.document.querySelector("#timebody img"), null);
  assert.deepEqual(errors, []);
});

test("a spline series (D77): the fitted curve on an axis in hours, the control's curve dashed", async () => {
  const times = [0, 0.5, 1, 2, 4, 8, 24, 48], labels = times.map((t) => (t < 1 && t ? t * 60 + " min" : t + " h"));
  const grid = [0, 12, 24, 36, 48], gb = [[0, 0], [0.5, 0.1], [0.9, 0.3], [1.1, 0.6], [1.2, 1]];
  const spl = (name, idx, off, vs) => withData(series(name, idx, off, vs), {
    times, labels, stime: [0, 4, 48], model: "spline", df: 2, grid, gb,
    cf: idx.map(() => [1, 0.5]), lv: idx.map(() => 20), fc: idx.map(() => times.map(() => 0)) });
  const time = withData(TIME, { series: [spl("Drug", [0, 1, 2, 3], 0, "DMSO"), spl("DMSO", [0, 1, 2, 3], 3)] });
  const { document, errors } = await loadReport({ data: withData(BASE, { time }) });
  assert.deepEqual(errors, []);
  assert.match(document.querySelector("#timebody").textContent, /time as a natural spline in hours, 2 df/);
  const prof = document.querySelector("#timeprofile");
  const paths = [...prof.querySelectorAll("path")].filter((p) => (p.getAttribute("d") || "").split("L").length === grid.length);
  assert.equal(paths.length, 2, "the series' curve and the control's, one segment per grid point");
  assert.ok(paths.some((p) => p.getAttribute("stroke-dasharray") === "5 4"));
  assert.match(prof.querySelector("svg").textContent, /line: spline, 2 df/);
  assert.match(prof.querySelector("svg").textContent, /time \(h\)/);
  assert.equal(prof.querySelectorAll("circle[data-j]").length, 3);
  const xs = [...prof.querySelectorAll("circle[data-j]")].map((c) => +c.getAttribute("cx"));
  assert.ok((xs[1] - xs[0]) * 5 < xs[2] - xs[1], "x is in hours: 0 -> 4 h is much shorter than 4 -> 48 h");
});
