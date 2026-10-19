// The lab's cysteine atlas in the Liganded sites table (D.cys.atlas, atlasctx.py with_atlas, D124): the atlas
// tier, experiments and "new to atlas" columns, the QC-suspect mark, the "new to the atlas" tile and Show choice,
// sorting by them; a payload without the atlas keeps the table as it was.
import { test } from "node:test";
import assert from "node:assert/strict";

import { loadReport, baseData, withData } from "../lib/harness.mjs";

const BASE = baseData();

function compound(name, r, cls, over) {
  const count = (k) => cls.filter((c) => c === k).length;
  return { name, reps: 3, minRep: 2, counts: { liganded: count(0), inconsistent: count(1), "not liganded": count(2), "too few": count(3) },
    assessed: cls.filter((c) => c < 3).length, fraction: count(0) / Math.max(1, cls.filter((c) => c < 3).length),
    r, n: r.map((v) => (v == null ? 0 : 3)), over, cls };
}

const CYS = {
  ran: true, rule: "R ≥ 4 (heavy / light) in at least 2 replicates", ratio: 4, minRep: 2, dir: "high",
  classes: ["liganded", "inconsistent", "not liganded", "too few"], i: [0, 1, 2, 3, 4, 5, 6, 7],
  nlig: [2, 1, 1, 0, 0, 0, 1, 0], sel: [2, 1, 1, 0, 0, 0, 3, 0], selNames: ["", "selective", "shared", "unresolved"],
  selectivity: { selective: 2, shared: 1, unresolved: 1 },
  compounds: [
    compound("CmpdA", [3.1, 2.4, 0.1, 2.2, 0.0, -0.3, 2.6, null], [0, 0, 2, 1, 2, 2, 0, 3], [3, 2, 0, 1, 0, 0, 3, 0]),
    compound("CmpdB", [2.8, 0.2, 2.9, 0.1, 0.3, 0.0, null, null], [0, 2, 0, 2, 2, 2, 3, 3], [3, 0, 3, 0, 0, 0, 0, 0]),
  ],
  proteins: [], notes: [],
};
const ATLAS = {
  built: "2026-10-19", rule: "Dan's spectra rule of 2026-10-14", quotable: false, sites: 12345, line: "Atlas columns: …",
  tiers: ["high", "medium", "low"],
  tier: [0, null, 1, null, 2, null, null, null], exp: [12, null, 3, null, 1, null, null, null],
  spec: [340, null, 20, null, 2, null, null, null], qc: [0, 0, 1, 0, 0, 0, 0, 0], new: [0, 1, 0, 1, 0, 0, 1, 0],
  counts: { held: 3, new: 3, blank: 2, ligHeld: 2, ligNew: 2 },
};
const DATA = withData(BASE, { cys: { ...CYS, atlas: ATLAS } });
const rowsOf = (document) => [...document.querySelectorAll("#cystable tbody tr[data-k]")];
const cells = (tr) => [...tr.children].map((c) => c.textContent.trim());
function choose(window, sel, value) {
  const e = window.document.querySelector(sel);
  e.value = value;
  e.dispatchEvent(new window.Event(e.tagName === "SELECT" ? "change" : "input"));
}

test("the atlas columns, the QC-suspect mark and the new badge", async () => {
  const { window, document, errors } = await loadReport({ data: DATA });
  assert.deepEqual(errors, []);
  const head = [...document.querySelectorAll("#cystable th")].map((t) => t.textContent);
  assert.deepEqual(head, ["Site", "R CmpdA", "R CmpdB", "replicates ≥ R", "call", "liganded by", "selectivity", "atlas tier",
    "atlas experiments", "new to atlas", "Description"]);
  const rows = rowsOf(document);
  assert.deepEqual(rows.map((r) => r.dataset.k), ["0", "6", "1"]);
  assert.deepEqual(cells(rows[0]).slice(7, 10), ["high", "12", ""]);
  assert.match(rows[0].children[7].getAttribute("title"), /high, 12 experiments, 340 spectra/);
  assert.deepEqual(cells(rows[1]).slice(7, 10), ["", "", "new"]);
  assert.ok(rows[1].querySelector(".badge.anew"));
  choose(window, "#cshow", "all");
  const two = rowsOf(document).find((r) => r.dataset.k === "2");
  assert.equal(cells(two)[7], "medium QC suspect");
  assert.ok(two.children[7].querySelector(".badge"));
  assert.equal(rowsOf(document).find((r) => r.dataset.k === "5").children[7].textContent, "", "the atlas can't say: blank");
});

test("the tile, the Show choice and sorting by the atlas", async () => {
  const { window, document, errors } = await loadReport({ data: DATA });
  const tiles = [...document.querySelectorAll("#cysbody .tile")].map((t) => t.textContent);
  assert.ok(tiles.some((t) => /New to the atlas2liganded sites the lab's atlas doesn't have · 2 it has/.test(t)), tiles.join(" | "));
  choose(window, "#cshow", "anew");
  assert.deepEqual(rowsOf(document).map((r) => r.dataset.k), ["6", "1"]);
  choose(window, "#cshow", "all");
  const th = (k) => document.querySelector("#cystable th[data-k='" + k + "']");
  th("atier").click();
  assert.equal(th("atier").dataset.dir, "asc");
  assert.deepEqual(rowsOf(document).slice(0, 3).map((r) => r.dataset.k), ["0", "2", "4"], "high, medium, low, then blanks");
  th("aexp").click();
  assert.deepEqual(rowsOf(document).slice(0, 3).map((r) => r.dataset.k), ["0", "2", "4"], "most experiments first");
  th("anew").click();
  assert.deepEqual(rowsOf(document).slice(0, 3).map((r) => r.dataset.k).sort(), ["1", "3", "6"], "new sites first");
  assert.deepEqual(errors, []);
});

test("without the atlas the table, the tiles and the Show choices are as before", async () => {
  const { document, errors } = await loadReport({ data: withData(BASE, { cys: CYS }) });
  assert.deepEqual(errors, []);
  const head = [...document.querySelectorAll("#cystable th")].map((t) => t.textContent);
  assert.equal(head.some((h) => /atlas/.test(h)), false);
  assert.equal([...document.querySelectorAll("#cshow option")].some((o) => o.value === "anew"), false);
  assert.doesNotMatch(document.querySelector("#cysbody .tiles").textContent, /atlas/);
});
