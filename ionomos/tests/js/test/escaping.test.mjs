// Escaping tests: gene/sample/description strings come from user data, so
// nothing they contain may reach a parsing context. The sinks that matter:
// table rows, tiles, the detail panel, every showTip() caller (tip.innerHTML
// is assigned directly — report.js's central XSS invariant), and the CSV
// export. The skipped test at the end is the reproduction for finding F-01
// (report.py's `</` escaping does not neutralise `<!--` in the data script).
import { test } from "node:test";
import assert from "node:assert/strict";

import { loadReport, baseData, withData, loadReportRaw, blobText, readFixture } from "../lib/harness.mjs";

const BASE = baseData();
const SCRIPT = "<script>window.__xss=1</script>";
const HOSTILE = {
  label: `<img src=x onerror=window.__xss=1> & "quotes" 'apostrophes'`,
  id: SCRIPT,
  desc: `<b>&"' &lt;already-escaped&gt;</b>`,
};
const nF = BASE.v.length;

function hostileData() {
  const fc = BASE.comps[0].fc.slice();
  const p = BASE.comps[0].p.slice();
  const q = BASE.comps[0].q.slice();
  fc[0] = 3; // make the hostile feature the top-ranked significant hit, so
  p[0] = 1e-12; // the first rendered volcano circle is feature 0 and a hover
  q[0] = 1e-10; // on it shows the hostile label in the tooltip
  return withData(BASE, {
    f: {
      id: Array.from({ length: nF }, (_, i) => (i === 0 ? HOSTILE.id : BASE.f.id[i])),
      label: Array.from({ length: nF }, (_, i) => (i === 0 ? HOSTILE.label : BASE.f.label[i])),
      desc: Array.from({ length: nF }, (_, i) => (i === 0 ? HOSTILE.desc : BASE.f.desc[i])),
    },
    samples: [`<script>alert("s")</script>`, `a"b&c'd`, ...BASE.samples.slice(2)],
    cond: [`<c&d>`, `D"MS'O&`, ...BASE.cond.slice(2)],
    conditions: [`<c&d>`, `D"MS'O&`],
    comps: [
      withData(BASE.comps[0], {
        fc, p, q,
        name: `<<"&">> vs <script>1</script>`,
      }),
    ],
  });
}

/** Untick "significant only" so every feature (including the hostile one) gets a row. */
function showAllRows(document) {
  const sig = document.querySelector("#sigonly");
  if (sig && sig.checked) sig.click();
}

function findHostileRow(document) {
  return [...document.querySelectorAll("#table tbody tr")].find((tr) =>
    tr.textContent.includes(`"quotes"`)
  );
}

test("hostile gene/sample/condition names never execute or inject elements", async () => {
  const { window, document, errors } = await loadReport({ data: hostileData() });
  assert.deepEqual(errors, [], "no page errors with hostile names");
  assert.equal(window.__xss, undefined, "no injected script ran");
  assert.equal(document.querySelector("img[src='x']"), null, "no injected img");
  showAllRows(document);
  const table = document.querySelector("#table");
  assert.ok(findHostileRow(document), "the hostile feature renders as a table row");
  // ...as text, nowhere as markup
  for (const sink of ["#tiles", "#table", "#detail", "#differential-body", "#qc"]) {
    const host = document.querySelector(sink);
    assert.ok(host, `${sink} rendered`);
    assert.equal(host.querySelector("img[src='x']"), null, `no img in ${sink}`);
    assert.equal(host.querySelector("script"), null, `no script element in ${sink}`);
  }
});

test("hostile description round-trips as text in the detail panel", async () => {
  const { document } = await loadReport({ data: hostileData() });
  showAllRows(document);
  const row = findHostileRow(document);
  assert.ok(row, "hostile row present after unticking significant-only");
  row.click();
  const detail = document.querySelector("#detail");
  assert.equal(detail.querySelector("img[src='x']"), null, "detail does not inject the img");
  assert.equal(detail.querySelector("script"), null, "detail does not inject a script");
  assert.ok(detail.textContent.includes(`"quotes"`), "hostile label appears as text in the detail");
  assert.ok(detail.textContent.includes("already-escaped"), "hostile description appears as text");
});

test("tooltip (showTip) stays text-safe for hostile names on the volcano", async () => {
  const { window, document } = await loadReport({ data: hostileData() });
  const tip = document.querySelector("#tip");
  assert.ok(tip, "tip element exists");
  const hit = document.querySelector("#differential-body svg rect[style*='crosshair']");
  assert.ok(hit, "volcano hit rect exists");
  // aim at a real rendered point: hover finds the nearest point within a
  // small radius, so an arbitrary coordinate would miss and hide the tip.
  // Feature 0 is set up as the most significant hit, so it is the topmost
  // circle (smallest cy); significant points are drawn last, on top.
  const circles = [...document.querySelectorAll("#differential-body svg circle")];
  assert.ok(circles.length > 0, "volcano points rendered");
  const top = circles.reduce((a, b) => (+b.getAttribute("cy") < +a.getAttribute("cy") ? b : a));
  const mx = Math.round(+top.getAttribute("cx")), my = Math.round(+top.getAttribute("cy"));
  hit.dispatchEvent(new window.MouseEvent("mousemove", { clientX: mx, clientY: my, bubbles: true }));
  assert.equal(tip.style.display, "block", "tip shown");
  assert.equal(tip.querySelector("img[src='x']"), null, "tip does not inject the img");
  assert.equal(tip.querySelector("script"), null, "tip does not inject a script");
  assert.ok(tip.textContent.includes(HOSTILE.label.slice(0, 6)), "tip shows the label as text");
});

test("hostile feature name arrives in the page's embedded JSON as data (not markup)", async () => {
  const { document } = await loadReport({ data: hostileData() });
  const embedded = JSON.parse(document.querySelector("#ionomos-data").textContent);
  assert.equal(embedded.f.label[0], HOSTILE.label);
  assert.equal(embedded.f.id[0], HOSTILE.id);
});

test("CSV export quotes hostile cells and captures as a blob", async () => {
  const { window, document } = await loadReport({ data: hostileData() });
  showAllRows(document);
  document.querySelector("#csv").click();
  assert.equal(window.__blobs.length, 1, "one CSV blob created");
  const csv = await blobText(window, window.__blobs[0]);
  assert.ok(csv.split("\n").length > 2, "CSV has header and data rows");
  assert.ok(!csv.startsWith("="), "no formula-leading cell at file start");
  // text cells are quoted with inner quotes doubled; find the hostile label's row
  const row = csv.split("\n").find((l) => l.includes(`""quotes""`));
  assert.ok(row, "the hostile label appears with its quotes doubled");
  const cells = row.split(",");
  assert.ok(cells[1].startsWith('"') && cells[1].endsWith('"'), "label cell is quoted");
  assert.ok(cells[2].startsWith('"'), "description cell is quoted");
});

test("raw-mode load of the shipped fixture runs setup (regression guard on the escaping pipeline)", async () => {
  // runScripts: "dangerously" makes jsdom parse and run the page exactly like
  // a browser. The shipped escaping (`</` -> `<\/`) must keep the data script
  // intact through real HTML tokenization.
  const { window } = await loadReportRaw();
  assert.equal(window.__xss, undefined, "fixture is clean");
});

test.skip("F-01 repro: a data string containing <!-- breaks script-data tokenization", async () => {
  // report.py:296 escapes `</` but not `<!--`. In the HTML script-data state,
  // `<!--` starts a double-escaped tokenization; the later `</script>` of the
  // report code tag is then not a real end tag, the data script swallows the
  // report script, and the page renders empty. Skipped until F-01 is fixed:
  // the fix (escape `<` as \u003c in report.py) flips this test on.
  const data = JSON.parse(JSON.stringify(baseData()));
  data.f.label[0] = "<!--<script>window.__xss=1</script>";
  const html = readFixture().replace(
    /(<script id='ionomos-data' type='application\/json'>)[\s\S]*?(<\/script>)/,
    (_m, open, close) => open + JSON.stringify(data) + close
  );
  const { window, document } = await loadReportRaw({ html });
  assert.equal(window.__xss, undefined, "no execution expected either way");
  assert.ok(document.querySelector("#differential-body svg"), "report still renders (fails while F-01 is open)");
});
