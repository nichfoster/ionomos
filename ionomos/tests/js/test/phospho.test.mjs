// The phosphosites / kinases / partners section (renderPhos, D79): hidden for the fixture (proteins, nothing asked
// for); a synthetic D.phos payload (the shape phospho.report_payload writes) exercises the localisation tiles, the
// KSEA bar chart and table, clicking a kinase, the comparison switch, the STRING partners, a hostile name, and the
// kinase-activity figure in "Export for slides".
import { test } from "node:test";
import assert from "node:assert/strict";

import { loadReport, baseData, withData, blobBytes, unzip, until } from "../lib/harness.mjs";

const BASE = baseData();
const KEY = "ionomos.export.v1";
const K = (name, m, z, fdr, sig, subs) => [name, m, z / 4, z, fdr / 2, fdr, sig, subs];
const PHOS = {
  ran: true,
  loc: { table: "combined_site_STY_79.9663.tsv", kind: "lfq", source: "FragPipe (IonQuant) site report", quantity: "MaxLFQ Intensity",
    min_localization: 0.75, filter: "best localisation probability ≥ 0.75, applied by Ionomos", sites_in_table: 260, sites_kept: 240, sites_below: 20,
    values_dropped: 0, residues: { S: 160, T: 60, Y: 20 }, hist: [] },
  ksea: { ran: true, file: "Kinase_Substrate_Dataset.gz", match: "gene", min_substrates: 5, networkin: false, networkin_score: null, relationships: 40, alpha: 0.05,
    comps: [
      { name: "Drug vs DMSO", slug: "Drug_vs_DMSO", sites: 240, matched: 30,
        k: [K("KIN_UP", 18, 6.1, 1e-8, "up", ["GENE001 S10", "GENE002 T24"]), K("KIN<b>X", 6, -3.2, 0.004, "down", ["GENE009 S70"]), K("KIN_FLAT", 12, 0.3, 0.8, "", ["GENE040 S280"])] },
      { name: "Drug vs DMSO (protein-corrected)", slug: "Drug_vs_DMSO_protein-corrected", sites: 200, matched: 25,
        k: [K("KIN_UP", 15, 4.0, 1e-4, "up", ["GENE001 S10"])] },
    ] },
  string: { ran: true, file: "9606.protein.links.v12.0.txt", min_score: 700,
    comps: [{ name: "Drug vs DMSO", hits: 18, connected: 2, edges: 1, genes: [["GENE001", "up", ["GENE002"], [900]], ["GENE002", "up", ["GENE001"], [900]]] }] },
  corrected: true,
};
const DATA = withData(BASE, { phos: PHOS });

test("fixture without phospho: section and nav link hidden", async () => {
  const { document, errors } = await loadReport();
  assert.deepEqual(errors, []);
  assert.equal(document.querySelector("#phos").hidden, true);
  assert.equal(document.querySelector("#navphos").hidden, true);
  assert.equal(document.querySelector("#phosbody").textContent, "");
});

test("tiles, kinase bars and table, a click on a kinase, the comparison switch, the partners", async () => {
  const { window, document, errors } = await loadReport({ data: DATA });
  assert.deepEqual(errors, []);
  assert.equal(document.querySelector("#phos").hidden, false);
  const tiles = document.querySelector("#phosbody .tiles").textContent;
  assert.match(tiles, /Phosphosites analysed240of 260/);
  assert.match(tiles, /S 160 · T 60 · Y 20/);
  assert.match(tiles, /20sites left out/);
  assert.match(tiles, /Protein-corrected/);
  const bars = [...document.querySelectorAll("#phkin svg rect")];
  assert.equal(bars.length, 3);
  const labels = [...document.querySelectorAll("#phkin svg text")].map((t) => t.textContent);
  assert.ok(labels.includes("KIN_UP (18)") && labels.includes("KIN<b>X (6)"), labels.join(" | "));
  assert.equal(document.querySelectorAll("#phkin b").length, 0, "a kinase name is text, never markup");
  const rows = [...document.querySelectorAll("#phtable tbody tr")];
  assert.equal(rows.length, 3);
  assert.match(rows[0].textContent, /more active/);
  rows[1].click();
  assert.match(document.querySelector("#phtable p").textContent, /KIN<b>X: 6 measured substrates \(GENE009 S70, …\)/);
  document.querySelector("#phfind").click();
  assert.equal(document.querySelector("#search").value, "GENE009");
  assert.match(document.querySelector("#phstring").textContent, /2 of 18 hit genes interact/);
  assert.match(document.querySelector("#phstring tbody").textContent, /GENE001upGENE002 \(900\)/);
  const sel = document.querySelector("#phcomp");
  sel.value = "1";
  sel.dispatchEvent(new window.Event("change"));
  assert.equal(document.querySelectorAll("#phkin svg rect").length, 1);
  assert.match(document.querySelector("#phosbody .row").textContent, /25 of 200 sites are substrates/);
  assert.deepEqual(errors, []);
});

test("Export for slides holds one kinase-activity figure per comparison, and the page is left as it was", async () => {
  const { window, document, errors } = await loadReport({ data: DATA, storage: { [KEY]: { zip_format: "svg" } } });
  const before = document.querySelector("#phkin svg").outerHTML;
  document.querySelector("#slides").click();
  await until(() => window.__downloads.length === 1, 30000);
  assert.deepEqual(errors, []);
  const files = unzip(await blobBytes(window, window.__downloads[0].blob));
  const figs = files.filter((f) => f.name.includes("/figures/")).map((f) => f.name.replace(/^.*\/\d\d_/, ""));
  assert.deepEqual(figs.filter((n) => /^kinase_activity_/.test(n)).sort(),
    ["kinase_activity_Drug_vs_DMSO.svg", "kinase_activity_Drug_vs_DMSO_protein-corrected.svg"]);
  const svgText = new TextDecoder().decode(files.find((f) => f.name.endsWith("_kinase_activity_Drug_vs_DMSO.svg")).data);
  const doc = new window.DOMParser().parseFromString(svgText, "image/svg+xml");
  assert.equal(doc.querySelector("parsererror"), null);
  assert.ok(!/var\(|class=|style=/.test(svgText), "no CSS in an exported figure");
  assert.equal(doc.querySelector("title").textContent, "Kinase activity (KSEA)");
  assert.equal(document.querySelector("#phkin svg").outerHTML, before);
});
