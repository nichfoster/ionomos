// "Export for slides" with the dose-response, time-course and liganded-site sections (D68): the zip holds the
// potency plot and the most relevant curves of each compound, each time series' patterns and its most significant
// features, each compound's liganded-site rank plot and the sites x compounds selectivity map, all in the export
// style; the page is left as it was. Synthetic payloads in the shapes doseresponse / timecourse / cys write.
import { test } from "node:test";
import assert from "node:assert/strict";

import { loadReport, baseData, withData, blobBytes, unzip, until } from "../lib/harness.mjs";

const BASE = baseData();
const KEY = "ionomos.export.v1";

function doseSeries(name, idx) {
  const pick = (a) => idx.map((_, k) => a[k % a.length]);
  return {
    name, unit: "nM", doses: [1e-9, 1e-8, 1e-7, 1e-6], samples: [0, 1, 2, 3, 4, 5], sdose: [0, 0, 0, 1e-8, 1e-7, 1e-6], controls: ["DMSO"], i: idx,
    cls: pick(["down", "up", "not", "unclear"]), n: pick([7]), pec50: pick([7.2, 6.1, 8.0, 5.5]), ciL: pick([7.0, 5.8, null, 4.0]), ciR: pick([7.4, 6.4, null, 7.0]),
    ec50: pick([63.1, 794, 10, 3162]), slope: pick([1.2, 0.9, 0.5, 3]), front: pick([1, 1, 1, 1]), back: pick([0.25, 3.1, 1.02, 1.3]), fc: pick([-1.9, 1.6, 0.02, 0.3]),
    p: pick([1e-6, 3e-5, 0.6, 0.04]), q: pick([1e-5, 1e-4, 0.8, 0.1]), rel: pick([5.1, 4.2, 0.1, 0.9]), r2: pick([0.97, 0.93, 0.02, 0.4]),
    y: idx.map((_, k) => [0.02, -0.03, 0.01, k % 2 ? 0.4 : -0.5, k % 2 ? 1.1 : -1.4, k % 2 ? 1.6 : -2.0]), skipped: 0,
  };
}
function timeSeries(name, idx, off, vs) {
  const pick = (a) => idx.map((_, k) => a[k % a.length]);
  return {
    name, unit: "h", times: [0, 1, 4], labels: ["0 h", "1 h", "4 h"], conds: [name + "_0h", name + "_1h", name + "_4h"], samples: [0, 1, 2].map((j) => j + off), stime: [0, 1, 4], i: idx,
    cls: pick(["up", "down", "not", "mixed"]), pat: pick([1, 2, null, 1]), F: pick([40, 22, 0.4, 9]), p: pick([1e-7, 1e-5, 0.7, 0.001]), q: pick([1e-5, 1e-4, 0.9, 0.01]),
    tt: pick([8, -6, 0.2, 1]), tq: pick([1e-4, 1e-3, 0.9, 0.5]), max: pick([2.1, -1.8, 0.1, 1.4]), peak: pick([2, 2, 1, 1]),
    fc: idx.map((_, k) => [[0, 1, 2.1], [0, -0.9, -1.8], [0, 0.1, 0], [0, 1.4, -1.1]][k % 4]),
    patterns: [{ n: 4, profile: [0, 1.1, 2] }, { n: 2, profile: [0, -0.9, -1.8] }], vs: vs || "", iq: vs ? pick([1e-4, 0.3, 0.9, 0.02]) : null, untested: 0,
  };
}
function compound(name, r, cls) {
  const count = (k) => cls.filter((c) => c === k).length;
  return { name, reps: 3, minRep: 2, counts: { liganded: count(0), inconsistent: count(1), "not liganded": count(2), "too few": count(3) },
    assessed: cls.filter((c) => c < 3).length, fraction: 0.3, r, n: r.map((v) => (v == null ? 0 : 3)), over: cls.map((c) => (c === 0 ? 3 : 0)), cls };
}
export const DATA = withData(BASE, {
  dose: { ran: true, found: true, alpha: 0.05, fcLim: 0.45, reason: "", series: [doseSeries("Cmpd", [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15])] },
  time: { ran: true, found: true, alpha: 0.05, lfc: 1, model: "~0 + condition", reason: "", series: [timeSeries("Drug", [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11], 3, "DMSO"), timeSeries("DMSO", [0, 1, 2, 3], 0)] },
  cys: {
    ran: true, rule: "R ≥ 4 (heavy / light) in at least 2 replicates", ratio: 4, minRep: 2, dir: "high", classes: ["liganded", "inconsistent", "not liganded", "too few"],
    i: [0, 1, 2, 3, 4, 5, 6, 7], nlig: [2, 1, 1, 0, 0, 0, 1, 0], sel: [2, 1, 1, 0, 0, 0, 3, 0], selNames: ["", "selective", "shared", "unresolved"], selectivity: { selective: 2, shared: 1, unresolved: 1 },
    compounds: [compound("CmpdA", [3.1, 2.4, 0.1, 2.2, 0.0, -0.3, 2.6, null], [0, 0, 2, 1, 2, 2, 0, 3]), compound("CmpdB", [2.8, 0.2, 2.9, 0.1, 0.3, 0.0, null, null], [0, 2, 0, 2, 2, 2, 3, 3])],
    proteins: [], notes: [],
  },
});

test("Export for slides holds the dose-response, time-course and liganded-site figures, and leaves the page as it was", async () => {
  const { window, document, errors } = await loadReport({ data: DATA, storage: { [KEY]: { zip_format: "svg" } } });
  assert.deepEqual(errors, []);
  const before = { dose: document.querySelector("#dosehead").textContent, time: document.querySelector("#timehead").textContent, cys: document.querySelector("#cysrank svg").outerHTML.length };
  document.querySelector("#slides").click();
  await until(() => window.__downloads.length === 1, 30000);
  assert.deepEqual(errors, []);
  const files = unzip(await blobBytes(window, window.__downloads[0].blob));
  assert.ok(files.every((f) => f.crcOk));
  const figs = files.filter((f) => f.name.includes("/figures/")).map((f) => f.name.replace(/^.*\/\d\d_/, ""));
  const named = (re) => figs.filter((n) => re.test(n));
  assert.deepEqual(named(/^dose_potency_/), ["dose_potency_Cmpd.svg"]);
  // the 6 most relevant regulated curves (up / down; the payload is sorted by relevance), one figure each
  const regulated = [0, 1, 4, 5, 8, 9].map((i) => "dose_curve_" + (BASE.f.label[i] || BASE.f.id[i]) + ".svg");
  assert.deepEqual(named(/^dose_curve_/).sort(), regulated.map((n) => n.replace(/[^\w.+-]+/g, "_")).sort());
  assert.deepEqual(named(/^time_patterns_/).sort(), ["time_patterns_DMSO.svg", "time_patterns_Drug.svg"]);
  // the most significant changing features of each series (Drug: 6 of its 9; DMSO: its 3), named by series and feature
  assert.equal(named(/^time_course_Drug_/).length, 6, figs.join(", "));
  assert.equal(named(/^time_course_DMSO_/).length, 3, figs.join(", "));
  assert.deepEqual(named(/^liganded_rank_/).sort(), ["liganded_rank_CmpdA.svg", "liganded_rank_CmpdB.svg"]);
  assert.deepEqual(named(/^liganded_selectivity/), ["liganded_selectivity.svg"]);
  const svgOf = (n) => new window.DOMParser().parseFromString(new TextDecoder().decode(files.find((f) => f.name.endsWith("_" + n)).data), "image/svg+xml");
  for (const n of ["time_patterns_Drug.svg", "liganded_selectivity.svg", "dose_potency_Cmpd.svg"]) {
    const doc = svgOf(n);
    assert.equal(doc.querySelector("parsererror"), null, n + " is well-formed");
    assert.ok(!/var\(|class=|style=/.test(new window.XMLSerializer().serializeToString(doc)), n + ": no CSS");
    assert.match(doc.querySelector("desc").textContent, /Export style: 16:9 slide/);
  }
  const pat = svgOf("time_patterns_Drug.svg"), ptexts = [...pat.querySelectorAll("text")].map((t) => t.textContent);
  assert.ok(ptexts.includes("Pattern 1 · 4 features") && ptexts.includes("Pattern 2 · 2 features"));
  assert.equal(pat.querySelector("title").textContent, "Time-course patterns");
  const sel = svgOf("liganded_selectivity.svg"), stexts = [...sel.querySelectorAll("text")].map((t) => t.textContent);
  assert.ok(stexts.includes("CmpdA") && stexts.includes("CmpdB") && stexts.includes("selective") && stexts.includes("shared"));
  assert.ok(stexts.some((t) => /4 of 4 sites liganded by any compound/.test(t)), "says how many sites it shows");
  assert.equal(sel.querySelectorAll("rect[fill]").length >= 8, true, "a cell per site and compound");
  // the page is drawn back as it was
  assert.equal(document.querySelector("#dosehead").textContent, before.dose);
  assert.equal(document.querySelector("#timehead").textContent, before.time);
  assert.ok(document.querySelector("#cysrank svg"));
  const readme = new TextDecoder().decode(files.find((f) => f.name.endsWith("README.txt")).data);
  assert.match(readme, /time_patterns_Drug\.svg +Time-course patterns, Drug/);
  assert.match(readme, /liganded_selectivity\.svg +Liganded sites across compounds/);
});

test("the patterns and the selectivity map follow the palette like every other figure", async () => {
  const { window, document } = await loadReport({ data: DATA, storage: { [KEY]: { zip_format: "svg", palette: "grey", size: "half" } } });
  document.querySelector("#slides").click();
  await until(() => window.__downloads.length === 1, 30000);
  const files = unzip(await blobBytes(window, window.__downloads[0].blob));
  for (const n of ["time_patterns_Drug.svg", "liganded_selectivity.svg"]) {
    const text = new TextDecoder().decode(files.find((f) => f.name.endsWith("_" + n)).data);
    const cols = [...text.matchAll(/(?:fill|stroke)="(#[0-9a-f]{6})"/g)].map((m) => m[1]);
    assert.ok(cols.length > 5 && cols.every((c) => c.slice(1, 3) === c.slice(3, 5) && c.slice(3, 5) === c.slice(5, 7)), n + " is grey: " + [...new Set(cols)].join(" "));
    assert.match(text, /width="640" height="[\d.]+"|width="[\d.]+" height="600"/, "half a slide: never larger than 640 x 600");
  }
});
