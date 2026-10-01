// The Specific targets section (renderSpecific): hidden for the fixture (DMSO and Drug, no competition). A synthetic
// D.roles payload (the shape roles.report_payload writes) with three comparisons (compound vs control, competition vs
// compound, competition vs control) exercises the role chips, the tiles, the two-axis plot with its marked quadrant,
// the table and its Show choices, the live cut-offs, a click, hostile names; and the per-comparison power table and
// the samples-per-side line that unequal groups get.
import { test } from "node:test";
import assert from "node:assert/strict";

import { loadReport, baseData, withData } from "../lib/harness.mjs";

const BASE = baseData();
const c0 = BASE.comps[0];
const N = c0.fc.length;
const fill = (fn) => Array.from({ length: N }, (_v, i) => fn(i));
// features 0-3 specific (enriched, competed off), 4-6 enriched and not competed, 7 competed but not enriched,
// 8 not in the competition comparison at all, the rest unchanged
const eFc = fill((i) => (i <= 6 ? 3 + 0.1 * i : 0.1));
const kFc = fill((i) => (i <= 3 ? -3 + 0.1 * i : i === 7 ? -2.5 : i === 8 ? null : 0.05));
const sigQ = (fc) => fc.map((v) => (v == null ? null : Math.abs(v) >= 1 ? 0.001 : 0.8));
const comp = (name, t1, t2, fc, size, kind) => ({ ...c0, name, slug: name.replace(/\W+/g, "_"), t1, t2, fc, p: sigQ(fc), q: sigQ(fc), size, kind, onoff: [] });
const COMPS = [
  comp("Probe vs DMSO", "Probe", "DMSO", eFc, [4, 2], "enrichment"),
  comp("Probe_Comp vs Probe", "Probe_Comp", "Probe", kFc, [4, 4], "competition"),
  comp("Probe_Comp vs DMSO", "Probe_Comp", "DMSO", fill((i) => (i >= 4 && i <= 6 ? 3 : 0)), [4, 2], "remaining"),
];
const ROLES = {
  active: true, control: "DMSO",
  conditions: [{ name: "DMSO", role: "control", of: "", n: 2, source: "name (control_keywords)" },
    { name: "Probe", role: "compound", of: "", n: 4, source: "default (not a control)" },
    { name: "Probe_Comp", role: "competition", of: "Probe", n: 4, source: "name (comp)" }],
  specific: [{ compound: "Probe", competition: "Probe_Comp", control: "DMSO", e: 0, k: 1, r: 2, conf: "", note: "" }],
};
const DATA = withData(BASE, { comps: COMPS, roles: ROLES });
const rowsOf = (document) => [...document.querySelectorAll("#sptable tbody tr[data-i]")];
function choose(window, sel, value) {
  const e = window.document.querySelector(sel);
  e.value = value;
  e.dispatchEvent(new window.Event(e.tagName === "SELECT" ? "change" : "input"));
}

test("fixture without a competition: section and nav link hidden", async () => {
  const { document, errors } = await loadReport();
  assert.deepEqual(errors, []);
  assert.equal(document.querySelector("#specific").hidden, true);
  assert.equal(document.querySelector("#navspecific").hidden, true);
  assert.equal(document.querySelector("#specificbody").innerHTML, "");
  assert.equal(BASE.roles.specific.length, 0);
  assert.deepEqual(BASE.roles.conditions.map((c) => c.role), ["control", "compound"]);
});

test("specific targets: roles, tiles, plot with the marked quadrant, table", async () => {
  const { window, document, errors } = await loadReport({ data: DATA });
  assert.deepEqual(errors, []);
  assert.equal(document.querySelector("#specific").hidden, false);
  assert.equal(document.querySelector("#navspecific").hidden, false);
  const chips = [...document.querySelectorAll("#specificbody .pipeline .step")].map((s) => s.textContent);
  assert.deepEqual(chips, ["DMSO control · n = 2", "Probe compound · n = 4", "Probe_Comp competition of Probe · n = 4"]);
  const tiles = [...document.querySelectorAll("#specificbody .tile")].map((t) => t.textContent);
  assert.match(tiles[0], /Specific targets of Probe4enriched against DMSO and competed off · of \d+ tested/);
  assert.match(tiles[1], /Enriched, not competed3/);
  assert.match(tiles[2], /Samples4 \/ 2Probe vs DMSO · Probe_Comp vs Probe: 4 \/ 4/);
  assert.match(document.querySelector("#specificbody").textContent, /up in Probe vs DMSO and down in Probe_Comp vs Probe, each at \|log2FC\| ≥ 1\.00 and adj\. p ≤ 0\.05/);
  assert.equal(document.querySelector("#specificbody a[href='specific_targets.tsv']").textContent, "specific_targets.tsv");
  // one point per feature in both comparisons (8 has no competition value), coloured by call
  const dots = [...document.querySelectorAll("#spplot circle[data-i]")];
  assert.equal(dots.length, c0.fc.filter((v, i) => eFc[i] != null && kFc[i] != null).length);
  const calls = (k) => dots.filter((d) => d.getAttribute("data-call") === String(k)).map((d) => +d.getAttribute("data-i")).sort((a, b) => a - b);
  assert.deepEqual(calls(0), [0, 1, 2, 3]);
  assert.deepEqual(calls(1), [4, 5, 6]);
  assert.deepEqual(calls(2), [7]);
  assert.equal(dots.some((d) => d.getAttribute("data-i") === "8"), false);
  // the quadrant of specific binders is drawn, and the specific points lie inside it
  const quad = document.querySelector("#spplot #spquad");
  const qx = +quad.getAttribute("x"), qy = +quad.getAttribute("y"), qh = +quad.getAttribute("height");
  for (const d of dots) {
    const inside = +d.getAttribute("cx") >= qx && +d.getAttribute("cy") <= qy + qh;
    assert.equal(inside, d.getAttribute("data-call") === "0", "feature " + d.getAttribute("data-i"));
  }
  const svgText = document.querySelector("#spplot svg").textContent;
  assert.match(svgText, /enrichment: log2 Probe \/ DMSO/);
  assert.match(svgText, /competed off: log2 Probe \/ Probe_Comp/);
  assert.ok(document.querySelector("#spplot .tools button"), "SVG / PNG export");
  assert.match(document.querySelector("#specificbody .legend").textContent, /specific \(4\).*enriched, not competed \(3\).*competed, not enriched \(1\)/);
  // table: specific ones, strongest first, both fold changes and both adjusted p-values
  assert.deepEqual(rowsOf(document).map((r) => r.dataset.i), ["0", "1", "2", "3"]);
  const head = [...document.querySelectorAll("#sptable th")].map((t) => t.textContent);
  assert.deepEqual(head, ["Protein", "enrichment log2FC", "adj. p", "competition log2FC", "adj. p", "left log2FC", "call"]);
  const first = [...rowsOf(document)[0].children].map((c) => c.textContent.trim());
  assert.deepEqual(first.slice(1), ["3.00", "0.0010", "-3.00", "0.0010", "0.00", "specific"]);
  for (const [show, want] of [["1", ["6", "5", "4"]], ["2", ["7"]]]) {
    choose(window, "#spshow", show);
    assert.deepEqual(rowsOf(document).map((r) => r.dataset.i), want, show);
  }
  choose(window, "#spshow", "all");
  assert.match(document.querySelector("#sptable .pager").textContent, new RegExp("^" + dots.length + " proteins · page 1 of "));
});

test("the calls follow the live cut-offs; a click opens the feature in the enrichment comparison", async () => {
  const { window, document, errors } = await loadReport({ data: DATA });
  choose(window, "#lfc", "3.15");   // enrichment 3.0, 3.1 fall out; of the competed ones only |−3.0| < 3.15 stays out too
  assert.match(document.querySelector("#specificbody .tile").textContent, /Specific targets of Probe0/);
  assert.match(document.querySelector("#sptable").textContent, /None at these cut-offs/);
  choose(window, "#lfc", "2.85");   // -2.8 and -2.7 are no longer competed off: features 2 and 3 move to "not competed"
  assert.deepEqual(rowsOf(document).map((r) => r.dataset.i), ["0", "1"]);
  window.document.querySelector("#reset").click();
  assert.deepEqual(rowsOf(document).map((r) => r.dataset.i), ["0", "1", "2", "3"]);
  window.document.querySelector("#comp").value = "2";
  window.document.querySelector("#comp").dispatchEvent(new window.Event("change"));
  rowsOf(document)[1].click();
  assert.equal(document.querySelector("#comp").value, "0", "back to compound vs control");
  assert.match(document.querySelector("#detail").textContent, new RegExp((BASE.f.label[1] || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  document.querySelector("#spplot circle[data-i='4']").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  assert.deepEqual(errors, []);
});

test("two compounds: a switch; a low-confidence note; hostile names are escaped", async () => {
  const evil = "<img src=x onerror=alert(1)>";
  const roles = withData(ROLES, { conditions: ROLES.conditions.concat([{ name: evil, role: "compound", of: "", n: 1, source: "x" }]),
    specific: [ROLES.specific[0], { compound: evil, competition: evil, control: evil, e: 2, k: 1, r: null, conf: "low", note: "Low confidence: a group behind these calls has fewer samples than the analysis asks for." }] });
  const comps = COMPS.map((c, k) => (k === 2 ? { ...c, name: evil } : c));
  const { window, document, errors } = await loadReport({ data: withData(BASE, { comps, roles }) });
  assert.deepEqual(errors, []);
  assert.equal(document.querySelectorAll("#spcomp option").length, 2);
  choose(window, "#spcomp", "1");
  assert.equal(document.querySelector("#specificbody img"), null);
  assert.match(document.querySelector("#specificbody .notes").textContent, /Low confidence/);
  assert.match(document.querySelector("#specificbody").textContent, /<img src=x/);
  const head = [...document.querySelectorAll("#sptable th")].map((t) => t.textContent);
  assert.equal(head.includes("left log2FC"), false, "no competition vs control comparison for this one");
});

test("unequal groups: samples per side under the volcano, and power per comparison", async () => {
  const power = withData(BASE.qc.power, { unbalanced: true, comparisons: [
    { name: "Probe vs DMSO", n: [4, 2], df: 12.3, balanced: 2.67, mdfc: { "0.05": { q25: 0.9, q50: 1.21, q75: 1.6 }, "0.001": { q25: 1.6, q50: 2.1, q75: 2.8 } } },
    { name: "Probe_Comp vs Probe", n: [4, 4], df: 12.3, balanced: 4, mdfc: { "0.05": { q25: 0.7, q50: 0.99, q75: 1.3 }, "0.001": null } }] });
  const { document, errors } = await loadReport({ data: withData(DATA, { qc: withData(BASE.qc, { power }) }) });
  assert.deepEqual(errors, []);
  assert.match(document.querySelector("#vcount").textContent, /tested · 4 against 2 samples/);
  [...document.querySelectorAll("#qc .tabs button")].find((b) => b.dataset.k === "power").click();
  assert.match(document.querySelector("#qcbody .verdict").textContent, /^The groups are not the same size/);
  const rows = [...document.querySelectorAll("#powcomps tbody tr")].map((r) => [...r.children].map((c) => c.textContent.trim()));
  assert.deepEqual(rows[0], ["Probe vs DMSO", "4 against 2", "2.7", "1.21 (0.90–1.60)", "2.10 (1.60–2.80)"]);
  assert.deepEqual(rows[1], ["Probe_Comp vs Probe", "4 against 4", "4.0", "0.99 (0.70–1.30)", "–"]);
  assert.ok(document.querySelector("#powcomps tbody tr td[title]"), "above the cut-off: says so");
  assert.match(document.querySelector("#qcbody").textContent, /a small group costs precision of its mean, not of the variance/);
});

test("equal groups (the fixture): the power table lists the comparison, nothing says unequal", async () => {
  const { document, errors } = await loadReport();
  assert.deepEqual(errors, []);
  assert.match(document.querySelector("#vcount").textContent, /3 against 3 samples/);
  [...document.querySelectorAll("#qc .tabs button")].find((b) => b.dataset.k === "power").click();
  assert.doesNotMatch(document.querySelector("#qcbody .verdict").textContent, /not the same size/);
  assert.equal(document.querySelectorAll("#powcomps tbody tr").length, 1);
});
