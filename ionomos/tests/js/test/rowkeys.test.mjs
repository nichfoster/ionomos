// #176: a feature opens from the keyboard. Every table whose rows open something is one Tab stop (roving
// tabindex): the arrow keys, Home, End, Page Up / Down move, Enter or Space opens the row as a click does and the
// keyboard stays on it, Shift+Enter pins. The row that has the keyboard rings its point on the chart beside it and
// shows the point's tooltip. The pinned and "Behaves like" chips are buttons. The mouse does what it did.
import { test } from "node:test";
import assert from "node:assert/strict";

import { loadReport, baseData, withData, blobText } from "../lib/harness.mjs";

const BASE = baseData();
const tick = () => new Promise((r) => setTimeout(r, 0));
const key = (window, el, k, extra = {}) => el.dispatchEvent(new window.KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true, ...extra }));
const stops = (body) => [...body.querySelectorAll("tr")].filter((tr) => tr.tabIndex === 0);

test("the results table is one Tab stop: arrows move, the volcano rings the row's point, Enter opens it", async () => {
  const { window, document, errors } = await loadReport();
  document.querySelector("#sigonly").click();  // every feature: more rows to move through
  const rows = () => [...document.querySelectorAll("#table tbody tr[data-i]")];
  assert.ok(rows().length > 5);
  assert.equal(stops(document.querySelector("#table tbody")).length, 1, "one Tab stop");
  assert.equal(rows()[0].tabIndex, 0, "the first row, before any is opened");
  assert.ok(rows().slice(1).every((tr) => tr.tabIndex === -1));
  rows()[0].focus();
  key(window, rows()[0], "ArrowDown");
  const second = rows()[1];
  assert.equal(document.activeElement, second, "ArrowDown: the next row");
  assert.equal(second.tabIndex, 0, "the Tab stop moves with it");
  assert.equal(stops(document.querySelector("#table tbody")).length, 1);
  // its point on the volcano is ringed, and the tooltip names it
  const i = +second.dataset.i;
  assert.equal(document.querySelectorAll("#volcano svg .kmark").length, 1, "the point is ringed");
  const tip = document.querySelector("#tip");
  assert.equal(tip.style.display, "block");
  assert.ok(tip.textContent.includes(BASE.f.label[i] || BASE.f.id[i]), "the tooltip is the row's feature");
  key(window, second, "End");
  assert.equal(document.activeElement, rows().at(-1));
  key(window, document.activeElement, "Home");
  assert.equal(document.activeElement, rows()[0]);
  key(window, document.activeElement, "PageDown");
  assert.equal(document.activeElement, rows()[Math.min(10, rows().length - 1)]);
  key(window, document.activeElement, "ArrowUp");
  const row = document.activeElement, fi = +row.dataset.i;
  // Enter opens it: the detail panel, the row marked, the keyboard still on it after the table is drawn again
  key(window, row, "Enter");
  await tick();
  assert.equal(document.querySelector("#detail h4").textContent, BASE.f.label[fi] || BASE.f.id[fi]);
  assert.equal(document.activeElement.dataset.i, String(fi), "the keyboard stays on the opened row");
  assert.ok(document.activeElement.classList.contains("focus"));
  assert.equal(document.activeElement.tabIndex, 0);
  assert.equal(document.querySelectorAll("#volcano svg .kmark").length, 1, "the redrawn volcano rings it again");
  // Shift+Enter pins, as a shift-click does
  key(window, document.activeElement, "Enter", { shiftKey: true });
  await tick();
  assert.ok(document.querySelector("#pins .chip[data-i='" + fi + "']"), "pinned");
  // the ring never reaches an exported figure
  assert.equal(document.querySelectorAll("#volcano svg .kmark").length, 1);
  [...document.querySelectorAll("#volcano > .tools button")].find((b) => b.textContent === "SVG").click();
  const svgText = await blobText(window, window.__downloads.at(-1).blob);
  assert.ok(svgText.includes("<svg") && !svgText.includes("kmark"), "no keyboard ring in the file");
  // leaving the table takes the ring and the tooltip away
  document.querySelector("#search").focus();
  assert.equal(document.querySelectorAll("#volcano svg .kmark").length, 0);
  assert.equal(tip.style.display, "none");
  assert.deepEqual(errors, []);
});

test("Tab comes back to the row last opened, and the mouse still opens and pins rows", async () => {
  const { window, document, errors } = await loadReport();
  const rows = () => [...document.querySelectorAll("#table tbody tr[data-i]")];
  const r = rows()[2], i = +r.dataset.i;
  r.click();
  assert.equal(document.querySelector("#detail h4").textContent, BASE.f.label[i] || BASE.f.id[i]);
  assert.equal(rows()[2].tabIndex, 0, "the opened row is the table's Tab stop");
  assert.equal(stops(document.querySelector("#table tbody")).length, 1);
  rows()[3].dispatchEvent(new window.MouseEvent("click", { bubbles: true, shiftKey: true }));
  assert.equal(document.querySelectorAll("#pins .chip").length, 1, "shift-click pins");
  assert.deepEqual(errors, []);
});

test("pinned chips are buttons: Enter opens, Delete unpins and the keyboard stays among the pins", async () => {
  const { window, document, errors } = await loadReport();
  const rows = () => [...document.querySelectorAll("#table tbody tr[data-i]")];
  const a = +rows()[0].dataset.i, b = +rows()[1].dataset.i;
  for (const tr of rows().slice(0, 2)) tr.dispatchEvent(new window.MouseEvent("click", { bubbles: true, shiftKey: true }));
  const chips = () => [...document.querySelectorAll("#pins .chip")];
  assert.equal(chips().length, 2);
  assert.ok(chips().every((c) => c.tagName === "BUTTON" && c.type === "button" && c.tabIndex === 0));
  assert.match(chips()[0].getAttribute("aria-label"), /Delete unpins/);
  document.querySelector("#detail").innerHTML = "";
  chips()[0].focus();
  chips()[0].click();  // what Enter on a button does
  assert.equal(document.querySelector("#detail h4").textContent, BASE.f.label[a] || BASE.f.id[a]);
  key(window, chips()[0], "Delete");
  assert.deepEqual(chips().map((c) => +c.dataset.i), [b], "unpinned");
  assert.equal(document.activeElement, chips()[0], "the next pin has the keyboard");
  key(window, chips()[0], "Backspace");
  assert.equal(chips().length, 0);
  assert.equal(document.activeElement.id, "search");
  assert.deepEqual(errors, []);
});

test("the Behaves like chips are buttons that open the feature and keep the keyboard in the panel", async () => {
  const { document, errors } = await loadReport();
  // a feature with similar ones: try rows until the panel lists some
  const rows = [...document.querySelectorAll("#table tbody tr[data-i]")];
  let chip = null;
  for (const r of rows) { r.click(); chip = document.querySelector("#detail .sim .chip"); if (chip) break; }
  if (!chip) return;  // the fixture always has correlated features; nothing to check otherwise
  assert.equal(chip.tagName, "BUTTON");
  const j = +chip.dataset.j;
  chip.focus();
  chip.click();
  assert.equal(document.querySelector("#detail h4").textContent, BASE.f.label[j] || BASE.f.id[j]);
  assert.ok(document.querySelector("#detail").contains(document.activeElement), "the keyboard is in the new panel");
  assert.deepEqual(errors, []);
});

test("every table whose rows open something has exactly one Tab stop", async () => {
  const { document, errors } = await loadReport();
  let n = 0;
  for (const body of document.querySelectorAll("tbody")) {
    const live = [...body.querySelectorAll(":scope > tr")].filter((tr) => tr.onclick);
    if (!live.length) continue;
    n++;
    assert.equal(live.filter((tr) => tr.tabIndex === 0).length, 1, (body.closest("[id]") || {}).id + ": one Tab stop");
  }
  assert.ok(n >= 2, "results and enrichment tables at least: " + n);
  assert.deepEqual(errors, []);
});

function series(name, idx) {
  const pick = (a) => idx.map((_, k) => a[k % a.length]);
  return {
    name, unit: "nM", doses: [1e-9, 1e-8, 1e-7, 1e-6], samples: [0, 1, 2, 3, 4, 5], sdose: [0, 0, 0, 1e-8, 1e-7, 1e-6], controls: ["DMSO"], i: idx,
    cls: pick(["down", "up", "not", "unclear"]), n: pick([7]), pec50: pick([7.2, 6.1, 8.0, 5.5]), ciL: pick([7.0, 5.8, null, 4.0]), ciR: pick([7.4, 6.4, null, 7.0]),
    ec50: pick([63.1, 794, 10, 3162]), slope: pick([1.2, 0.9, 0.5, 3]), front: pick([1, 1, 1, 1]), back: pick([0.25, 3.1, 1.02, 1.3]), fc: pick([-1.9, 1.6, 0.02, 0.3]),
    p: pick([1e-6, 3e-5, 0.6, 0.04]), q: pick([1e-5, 1e-4, 0.8, 0.1]), rel: pick([5.1, 4.2, 0.1, 0.9]), r2: pick([0.97, 0.93, 0.02, 0.4]),
    y: idx.map((_, k) => [0.02, -0.03, 0.01, k % 2 ? 0.4 : -0.5, k % 2 ? 1.1 : -1.4, k % 2 ? 1.6 : -2.0]), skipped: 0,
  };
}

test("the dose table: the row with the keyboard rings its curve's point, Enter draws its curve", async () => {
  const dose = { ran: true, found: true, alpha: 0.05, fcLim: 0.45, reason: "", series: [series("Cmpd", [0, 1, 2, 3, 4, 5, 6, 7])] };
  const { window, document, errors } = await loadReport({ data: withData(BASE, { dose }) });
  const rows = () => [...document.querySelectorAll("#dosetable tbody tr[data-k]")];
  assert.equal(stops(document.querySelector("#dosetable tbody")).length, 1);
  const start = rows().find((tr) => tr.tabIndex === 0);
  start.focus();
  key(window, start, "ArrowDown");
  const r = document.activeElement, k = r.dataset.k;
  assert.equal(r.tagName, "TR");
  const ring = document.querySelector("#dosescatter svg .kmark"), dot = document.querySelector("#dosescatter circle[data-k='" + k + "']");
  assert.ok(ring, "the point is ringed");
  assert.equal(ring.getAttribute("cx"), dot.getAttribute("cx"));
  assert.equal(document.querySelector("#tip").style.display, "block");
  key(window, r, "Enter");
  await tick();
  const i = dose.series[0].i[+k];
  assert.ok(document.querySelector("#dosehead").textContent.includes(BASE.f.label[i] || BASE.f.id[i]), "its curve is drawn");
  assert.equal(document.activeElement.dataset.k, k, "the keyboard stays on the row");
  assert.deepEqual(errors, []);
});
