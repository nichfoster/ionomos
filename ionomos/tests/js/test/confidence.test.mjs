// Comparisons Python labels by what the data supports: "low" (a group of one, p borrowed) keeps the volcano
// with a banner; "none" (no replicates anywhere) has no p-values and must still draw, as a fold-change plot.
import { test } from "node:test";
import assert from "node:assert/strict";

import { loadReport, baseData, withData } from "../lib/harness.mjs";

const BASE = baseData();
const comp0 = BASE.comps[0];

test("low confidence: volcano drawn, banner and tile badge shown", async () => {
  const data = withData(BASE, { comps: [{ ...comp0, conf: "low", confNote: "Low confidence: DMSO has 1 sample." }] });
  const { document, errors } = await loadReport({ data });
  assert.deepEqual(errors, []);
  assert.ok(document.querySelector("#differential-body svg circle"), "points drawn");
  assert.match(document.querySelector("#vcount").textContent, /Low confidence: .*DMSO has 1 sample/);
  assert.match(document.querySelector("#tiles").textContent, /Low confidence/);
  assert.equal(document.querySelector("#volc").disabled, false);
});

test("fold change only: points without p-values are drawn and called on fold change", async () => {
  const n = comp0.fc.length;
  const fc = comp0.fc.map((v, i) => (i === 0 ? 3 : i === 1 ? -3 : v == null ? null : 0.1));
  const comp = { ...comp0, fc, p: Array(n).fill(null), q: Array(n).fill(null), conf: "none",
    confNote: "DMSO has 1 sample — fold change only" };
  const { document, errors } = await loadReport({ data: withData(BASE, { comps: [comp] }) });
  assert.deepEqual(errors, []);
  const circles = document.querySelectorAll("#differential-body svg circle");
  assert.ok(circles.length >= 2, "fold-change points drawn despite null p-values");
  const vc = document.querySelector("#vcount").textContent;
  assert.match(vc, /Fold change only/);
  assert.match(vc, /1 up .*1 down/s);
  assert.match(vc, /ranked by fold change/);
  assert.equal(document.querySelector("#volc").disabled, true, "no volcano mode without p-values");
  assert.match(document.querySelector("#differential-body").textContent, /mean log2 abundance/);
});
