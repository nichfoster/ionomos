// The moderated F (D42): with 3+ conditions the payload carries D.F; the overview gets an
// "Any change (F)" tile that follows the p cut-off, and the table and CSV an F column.
import { test } from "node:test";
import assert from "node:assert/strict";

import { loadReport, baseData, withData } from "../lib/harness.mjs";

const BASE = baseData();
const n = BASE.f.id.length;

function withF() {
  const q = Array.from({ length: n }, (_, i) => (i < 5 ? 0.001 : i < 8 ? 0.03 : i === n - 1 ? null : 0.5));
  return withData(BASE, { F: { f: q.map((x) => (x == null ? null : 10)), p: q, q, df1: 2, ref: "DMSO",
    conds: ["DMSO", "Drug", "Drug2"] } });
}

test("no F without 3+ conditions: no tile, no column", async () => {
  const { document, errors } = await loadReport({ data: BASE });
  assert.deepEqual(errors, []);
  assert.doesNotMatch(document.querySelector("#tiles").textContent, /Any change/);
  assert.doesNotMatch(document.querySelector("#table thead").textContent, /any change/);
});

test("the F tile counts features at the current cut-off and the table has the column", async () => {
  const { window, document, errors } = await loadReport({ data: withF() });
  assert.deepEqual(errors, []);
  const tile = [...document.querySelectorAll("#tiles .tile")].find((t) => /Any change \(F\)/.test(t.textContent));
  assert.ok(tile, "F tile shown");
  assert.equal(tile.querySelector(".v").textContent, "8");
  assert.match(tile.textContent, /across 3 conditions, of \d+/);
  assert.match(document.querySelector("#table thead").textContent, /any change \(F\) adj\. p/);
  const alpha = document.querySelector("#alpha");
  alpha.value = "0.01";
  alpha.dispatchEvent(new window.Event("input", { bubbles: true }));
  alpha.dispatchEvent(new window.Event("change", { bubbles: true }));
  const tile2 = [...document.querySelectorAll("#tiles .tile")].find((t) => /Any change \(F\)/.test(t.textContent));
  assert.equal(tile2.querySelector(".v").textContent, "5");
});
