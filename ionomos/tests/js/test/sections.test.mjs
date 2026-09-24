// Section renderers of report.js against every data shape the brief calls
// out: the fixture baseline, zero comparisons, one sample, ratio (isoDTB)
// data, a 10k-feature × 50-sample set, all p-values missing, and the
// dark/light theme toggle. Each payload is loaded through the same code path
// a browser runs (the inlined report.js source, evaluated in the fixture DOM).
import { test } from "node:test";
import assert from "node:assert/strict";

import { loadReport, baseData, withData } from "../lib/harness.mjs";

const BASE = baseData();

test("baseline fixture renders every section", async () => {
  const { document, errors } = await loadReport();
  assert.deepEqual(errors, [], "no uncaught page errors during setup");
  assert.ok(document.querySelector("#tiles .tile"), "tiles rendered");
  assert.ok(document.querySelector("#differential-body svg"), "volcano SVG rendered");
  assert.ok(document.querySelector("#table tbody tr"), "table has rows");
  assert.ok(document.querySelector("#overview"), "overview rendered");
  assert.equal(document.title, BASE.title + " — Ionomos report");
});

test("zero comparisons: empty-state message, no crash, notes mentioned", async () => {
  const data = withData(BASE, { comps: [], imp: null, enr: [], qc: { heatmap: null } });
  const { document } = await loadReport({ data });
  const body = document.querySelector("#differential-body");
  assert.ok(body, "differential section exists");
  assert.match(body.textContent, /No comparisons were made/);
  assert.ok(document.querySelector("#tiles .tile"), "tiles still render");
  assert.ok(!document.querySelector("#differential-body svg"), "no volcano without comparisons");
});

test("zero comparisons with notes points at them", async () => {
  const data = withData(BASE, { comps: [], imp: null, enr: [], notes: ["Drifted RT on DMSO_2"] });
  const { document } = await loadReport({ data });
  assert.match(document.querySelector("#differential-body").textContent, /see the notes above/);
});

test("one sample: tiles, QC and empty comparisons all render", async () => {
  const data = withData(BASE, {
    samples: ["s1"],
    cond: ["C1"],
    conditions: ["C1"],
    v: [[1.23]],
    f: { id: ["p1"], label: ["P1_protein"], desc: ["one protein"] },
    imp: null,
    comps: [],
    enr: [],
    qc: {
      pca: { scores: [[0, 0]], percent: [80, 20], n: 1 },
      correlation: { matrix: [[1]], order: [0], complete_rows: 1 },
      missing: { all: 1, complete: 1, under_half: 0, rows: [], row_ids: [], total: 0, curve: [[0, 0], [0.5, 0], [1, 0]] },
      box_before: [{ lo: 1.0, hi: 1.5, q1: 1.1, median: 1.2, q3: 1.4, n: 1 }],
      box_after: [{ lo: 1.0, hi: 1.5, q1: 1.1, median: 1.2, q3: 1.4, n: 1 }],
      features_per_sample: [1],
      features_per_sample_after: [1],
      cv: { C1: [0.1] },
      heatmap: null,
    },
  });
  const { document } = await loadReport({ data });
  const tiles = [...document.querySelectorAll("#tiles .tile")].map((t) => t.textContent);
  assert.ok(tiles.some((t) => /1/.test(t) && /Sample/i.test(t)), `samples tile shows 1: ${tiles.join(" | ")}`);
  assert.ok(document.querySelector("#qc .tabs button"), "QC tabs render with one sample");
  assert.ok(document.querySelector("#differential-body"), "differential section present");
  assert.match(document.querySelector("#differential-body").textContent, /No comparisons were made/);
});

test("ratio (isoDTB) data: no MA plot, Site column, ratio axis label, CV note", async () => {
  const data = withData(BASE, {
    kind: "ratio",
    levelWord: "site",
    levelTitle: "site",
    comps: [
      {
        name: "S1 vs 0", slug: "S1_vs_0", t1: "S1", t2: null,
        fc: [1.2, -0.4], p: [0.01, 0.6], q: [0.02, 0.6],
        ciL: [0.9, -0.8], ciR: [1.5, 0.0], nt: 2, nc: 0, a: [0, 1],
      },
    ],
  });
  const { document } = await loadReport({ data });
  const ma = document.querySelector("#ma");
  assert.ok(ma, "MA control exists");
  assert.equal(ma.disabled, true, "MA toggle disabled for ratio data");
  const headers = [...document.querySelectorAll("#table th")].map((t) => t.textContent);
  assert.ok(headers.includes("Site"), `Site column for ratio data: ${headers.join(", ")}`);
  const axis = [...document.querySelectorAll("#differential-body svg text")].map((t) => t.textContent);
  assert.ok(axis.some((t) => t === "log2 H/L"), `ratio axis label: ${axis.join(" | ")}`);
  // cut-off note still counts tested features
  assert.match(document.querySelector("#vcount").textContent, /2 tested/);
});

test("all p-values missing: nothing significant, table empty-state, no crash", async () => {
  const nF = BASE.v.length;
  const data = withData(BASE, {
    comps: [withData(BASE.comps[0], { p: Array(nF).fill(null), q: Array(nF).fill(null) })],
  });
  const { document } = await loadReport({ data });
  assert.match(document.querySelector("#vcount").textContent, /of 0 tested/);
  assert.match(
    document.querySelector("#table").textContent,
    /No significant features at these cut-offs/
  );
  assert.ok(document.querySelector("#differential-body svg"), "volcano still renders");
});

test("10k features × 50 samples renders, volcano has 10k points, table paginates", async () => {
  const nF = 10000, nS = 50;
  const v = Array.from({ length: nF }, (_, i) =>
    Array.from({ length: nS }, (_, j) => 12 + ((i * 7 + j * 13) % 97) / 10)
  );
  const fc = Array.from({ length: nF }, (_, i) => (((i * 37) % 200) - 100) / 40);
  const p = Array.from({ length: nF }, (_, i) => (i % 50 === 0 ? 1e-9 : 0.5));
  const q = p.map((x) => (x < 0.5 ? 1e-8 : 0.6));
  const data = withData(BASE, {
    v,
    samples: Array.from({ length: nS }, (_, j) => `s${j + 1}`),
    cond: Array.from({ length: nS }, (_, j) => (j % 2 ? "Drug" : "DMSO")),
    f: {
      id: Array.from({ length: nF }, (_, i) => `prot${i}`),
      label: Array.from({ length: nF }, (_, i) => `GENE${i}`),
      desc: Array.from({ length: nF }, (_, i) => `description ${i}`),
    },
    comps: [
      {
        name: "Drug vs DMSO", slug: "Drug_vs_DMSO", t1: "Drug", t2: "DMSO",
        fc, p, q,
        ciL: fc.map((x) => x - 0.2), ciR: fc.map((x) => x + 0.2),
        nt: nS / 2, nc: nS / 2, a: v.map((row, i) => row[0]),
      },
    ],
    imp: null,
    qc: {
      pca: { scores: [], percent: [], n: 0 }, // PCA skipped on this synthetic set
      correlation: { matrix: [], order: [], complete_rows: 0 },
      missing: { all: nF, complete: nF, under_half: 0, rows: [], row_ids: [], total: 0, curve: [[0, 0], [1, 0]] },
      box_before: [], box_after: [],
      features_per_sample: Array.from({ length: nS }, () => nF),
      features_per_sample_after: Array.from({ length: nS }, () => nF),
      cv: { DMSO: [], Drug: [] },
      heatmap: null,
    },
  });
  const started = Date.now();
  const { document } = await loadReport({ data });
  const ms = Date.now() - started;
  const circles = document.querySelectorAll("#differential-body svg circle").length;
  assert.equal(circles, nF, "one volcano point per feature");
  // significant at p<=0.01: i % 50 == 0 (200 features); of those, |log2FC| >= 0.5
  // keeps 150 (fc is exactly 0 every 200 features), so the table shows 150 rows
  const expectedRows = p.reduce((n, x, i) => n + (x < 0.01 && Math.abs(fc[i]) >= 0.5 ? 1 : 0), 0);
  assert.match(
    document.querySelector("#table").textContent,
    new RegExp(`${expectedRows} rows · page 1 of`)
  );
  // pagination: next page starts with the next significant feature
  const next = [...document.querySelectorAll("#table button")].find((b) => b.textContent === "›");
  assert.ok(next, "pager next button exists");
  next.click();
  assert.match(
    document.querySelector("#table").textContent,
    new RegExp(`page 2 of`)
  );
  assert.ok(document.querySelector("#tiles .tile"), "tiles render");
  assert.ok(ms < 30000, `render stays interactive (${ms} ms)`);
});

test("dark/light theme toggle redraws without error", async () => {
  const { document } = await loadReport();
  const html = document.documentElement;
  const btn = document.querySelector("#theme");
  assert.ok(btn, "theme button exists");
  assert.equal(html.getAttribute("data-theme"), null);
  btn.click();
  assert.equal(html.getAttribute("data-theme"), "dark");
  btn.click();
  assert.equal(html.getAttribute("data-theme"), "light");
  // the volcano was re-rendered by the redraw, not emptied
  assert.ok(document.querySelector("#differential-body svg circle"), "volcano redrawn after theme change");
});
