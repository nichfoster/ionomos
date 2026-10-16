// Hostile payloads (round-4 audit): every section of report.js against data it was not made for. Zero
// comparisons, one sample, one feature, 100,000 features x 50 samples, names with quotes, `<`, `&`, unicode
// and 300 characters, every optional key missing, the payload as v0.18 wrote it, and the TMT section with no
// plexes. Each test visits every QC tab and asserts the page raised no error; the volume test also bounds
// the time it takes.
import { test } from "node:test";
import assert from "node:assert/strict";

import { loadReport, baseData, withData, blobText, until } from "../lib/harness.mjs";

const BASE = baseData();
const SVG_ZIP = { "ionomos.export.v1": { zip_format: "svg" } };  // jsdom draws no PNG: the zip of SVGs only

/** Click every QC tab in turn (the tab list is drawn again on each click). Returns the keys visited. */
function everyQcTab(document) {
  const keys = [...document.querySelectorAll("#qc .tabs button")].map((b) => b.dataset.k);
  for (const k of keys) document.querySelector(`#qc .tabs button[data-k='${k}']`).click();
  return keys;
}
/** A section's "could not be drawn" box: report.js shows it when a renderer throws (safe()). */
const broken = (document) => [...document.querySelectorAll(".empty")].map((e) => e.textContent).filter((t) => /could not be drawn/.test(t));

/** A consistent payload of nF features x nS samples in two conditions, one comparison, from BASE. */
function synth(nF, nS, opts = {}) {
  const name = opts.name || ((i) => `GENE${i}`);
  const cond = Array.from({ length: nS }, (_, j) => (j % 2 ? "Drug" : "DMSO"));
  const v = Array.from({ length: nF }, (_, i) => Array.from({ length: nS }, (_, j) => 12 + ((i * 7 + j * 13) % 97) / 10));
  const fc = Array.from({ length: nF }, (_, i) => (((i * 37) % 200) - 100) / 40);
  const p = Array.from({ length: nF }, (_, i) => (i % 50 === 0 ? 1e-9 : 0.5));
  const q = p.map((x) => (x < 0.5 ? 1e-8 : 0.6));
  return withData(BASE, {
    v, samples: Array.from({ length: nS }, (_, j) => `s${j + 1}`), cond, conditions: ["DMSO", "Drug"], rep: cond.map((_, j) => (j >> 1) + 1),
    f: { id: Array.from({ length: nF }, (_, i) => `P${String(i).padStart(5, "0")}`), label: Array.from({ length: nF }, (_, i) => name(i)),
      desc: Array.from({ length: nF }, (_, i) => `description ${i}`) },
    imp: null, enr: [], gsea: [],
    comps: [{ name: "Drug vs DMSO", slug: "Drug_vs_DMSO", t1: "Drug", t2: "DMSO", fc, p, q, ciL: fc.map((x) => x - 0.2), ciR: fc.map((x) => x + 0.2),
      nt: fc.map(() => nS / 2), nc: fc.map(() => nS / 2), a: v.map((row) => row[0]), size: [nS / 2, nS / 2], onoff: [], pi0: 0.8, pshape: "signal" }],
    qc: {
      pca: { scores: Array.from({ length: nS }, (_, j) => [j % 2 ? 1 : -1, (j % 5) / 5]), percent: [40, 20], n: Math.min(nF, 500) },
      correlation: { matrix: Array.from({ length: nS }, () => Array(nS).fill(0.9)), order: Array.from({ length: nS }, (_, j) => j), complete_rows: nF },
      missing: { all: nF, complete: nF, under_half: 0, rows: [], row_ids: [], total: 0, curve: [[0, 0], [1, nF]] },
      box_before: cond.map(() => ({ lo: 11, hi: 22, q1: 14, median: 16, q3: 18, n: nF })), box_after: cond.map(() => ({ lo: 11, hi: 22, q1: 14, median: 16, q3: 18, n: nF })),
      features_per_sample: cond.map(() => nF), features_per_sample_after: cond.map(() => nF),
      cv: { DMSO: { hist: [1, 2, 3], median: 0.1 }, Drug: { hist: [3, 2, 1], median: 0.2 } }, heatmap: null,
    },
    ...(opts.patch || {}),
  });
}

test("zero comparisons: every section and QC tab draws, nothing throws, the export still works", async () => {
  const data = withData(BASE, { comps: [], imp: null, enr: [], gsea: [], qc: { ...BASE.qc, heatmap: null, power: undefined } });
  const { window, document, errors } = await loadReport({ data, storage: SVG_ZIP });
  assert.match(document.querySelector("#differential-body").textContent, /No comparisons were made/);
  const keys = everyQcTab(document);
  assert.ok(keys.length >= 6, keys.join(","));
  assert.ok(document.querySelector("#compare").hidden, "no Compare section");
  assert.match(document.querySelector("#onoffbody").textContent, /Needs a comparison between two groups/);
  assert.match(document.querySelector("#enrich").textContent, /Enrichment|not run|needs/i);
  // the QC charts still export, and so does the whole report
  document.querySelector("#qc .tabs button[data-k='pca']").click();
  const svgBtn = [...document.querySelectorAll("#qc .tools button")].find((b) => b.textContent === "SVG");
  svgBtn.click();
  assert.equal(window.__downloads.length, 1);
  document.querySelector("#slides").click();
  await until(() => window.__downloads.length === 2);
  assert.match(window.__downloads[1].name, /_figures\.zip$/);
  assert.equal(document.querySelector("#search"), null, "no search box without a comparison to search in");
  assert.deepEqual(broken(document), []);
  assert.deepEqual(errors, []);
});

test("one sample, no comparison: every QC tab shows its empty state, nothing throws", async () => {
  const data = withData(BASE, {
    samples: ["s1"], cond: ["C1"], conditions: ["C1"], rep: [1], v: [[1.23], [2.5]],
    f: { id: ["p1", "p2"], label: ["P1", "P2"], desc: ["one", "two"] }, imp: null, comps: [], enr: [], gsea: [],
    qc: {
      pca: { scores: [[0]], percent: [100], n: 2 }, correlation: { matrix: [[1]], order: [0], complete_rows: 2 },
      missing: { all: 2, complete: 2, under_half: 0, rows: [], row_ids: [], total: 0, curve: [[0, 2], [1, 2]] },
      box_before: [{ lo: 1, hi: 3, q1: 1.2, median: 1.8, q3: 2.4, n: 2 }], box_after: [{ lo: 1, hi: 3, q1: 1.2, median: 1.8, q3: 2.4, n: 2 }],
      features_per_sample: [2], features_per_sample_after: [2], cv: { C1: { hist: [], median: null } }, heatmap: null,
    },
  });
  const { document, errors } = await loadReport({ data });
  everyQcTab(document);
  document.querySelector("#qc .tabs button[data-k='pca']").click();
  assert.match(document.querySelector("#qcbody").textContent, /PC1/, "a PCA with one component still draws");
  assert.deepEqual(broken(document), []);
  assert.deepEqual(errors, []);
});

test("one feature: volcano, table, detail, every QC tab, the CSV and the zip", async () => {
  const data = synth(1, 6);
  const { window, document, errors } = await loadReport({ data, storage: SVG_ZIP });
  assert.equal(document.querySelectorAll("#volcano svg circle").length, 1);
  assert.equal(document.querySelectorAll("#table tbody tr[data-i]").length, 1);
  document.querySelector("#table tbody tr[data-i]").click();
  assert.equal(document.querySelector("#detail h4").textContent, "GENE0");
  everyQcTab(document);
  document.querySelector("#csv").click();
  const csv = await blobText(window, window.__downloads.at(-1).blob);
  assert.equal(csv.split("\n").length, 2, "a header and the one row");
  document.querySelector("#slides").click();
  await until(() => window.__downloads.at(-1).name.endsWith(".zip"));
  assert.deepEqual(broken(document), []);
  assert.deepEqual(errors, []);
});

test("100,000 features x 50 samples: draws in bounded time, search and table work, QC tabs draw", async () => {
  const nF = 100000, nS = 50;
  const data = synth(nF, nS);
  const t0 = Date.now();
  const { window, document, errors } = await loadReport({ data });
  const ms = Date.now() - t0;
  assert.equal(document.querySelectorAll("#volcano svg circle").length, nF, "a point per feature");
  assert.match(document.querySelector("#table .pager").textContent, /page 1 of/);
  const box = document.querySelector("#search");
  const t1 = Date.now();
  box.value = "GENE9999*";
  box.dispatchEvent(new window.Event("input", { bubbles: true }));
  assert.match(document.querySelector("#searchinfo").textContent, /^11 matches/, "GENE9999 and GENE99990-GENE99999");
  const searchMs = Date.now() - t1;
  for (const [k, chart] of [["rank", "#rankchart"], ["mv", "#mvchart"], ["ids", "#idchart"], ["dist", "#distchart"]]) {
    document.querySelector(`#qc .tabs button[data-k='${k}']`).click();
    assert.ok(document.querySelector(chart + " svg"), `${k} drawn`);
  }
  assert.deepEqual(broken(document), []);
  assert.deepEqual(errors, []);
  assert.ok(ms < 180000, `the page is drawn in ${ms} ms`);
  assert.ok(searchMs < 60000, `a search takes ${searchMs} ms`);
});

// a 300-character name made of everything that can go wrong in HTML, SVG, CSV, file names and the address
const LONG = ("<b>&amp;'\"é中文🧬‮`${x}` -->" + "Ω".repeat(20)).padEnd(300, "x");
const HOSTILE = [`<img src=x onerror="window.__xss=1">`, `"'><svg onload=window.__xss=2>`, "a&b;c", "=SUM(A1)", LONG, "İstanbul ß ﬁ", "\u0000ctrl\u0007"];

test("names with quotes, <, &, unicode and 300 characters: every section shows them as text", async () => {
  const nF = 40;
  const data = synth(nF, 6, { name: (i) => HOSTILE[i % HOSTILE.length] + (i >= HOSTILE.length ? i : "") });
  data.samples = data.samples.map((s, j) => HOSTILE[j % HOSTILE.length] + j);
  data.conditions = [HOSTILE[0], LONG];
  data.cond = data.cond.map((c) => (c === "DMSO" ? HOSTILE[0] : LONG));
  data.comps[0] = { ...data.comps[0], name: LONG + " vs " + HOSTILE[0], slug: LONG, t1: LONG, t2: HOSTILE[0],
    onoff: [[3, "t", 3, 3]], onoffOut: [[HOSTILE[1], HOSTILE[2], "c", 3, 3, 12.5, LONG]] };
  data.comps[1] = { ...data.comps[0], name: HOSTILE[1], slug: HOSTILE[1] };
  data.f.desc = data.f.desc.map((_, i) => HOSTILE[(i + 3) % HOSTILE.length]);
  data.qc.cv = { [HOSTILE[0]]: { hist: [1, 2], median: 0.1 }, [LONG]: { hist: [2, 1], median: 0.2 } };
  data.qc.scorecard = data.samples.map((s, j) => ({ sample: s, condition: data.cond[j], status: j ? "ok" : "fail", ids: 40, missing_pct: 0, flags: j ? [] : [HOSTILE[1]] }));
  data.qc.heatmap = { rows: [0, 1, 2, 3], cols: [0, 1, 2, 3, 4, 5], total_significant: 4, values: [0, 1, 2, 3].map(() => [1, -1, 0.5, -0.5, 0, null]) };
  data.enr = [{ comparison: data.comps[0].name, direction: "up", library: HOSTILE[1], hits: 2, background: 40,
    terms: [{ term: LONG, k: 2, K: 5, p: 1e-4, q: 0.01, log2_odds: 2, genes: [HOSTILE[0], LONG] }] }];
  const { window, document, errors } = await loadReport({ data });
  document.querySelector("#sigonly").click();
  assert.ok(document.querySelector("#table").textContent.includes("İstanbul ß ﬁ"), "unicode kept");
  everyQcTab(document);
  document.querySelector("#table tbody tr[data-i]").click();
  // volcano and heatmap tooltips
  const hit = document.querySelector("#volcano svg rect[style*='crosshair']");
  const c = document.querySelector("#volcano svg circle");
  hit.dispatchEvent(new window.MouseEvent("mousemove", { clientX: +c.getAttribute("cx"), clientY: +c.getAttribute("cy"), bubbles: true }));
  document.querySelectorAll("button.qhelp").forEach((b) => b.click());
  document.querySelector("#enrich tbody tr").click();
  assert.equal(window.__xss, undefined, "no injected script ran");
  assert.equal(document.querySelectorAll("main img, #tip img, main svg svg[onload]").length, 0, "no injected element");
  assert.ok(document.querySelector("#detail h4").textContent.length > 0);
  assert.ok(document.querySelector("#tiles").textContent.includes(LONG + " vs "), "a 300-character comparison name is shown whole");
  // a file named from a hostile comparison is still a safe file name
  document.querySelector("#csv").click();
  const name = window.__downloads.at(-1).name;
  assert.match(name, /^[A-Za-z0-9][A-Za-z0-9._+-]*\.csv$/);
  assert.ok(name.length <= 120, `${name.length} characters`);
  const csv = await blobText(window, window.__downloads.at(-1).blob);
  assert.ok(!/^"=|,"=/m.test(csv), "a formula-looking cell is defused");
  assert.deepEqual(broken(document), []);
  assert.deepEqual(errors, []);
});

test("every optional key missing: the report still draws", async () => {
  const data = { ...BASE, qc: {}, f: { id: BASE.f.id, label: BASE.f.label, desc: BASE.f.desc } };
  for (const k of ["imp", "F", "plex", "tmtqc", "help", "exportDefaults", "roles", "dose", "cys", "time", "phos", "evidence", "enrNote", "rep", "gsea", "enr", "notes", "files", "imputationLabel", "sourceName"]) delete data[k];
  data.settings = { log2fc: 1, alpha: 0.05, use_adjusted: true, top_labels: 10 };
  data.comps = BASE.comps.map((c) => ({ name: c.name, slug: c.slug, t1: c.t1, t2: c.t2, fc: c.fc, p: c.p, q: c.q }));
  const { window, document, errors } = await loadReport({ data });
  assert.ok(document.querySelector("#volcano svg circle"), "volcano drawn");
  assert.ok(document.querySelector("#table tbody tr[data-i]"), "table drawn");
  document.querySelector("#table tbody tr[data-i]").click();
  assert.match(document.querySelector("#detail").textContent, /Drug vs DMSO/);
  everyQcTab(document);
  document.querySelector("#ma").click();
  document.querySelector("#csv").click();
  assert.equal(window.__downloads.length, 1);
  const box = document.querySelector("#search");
  box.value = "desc:protein";
  box.dispatchEvent(new window.Event("input", { bubbles: true }));
  assert.deepEqual(broken(document), []);
  assert.deepEqual(errors, []);
});

test("no comparisons and no notes: the report still draws (a payload without the notes list)", async () => {
  const data = { ...BASE, comps: [] };
  delete data.notes;
  const { document, errors } = await loadReport({ data });
  assert.match(document.querySelector("#differential-body").textContent, /No comparisons were made\./);
  assert.ok(document.querySelector("#qc .tabs button"), "the rest of the page is drawn");
  assert.deepEqual(errors, []);
});

test("a payload in the v0.18 shape (no settings.filter, no tmtqc, no onoffOut) draws and filters as before", async () => {
  const data = { ...BASE, settings: { ...BASE.settings } };
  delete data.settings.filter;
  delete data.tmtqc;
  data.comps = BASE.comps.map((c) => { const x = { ...c }; delete x.onoffOut; return x; });
  const { document, errors } = await loadReport({ data });
  assert.ok(document.querySelector("#volcano svg circle"));
  assert.ok(document.querySelector("#onoffbody").textContent.length > 0);
  assert.ok(document.querySelector("#tmt").hidden, "no TMT section");
  everyQcTab(document);
  assert.deepEqual(broken(document), []);
  assert.deepEqual(errors, []);
});

test("the TMT section with no plexes: tiles and the reason, no charts, nothing in the export", async () => {
  const tmtqc = { plexes: [], tables: ["tmt-report/abundance_protein_MD.tsv"], notes: ["No psm.tsv was found next to the TMT tables."],
    workflow: { label: "TMT-16", quant: "MS2" }, labelling: {}, purity: {}, limits: { min_labelling: 0.95, min_purity: 0.7 } };
  const { window, document, errors } = await loadReport({ data: { ...BASE, tmtqc } });
  assert.equal(document.querySelector("#tmt").hidden, false, "the section is shown");
  assert.equal(document.querySelector("#navtmt").hidden, false);
  const body = document.querySelector("#tmtbody");
  assert.match(body.textContent, /TMT-16/);
  assert.match(body.textContent, /No psm\.tsv was found next to the TMT tables\./);
  assert.match(body.textContent, /Plexes0/, "zero plexes counted");
  assert.equal(body.querySelector("svg"), null, "no chart without a plex");
  document.querySelector("#xopen").click();
  const opts = [...document.querySelectorAll("#xfig option")].map((o) => o.textContent);
  assert.ok(!opts.some((t) => /TMT/.test(t)), `no TMT figure offered: ${opts.join(" | ")}`);
  document.querySelector("#xclose").click();
  // and an empty tmtqc (nothing found at all) hides the section
  const empty = await loadReport({ data: { ...BASE, tmtqc: { plexes: [], tables: [] } } });
  assert.equal(empty.document.querySelector("#tmt").hidden, true);
  assert.deepEqual(empty.errors, []);
  assert.deepEqual(broken(document), []);
  assert.deepEqual(errors, []);
  assert.equal(window.__xss, undefined);
});

test("the TMT section with a plex whose channels have no signal and no labelling numbers", async () => {
  const tmtqc = { plexes: [{ name: "<b>plex1</b>", psms: 0, ch: [{ c: "126", n: "s1", cond: "A", na: false, ref: false, sig: null, q: null, rel: null, flags: [] },
    { c: "127N", n: "", cond: "", na: true, ref: false, sig: 0, q: null, rel: null, flags: ["no signal"] }], labelling: {} }],
  tables: [], notes: [], workflow: {}, labelling: {}, purity: { bins: [] }, limits: {} };
  const { document, errors } = await loadReport({ data: { ...BASE, tmtqc } });
  const body = document.querySelector("#tmtbody");
  assert.match(body.textContent, /No reporter signal in this plex/);
  assert.ok(body.textContent.includes("<b>plex1</b>"), "the plex name is text");
  assert.equal(body.querySelector("b b"), null);
  assert.deepEqual(broken(document), []);
  assert.deepEqual(errors, []);
});

test("values too large for a step to move (past 2^53) end their axis instead of hanging the page", { timeout: 60000 }, async () => {
  const huge = { lo: 1e17, hi: 1e17 + 16, q1: 1e17, median: 1e17 + 16, q3: 1e17 + 16, n: 3 };
  const data = withData(BASE, { qc: { ...BASE.qc, box_after: BASE.samples.map(() => huge), box_before: BASE.samples.map(() => huge) } });
  const { document, errors } = await loadReport({ data });
  document.querySelector("#qc .tabs button[data-k='dist']").click();
  assert.ok(document.querySelector("#distchart svg"), "the chart is drawn");
  assert.deepEqual(errors, []);
});

test("a dose series with 150,000 curves draws its potency plot (no argument-count overflow)", async () => {
  const nF = 150000, nS = 2;
  const data = synth(nF, nS);
  data.v = data.v.map(() => [1, 2]);
  const k = Array.from({ length: nF }, (_, i) => i);
  data.dose = { ran: true, found: true, alpha: 0.05, fcLim: 0.45, series: [{ name: "cmpd", unit: "nM", doses: [1e-9, 1e-8, 1e-7], controls: ["s1"],
    samples: [0, 1], sdose: [0, 1e-7], i: k, cls: k.map((i) => (i % 3 ? "not" : "up")), pec50: k.map((i) => 6 + (i % 100) / 50), ciL: k.map(() => null), ciR: k.map(() => null),
    ec50: k.map(() => null), slope: k.map(() => 1), front: k.map(() => 1), back: k.map(() => 2), fc: k.map((i) => (i % 7) / 7), p: k.map(() => 0.01), q: k.map(() => 0.02),
    rel: k.map(() => 1), r2: k.map(() => 0.9), n: k.map(() => 3), y: k.map(() => [0, 1]) }] };
  data.comps = [];
  const { document, errors } = await loadReport({ data });
  assert.ok(document.querySelector("#dosescatter svg circle"), "the potency plot is drawn");
  assert.deepEqual(broken(document), []);
  assert.deepEqual(errors, []);
});
