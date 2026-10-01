// The Liganded sites section (renderCys): hidden for the fixture (DIA, no site ratios); a synthetic D.cys payload
// (the shape cys.report_payload writes) exercises the tiles, the rank plot, the site table with its Show choices,
// the text filter, sorting, the compound switch, the protein view, the annotation column and hostile names.
import { test } from "node:test";
import assert from "node:assert/strict";

import { loadReport, baseData, withData } from "../lib/harness.mjs";

const BASE = baseData();
const N = 8;

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
  proteins: [{ p: "P1", g: "GENEA", s: 4, x: [0, 1, 2, 3], per: [[4, 2, 1], [4, 2, 1]] }, { p: "P2", g: "GENEB", s: 1, x: [6], per: [[1, 1, 0], [0, 0, 0]] }],
  notes: [],
};
const DATA = withData(BASE, { cys: CYS });
const rowsOf = (document) => [...document.querySelectorAll("#cystable tbody tr[data-k]")];
function choose(window, sel, value) {
  const e = window.document.querySelector(sel);
  e.value = value;
  e.dispatchEvent(new window.Event(e.tagName === "SELECT" ? "change" : "input"));
}

test("fixture without site ratios: section and nav link hidden", async () => {
  const { document, errors } = await loadReport();
  assert.deepEqual(errors, []);
  assert.equal(document.querySelector("#cys").hidden, true);
  assert.equal(document.querySelector("#navcys").hidden, true);
  assert.ok(document.querySelector("#cysbody .empty"));
});

test("liganded sites: tiles, rank plot, table and its Show choices", async () => {
  const { window, document, errors } = await loadReport({ data: DATA });
  assert.deepEqual(errors, []);
  assert.equal(document.querySelector("#cys").hidden, false);
  assert.equal(document.querySelector("#navcys").hidden, false);
  const tiles = [...document.querySelectorAll("#cysbody .tile")].map((t) => t.textContent);
  assert.equal(tiles.length, 3);
  assert.match(tiles[0], /CmpdA3liganded of 7 assessed \(42\.9%\) · 1 inconsistent · 1 with too few replicates/);
  assert.match(tiles[2], /Selective sites2one compound only · 1 shared · 1 unresolved/);
  assert.match(document.querySelector("#cysbody").textContent, /R ≥ 4 \(heavy \/ light\) in at least 2 replicates/);
  assert.equal(document.querySelector("#cysbody a[href='cysteine_sites.tsv']").textContent, "cysteine_sites.tsv");
  assert.equal(document.querySelectorAll("#cysrank circle[data-k]").length, 7, "one point per measured site");
  assert.match(document.querySelector("#cysrank svg").textContent, /R = 4/);
  assert.ok(document.querySelector("#cysrank .tools button"), "SVG / PNG export");
  // default: liganded by the first compound, strongest first
  assert.deepEqual(rowsOf(document).map((r) => r.dataset.k), ["0", "6", "1"]);
  const head = [...document.querySelectorAll("#cystable th")].map((t) => t.textContent);
  assert.deepEqual(head, ["Site", "R CmpdA", "R CmpdB", "replicates ≥ R", "call", "liganded by", "selectivity", "Description"]);
  const first = [...rowsOf(document)[0].children].map((c) => c.textContent.trim());
  assert.deepEqual(first.slice(1, 7), ["8.57", "6.96", "3 / 3", "liganded", "2", "shared"]);
  assert.match(rowsOf(document)[0].children[1].getAttribute("style"), /background/);
  for (const [show, want] of [["inc", ["3"]], ["any", ["0", "6", "1", "2"]], ["sel", ["1", "2"]], ["all", ["0", "6", "1", "3", "2", "4", "5"]]]) {
    choose(window, "#cshow", show);
    assert.deepEqual(rowsOf(document).map((r) => r.dataset.k), want, show);
  }
  choose(window, "#cq", (BASE.f.label[2] || BASE.f.id[2]).toLowerCase());
  assert.ok(rowsOf(document).some((r) => r.dataset.k === "2"));
  choose(window, "#cq", "no such site anywhere");
  assert.match(document.querySelector("#cystable").textContent, /No site matches/);
});

test("sorting, the compound switch and a click", async () => {
  const { window, document, errors } = await loadReport({ data: DATA });
  choose(window, "#cshow", "all");
  const th = (k) => document.querySelector("#cystable th[data-k='" + k + "']");
  th("c1").click();
  assert.equal(rowsOf(document)[0].dataset.k, "2", "sorted by CmpdB's ratio, highest first");
  th("c1").click();
  assert.equal(rowsOf(document)[0].dataset.k, "5", "and reversed");
  th("name").click();
  assert.equal(th("name").dataset.dir, "asc");
  choose(window, "#ccomp", "1");
  assert.match(document.querySelector("#cshow").textContent, /liganded by CmpdB/);
  assert.equal(document.querySelectorAll("#cysrank circle[data-k]").length, 6);
  choose(window, "#cshow", "lig");
  assert.deepEqual(rowsOf(document).map((r) => r.dataset.k).sort(), ["0", "2"]);
  rowsOf(document)[0].click();
  assert.match(document.querySelector("#detail").textContent, new RegExp((BASE.f.label[+rowsOf(document)[0].dataset.k] || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  document.querySelector("#cysrank circle[data-k]").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  assert.deepEqual(errors, []);
});

test("protein view marks proteins whose sites mostly move, and highlights their sites", async () => {
  const { document, errors } = await loadReport({ data: DATA });
  document.querySelector("#cprot").click();
  const rows = [...document.querySelectorAll("#cystable tbody tr[data-k]")];
  assert.equal(rows.length, 2);
  assert.match(rows[0].textContent, /GENEA\s*P1\s*4\s*2 \/ 4\s*most sites/);
  assert.doesNotMatch(rows[1].textContent, /most sites/);
  rows[0].click();
  assert.match(document.querySelector("#searchinfo").textContent + document.querySelector("#hl").textContent, /GENEA sites/);
  assert.deepEqual(errors, []);
});

test("one compound with an annotation: no selectivity, a new-sites tile and column", async () => {
  const one = withData(CYS, { compounds: [CYS.compounds[0]], nlig: [1, 1, 0, 0, 0, 0, 1, 0], annotation: { file: "cysdb.csv", sites_in_file: 10, flags: ["ligandable"], matched: 3, liganded_known: 2, liganded_new: 1,
    status: ["known liganded", "seen before", "new", "new", "new", "new", "new", "new"] } });
  const { window, document, errors } = await loadReport({ data: withData(BASE, { cys: one }) });
  assert.deepEqual(errors, []);
  assert.equal(document.querySelector("#ccomp"), null);
  const head = [...document.querySelectorAll("#cystable th")].map((t) => t.textContent);
  assert.deepEqual(head, ["Site", "R CmpdA", "replicates ≥ R", "call", "annotation", "Description"]);
  assert.match(document.querySelector("#cysbody .tiles").textContent, /New liganded sites1not in cysdb\.csv · 2 in it/);
  assert.equal([...document.querySelectorAll("#cshow option")].some((o) => o.value === "sel"), false);
  choose(window, "#cshow", "new");
  assert.deepEqual(rowsOf(document).map((r) => r.dataset.k), ["6"]);
});

test("not run: the reason is shown; hostile names are escaped", async () => {
  const off = await loadReport({ data: withData(BASE, { cys: { ran: false, reason: "liganded-site calls are switched off" } }) });
  assert.match(off.document.querySelector("#cysbody .empty").textContent, /switched off/);
  const evil = "<img src=x onerror=alert(1)>";
  const data = withData(BASE, { cys: withData(CYS, { compounds: [withData(CYS.compounds[0], { name: evil }), CYS.compounds[1]],
    proteins: [{ p: evil, g: evil, s: 1, x: [0], per: [[1, 1, 1], [1, 0, 0]] }], rule: evil }) });
  const { document, errors } = await loadReport({ data });
  assert.deepEqual(errors, []);
  assert.equal(document.querySelector("#cysbody img"), null);
  document.querySelector("#cprot").click();
  assert.equal(document.querySelector("#cysbody img"), null);
  assert.match(document.querySelector("#cystable").textContent, /<img src=x/);
});
