// "How far to trust this" (downstream/trust.py, D60) is static HTML that report.py writes under the key
// findings: report.js neither draws it nor needs to know it. These tests hold that line: the block is in the
// shipped page, the script leaves it alone through setup and every re-render, it sits where the reader looks
// first, and its help entry is in the report.
import { test } from "node:test";
import assert from "node:assert/strict";

import { loadReport, loadReportRaw, baseData, withData } from "../lib/harness.mjs";

const BASE = baseData();

test("the trust block is in the shipped page and survives the script", async () => {
  const raw = await loadReportRaw();
  assert.deepEqual(raw.errors, []);
  const { window, document, errors } = await loadReport();
  assert.deepEqual(errors, []);
  for (const doc of [raw.document, document]) {
    const box = doc.querySelector("#overview > #trust.card.findings");
    assert.ok(box, "under the overview");
    assert.equal(box.querySelector("h3").textContent, "How far to trust this");
    const items = [...box.querySelectorAll(":scope > ul > li")];
    assert.deepEqual(items.map((li) => li.dataset.key), ["replicates", "agreement", "missing", "comparison", "power"]);
    assert.match(items[0].textContent, /^Samples per group: DMSO 3, Drug 3\.$/);
    assert.match(items[3].textContent, /Drug vs DMSO: \d+ features tested, \d+ up and \d+ down\./);
    assert.match(box.querySelector(".meta").textContent, /There is no overall score/);
  }
  const before = document.querySelector("#trust").outerHTML;
  // every control that re-renders the overview: the block is not theirs to touch
  const lfc = document.querySelector("#lfc");
  lfc.value = "0.5";
  lfc.dispatchEvent(new window.Event("input", { bubbles: true }));
  lfc.dispatchEvent(new window.Event("change", { bubbles: true }));
  document.querySelector("#theme").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  assert.equal(document.querySelector("#trust").outerHTML, before);
});

test("it sits after the key findings and before the issues and the plots", async () => {
  const { document } = await loadReport();
  const kids = [...document.querySelector("#overview").children].map((el) => el.id || el.className);
  assert.ok(kids.indexOf("tiles") < kids.indexOf("findings"));
  assert.equal(kids.indexOf("trust"), kids.indexOf("findings") + 1);
  assert.ok(document.querySelector("#findings .findings"), "the key findings are still drawn by the script");
  const pos = document.querySelector("#trust").compareDocumentPosition(document.querySelector("#differential"));
  assert.ok(pos & 4, "before the differential section");
});

test("its help entry is in the report, and the page works without the block", async () => {
  assert.equal(BASE.help.entries["report.trust"].t, "How far to trust this");
  assert.ok(BASE.help.report.includes("report.trust"));
  const { document, errors } = await loadReport({ data: withData(BASE, { comps: [] }) });
  assert.deepEqual(errors, []);
  assert.ok(document.getElementById("help-report.trust"), "in the Help section");
  document.querySelector("#trust").remove();
  assert.ok(document.querySelector("#tiles .tile"));
});
