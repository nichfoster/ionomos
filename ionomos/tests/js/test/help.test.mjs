// The report's help (ionomos/help/*.md, embedded by report.py as D.help): the Help entry in the nav, the
// "?" beside section titles, QC tabs and issue boxes, the inline panels, the Help section at the end, that
// every help link lands on something in the page, and that the only dynamic text (issue titles and codes
// from the data) is escaped.
import { test } from "node:test";
import assert from "node:assert/strict";

import { loadReport, loadReportRaw, baseData, withData } from "../lib/harness.mjs";

const BASE = baseData();

function click(window, el) {
  el.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true }));
}

test("the fixture carries the help, with a Help link in the nav and a Help section", async () => {
  assert.ok(BASE.help && Object.keys(BASE.help.entries).length > 40, "report.py embeds the help");
  const { document, errors } = await loadReport();
  assert.deepEqual(errors, []);
  const nav = document.querySelector("nav.toc a[href='#help']");
  assert.ok(nav, "Help in the nav");
  const body = document.querySelector("#help #helpbody");
  assert.ok(body, "a Help section at the end");
  const ids = [...body.querySelectorAll("details.helpitem")].map((d) => d.id);
  for (const id of BASE.help.report.concat(BASE.help.glossary)) assert.ok(ids.includes("help-" + id), `help-${id} in the Help section`);
  assert.ok(ids.includes("help-glossary.log2fc") && ids.includes("help-qc.pca") && ids.includes("help-report.search"));
  assert.match(body.textContent, /Glossary/);
  assert.match(body.textContent, /ionomos help --open/);
});

test("a ? beside each section title opens its help inline, and closes again", async () => {
  const { window, document, errors } = await loadReport();
  const want = { "#differential > h2": "report.differential", "#heat > h2": "report.heatmap", "#enrichment > h2": "report.enrichment",
    "#quality > h2": "report.quality", "#methods > h2": "report.methods", "#onoff > h2": "report.onoff" };
  for (const [sel, id] of Object.entries(want)) {
    const b = document.querySelector(sel + " > button.qhelp");
    assert.ok(b, `? on ${sel}`);
    assert.equal(b.dataset.help, id);
    assert.equal(b.getAttribute("aria-expanded"), "false");
  }
  const b = document.querySelector("#heat > h2 > button.qhelp");
  click(window, b);
  const panel = document.querySelector("#heat > h2 + .helppanel");
  assert.ok(panel, "panel right after the title");
  assert.equal(b.getAttribute("aria-expanded"), "true");
  assert.match(panel.textContent, /Heatmap/);
  assert.match(panel.textContent, /centred on its own mean/);
  click(window, b);
  assert.equal(document.querySelector("#heat .helppanel"), null, "a second click closes it");
  assert.equal(b.getAttribute("aria-expanded"), "false");
  click(window, b);
  click(window, document.querySelector("#heat .helppanel .hclose"));
  assert.equal(document.querySelector("#heat .helppanel"), null, "× closes it");
  assert.ok(document.querySelector("#differential-body > .bar > button.qhelp[data-help='report.cutoffs']"), "? for the cut-offs");
  assert.ok(document.querySelector("#differential-body > h3 > button.qhelp[data-help='report.phist']"), "? for the p-value histogram");
  assert.ok(document.querySelector("#findings h3 > button.qhelp[data-help='report.findings']"), "? for the key findings");
  const methodsH3 = [...document.querySelectorAll("#methods > h3 > button.qhelp")].map((x) => x.dataset.help);
  assert.ok(methodsH3.includes("report.methods"), "Settings used");
  assert.deepEqual(errors, []);
});

test("every QC tab has its own ?", async () => {
  const { window, document, errors } = await loadReport();
  const tabs = [...document.querySelectorAll("#qc .tabs button")].map((t) => t.dataset.k);
  assert.ok(tabs.length >= 8);
  for (const k of tabs) {
    click(window, document.querySelector(`#qc .tabs button[data-k='${k}']`));
    const b = document.querySelector("#qcbody > .sub > button.qhelp");
    assert.ok(b, `? on the ${k} tab`);
    assert.equal(b.dataset.help, "qc." + k);
    assert.ok(BASE.help.entries["qc." + k], `help entry qc.${k}`);
    click(window, b);
    assert.ok(document.querySelector("#qcbody > .sub + .helppanel"), `${k}: panel opens`);
  }
  assert.deepEqual(errors, []);
});

test("an issue box gets a ? with that issue's help, and the Help section lists it", async () => {
  const { window, document, errors } = await loadReport();
  // the fixture's small table raises more than one issue: take the "few features" one
  const box = [...document.querySelectorAll(".issues > .issue")].find((x) => {
    const q = x.querySelector(".sev > button.qhelp");
    return q && q.dataset.help === "issue.FEW_FEATURES";
  });
  assert.ok(box, "the fixture has the issue");
  const b = box.querySelector(".sev > button.qhelp");
  assert.ok(b);
  assert.equal(b.dataset.help, "issue.FEW_FEATURES");
  click(window, b);
  assert.ok(box.querySelector(".helppanel"), "the panel opens inside the issue box");
  const item = document.querySelector("#help-issue\\.FEW_FEATURES");
  assert.ok(item);
  assert.match(item.textContent, /Only 90 features in the analysis/);
  assert.deepEqual(errors, []);
});

test("every help link resolves to an entry in the page, and following it keeps the view's address", async () => {
  const { window, document, errors } = await loadReport();
  document.querySelectorAll("button.qhelp").forEach((b) => click(window, b)); // open every panel
  const links = [...document.querySelectorAll("a[data-help]")];
  assert.ok(links.length > 30, "the help cross-links");
  const missing = links.map((a) => a.dataset.help).filter((id) => !document.getElementById("help-" + id));
  assert.deepEqual([...new Set(missing)], [], "links to entries that aren't in the report");
  for (const a of links) assert.equal(a.getAttribute("href"), "#help-" + a.dataset.help, "a plain anchor works too");
  const before = window.location.hash;
  const a = links.find((x) => x.dataset.help === "glossary.imputation");
  assert.ok(a);
  click(window, a);
  assert.equal(window.location.hash, before, "the link is handled in the page, the address state stays");
  assert.equal(document.getElementById("help-glossary.imputation").open, true, "and opens the entry");
  assert.deepEqual(errors, []);
});

test("issue titles and codes from the data are escaped in the Help section", async () => {
  const hostile = `<img src=x onerror="window.__xss=1"> & "q" 'a' <script>window.__xss=2</script>`;
  const data = withData(BASE, {
    help: {
      issues: [
        { code: "NO_HITS", sev: "warning", title: hostile, id: "issue.NO_HITS" },
        { code: `<b>X</b>`, sev: "error", title: hostile, id: null },
      ],
    },
  });
  const { window, document, errors } = await loadReport({ data });
  assert.deepEqual(errors, []);
  assert.equal(window.__xss, undefined);
  const body = document.querySelector("#helpbody");
  assert.equal(body.querySelector("img"), null, "no injected element");
  assert.equal(body.querySelector("script"), null);
  assert.equal(body.querySelector(".hin b"), null, "a code is text, not markup");
  assert.ok(body.textContent.includes(hostile), "the title shows as written");
  assert.ok(body.textContent.includes("<b>X</b>"));
  assert.match(body.textContent, /No help for this one yet/);
});

test("a report without the help payload still renders, and says so", async () => {
  const data = { ...BASE };
  delete data.help;
  const { document, errors } = await loadReport({ data });
  assert.deepEqual(errors, []);
  assert.equal(document.querySelectorAll("button.qhelp").length, 0);
  assert.match(document.querySelector("#helpbody").textContent, /could not be included/);
  assert.ok(document.querySelector("#volcano svg"), "the rest of the report is unaffected");
});

test("the shipped fixture runs with its help as a browser would", async () => {
  const { document, errors } = await loadReportRaw();
  assert.deepEqual(errors, []);
  assert.ok(document.querySelectorAll("#helpbody details.helpitem").length > 40);
  assert.ok(document.querySelector("#differential > h2 > button.qhelp"));
});
