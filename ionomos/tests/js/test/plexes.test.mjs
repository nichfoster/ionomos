// TMT across plexes (D48): with a `plex` per sample and a PCA from before IRS, the PCA can be coloured by plex
// and switched between the values before and after the plexes were put on one scale.
import { test } from "node:test";
import assert from "node:assert/strict";

import { loadReport, baseData, withData } from "../lib/harness.mjs";

const BASE = baseData();
const n = BASE.samples.length;

function openPCA(document, window) {
  const btn = [...document.querySelectorAll("#qc .tabs button")].find((b) => b.dataset.k === "pca");
  assert.ok(btn, "PCA tab exists");
  btn.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
}

function change(window, el, value) {
  el.value = value;
  el.dispatchEvent(new window.Event("change", { bubbles: true }));
}

test("plexes: colour by plex, before / after IRS", async () => {
  const plex = BASE.samples.map((_s, j) => (j % 2 ? "Exp2" : "Exp1"));
  const before = { scores: BASE.qc.pca.scores.map((s, j) => [j % 2 ? 5 : -5, s[1]]), percent: [70, 10], n: 40,
    label: "IRS (reference channel)" };
  const data = withData(BASE, { plex, qc: { ...BASE.qc, pcaBefore: before } });
  const { document, window, errors } = await loadReport({ data });
  openPCA(document, window);
  const by = document.querySelector("#pcby");
  assert.ok([...by.options].some((o) => o.value === "plex"), "colour by plex offered");
  change(window, by, "plex");
  assert.match(document.querySelector("#qcbody .legend").textContent, /Exp1.*Exp2/s);
  const when = document.querySelector("#pcw");
  assert.ok(when, "before / after select");
  assert.match(when.textContent, /before IRS \(reference channel\)/);
  change(window, when, "before");
  assert.equal(document.querySelectorAll("#pcachart circle").length, n, "every sample drawn before IRS");
  assert.match(document.querySelector("#pcachart").textContent, /PC1 \(70\.0%\)/);
  change(window, document.querySelector("#pcw"), "after");
  assert.equal(document.querySelectorAll("#pcachart circle").length, n);
  assert.deepEqual(errors, []);
});

test("no plexes: no plex option and no before / after select", async () => {
  const { document, window, errors } = await loadReport();
  openPCA(document, window);
  const by = document.querySelector("#pcby");
  assert.ok(!by || ![...by.options].some((o) => o.value === "plex"));
  assert.equal(document.querySelector("#pcw"), null);
  assert.deepEqual(errors, []);
});
