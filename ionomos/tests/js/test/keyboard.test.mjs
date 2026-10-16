// Keyboard and screen-reader basics (round-4 audit): the QC and enrichment tabs as tab lists, the keyboard
// staying on a control whose section is drawn again (tabs, selects, pagers, column headers), the ? help
// buttons and their panels on every section, the export dialog keeping and giving back the keyboard, the
// file pickers reachable by Tab, the two-way switches saying which half is on, and every chart named.
import { test } from "node:test";
import assert from "node:assert/strict";

import { loadReport, baseData } from "../lib/harness.mjs";

const BASE = baseData();
const tick = () => new Promise((r) => setTimeout(r, 0));  // the focus is given back once the section is drawn
const key = (window, el, k, extra = {}) => el.dispatchEvent(new window.KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true, ...extra }));

test("the QC tabs are a tab list: one Tab stop, arrows, Home and End choose, the keyboard stays on the tab", async () => {
  const { window, document, errors } = await loadReport();
  const bar = document.querySelector("#qc .tabs");
  assert.equal(bar.getAttribute("role"), "tablist");
  const tabs = () => [...document.querySelectorAll("#qc .tabs button")];
  assert.ok(tabs().every((b) => b.getAttribute("role") === "tab"));
  const on = () => tabs().find((b) => b.getAttribute("aria-selected") === "true");
  assert.equal(tabs().filter((b) => b.tabIndex === 0).length, 1, "one Tab stop");
  assert.equal(on().tabIndex, 0);
  assert.equal(document.querySelector("#qcbody").getAttribute("role"), "tabpanel");
  assert.equal(document.querySelector("#qcbody").getAttribute("aria-labelledby"), on().id);
  const keys = tabs().map((b) => b.dataset.k);
  on().focus();
  const start = keys.indexOf(on().dataset.k);
  key(window, document.activeElement, "ArrowRight");
  await tick();
  assert.equal(on().dataset.k, keys[(start + 1) % keys.length], "ArrowRight chooses the next tab");
  assert.equal(document.activeElement, on(), "and the keyboard is on it");
  key(window, document.activeElement, "End");
  await tick();
  assert.equal(on().dataset.k, keys.at(-1));
  assert.equal(document.activeElement, on());
  key(window, document.activeElement, "ArrowRight");
  await tick();
  assert.equal(on().dataset.k, keys[0], "wraps around");
  key(window, document.activeElement, "ArrowLeft");
  await tick();
  assert.equal(on().dataset.k, keys.at(-1));
  key(window, document.activeElement, "Home");
  await tick();
  assert.equal(on().dataset.k, keys[0]);
  assert.equal(document.activeElement, on());
  // Enter / Space on a tab is a click: the clicked tab keeps the keyboard after the tabs are drawn again
  const pca = tabs().find((b) => b.dataset.k === "pca");
  pca.focus();
  pca.click();
  await tick();
  assert.equal(document.activeElement.id, "qctab-pca");
  assert.deepEqual(errors, []);
});

test("the enrichment tabs are a tab list too", async () => {
  const { window, document, errors } = await loadReport();
  const tabs = () => [...document.querySelectorAll("#enrich .tabs button")];
  assert.equal(document.querySelector("#enrich .tabs").getAttribute("role"), "tablist");
  if (tabs().length < 2) return;  // the fixture has both: over-representation and ranked
  const first = tabs()[0];
  first.focus();
  key(window, first, "ArrowRight");
  await tick();
  assert.equal(tabs()[1].getAttribute("aria-selected"), "true");
  assert.equal(document.activeElement, tabs()[1]);
  assert.equal(document.querySelector("#enrbody").getAttribute("aria-labelledby"), tabs()[1].id);
  assert.deepEqual(errors, []);
});

test("a select, a pager button or a column header that redraws its section keeps the keyboard", async () => {
  const { window, document, errors } = await loadReport();
  // pager: every feature, so there are two pages
  document.querySelector("#sigonly").click();
  let next = document.querySelector("#pnext");
  next.focus();
  next.click();
  await tick();
  assert.match(document.querySelector("#table .pager").textContent, /page 2 of 2/);
  assert.equal(document.activeElement.id, "pnext", "the new › has the keyboard");
  // column header: reachable, says how the table is sorted, Enter sorts and keeps the keyboard
  const th = () => document.querySelector("#table th[data-k='fc']");
  assert.equal(th().getAttribute("tabindex"), "0");
  assert.equal(document.querySelector("#table th[data-k='p']").getAttribute("aria-sort"), "ascending");
  th().focus();
  key(window, th(), "Enter");
  await tick();
  assert.equal(th().getAttribute("aria-sort"), "ascending");
  assert.equal(document.activeElement, th(), "the header keeps the keyboard");
  key(window, th(), " ");
  await tick();
  assert.equal(th().getAttribute("aria-sort"), "descending", "Space sorts the other way");
  // a select in the PCA tab redraws the tab
  document.querySelector("#qc .tabs button[data-k='pca']").click();
  const pcy = document.querySelector("#pcy");
  pcy.focus();
  pcy.value = "0";
  pcy.dispatchEvent(new window.Event("change", { bubbles: true }));
  await tick();
  assert.equal(document.activeElement.id, "pcy");
  assert.equal(document.querySelector("#pcy").value, "0");
  assert.deepEqual(errors, []);
});

test("the keyboard is not pulled back to a control the user has left", async () => {
  const { window, document, errors } = await loadReport();
  const pca = document.querySelector("#qc .tabs button[data-k='pca']");
  pca.focus();
  document.querySelector("#search").focus();  // the user moved on
  document.querySelector("#qc .tabs button[data-k='corr']").click();
  await tick();
  assert.equal(document.activeElement.id, "search");
  assert.deepEqual(errors, []);
});

test("every section title has a ? that opens its help, Escape and × close it and give the keyboard back", async () => {
  const { window, document, errors } = await loadReport();
  const sections = [...document.querySelectorAll("main > section[id]")].filter((s) => s.id !== "help" && s.id !== "overview");
  for (const s of sections) {
    const b = s.querySelector(":scope > h2 > button.qhelp");
    assert.ok(b, `? on #${s.id}${s.hidden ? " (hidden until it has data)" : ""}`);
    const entry = BASE.help.entries[b.dataset.help];
    assert.ok(entry, `help entry ${b.dataset.help}`);
    assert.equal(b.type, "button");
    assert.equal(b.getAttribute("aria-label"), "Help: " + entry.t);
    b.focus();
    b.click();
    const panel = document.getElementById(b.getAttribute("aria-controls"));
    assert.ok(panel && panel.classList.contains("helppanel"), `#${s.id}: the ? controls its panel`);
    assert.equal(panel.querySelector(".hh b").textContent, entry.t);
    for (const a of panel.querySelectorAll("a[data-help]")) assert.ok(document.getElementById("help-" + a.dataset.help), `#${s.id}: link to ${a.dataset.help}`);
    // Escape inside the panel closes it, and the keyboard goes back to the ?
    panel.querySelector(".hclose").focus();
    key(window, panel.querySelector(".hclose"), "Escape");
    assert.equal(panel.isConnected, false, `#${s.id}: Escape closes`);
    assert.equal(b.getAttribute("aria-expanded"), "false");
    assert.equal(document.activeElement, b, `#${s.id}: the keyboard is back on the ?`);
    // × too
    b.click();
    const p2 = document.getElementById(b.getAttribute("aria-controls"));
    p2.querySelector(".hclose").focus();
    p2.querySelector(".hclose").click();
    assert.equal(p2.isConnected, false);
    assert.equal(document.activeElement, b, `#${s.id}: × gives the keyboard back`);
    // Escape on the ? itself closes an open panel
    b.click();
    key(window, b, "Escape");
    assert.equal(b.getAttribute("aria-expanded"), "false");
  }
  assert.deepEqual(errors, []);
});

test("every ? on the page opens a panel with its entry's title (QC tabs, cut-offs, options, issues, methods)", async () => {
  const { document, errors } = await loadReport();
  document.querySelector("#opts").click();
  const all = [...document.querySelectorAll("button.qhelp")];
  assert.ok(all.length >= 25, `${all.length} ? buttons`);
  for (const b of all) {
    b.click();
    const panel = document.getElementById(b.getAttribute("aria-controls"));
    assert.ok(panel, `${b.dataset.help} opens`);
    assert.equal(panel.querySelector(".hh b").textContent, BASE.help.entries[b.dataset.help].t);
    b.click();
    assert.equal(panel.isConnected, false, `${b.dataset.help} closes`);
  }
  assert.deepEqual(errors, []);
});

test("the export dialog keeps the keyboard inside and gives it back to what opened it", async () => {
  const { window, document, errors } = await loadReport();
  const opener = document.querySelector("#slides").previousElementSibling;  // the theme button: any button
  assert.ok(opener);
  const xopen = document.querySelector("#xopen");
  document.querySelector("#opts").click();
  xopen.focus();
  xopen.click();
  const dlg = document.querySelector("#xdlg");
  assert.equal(dlg.hidden, false);
  assert.equal(document.activeElement.id, "xclose", "opens on its close button");
  const controls = [...dlg.querySelectorAll("button, select, input, textarea, a[href]")].filter((x) => !x.disabled && !x.closest("[hidden]"));
  controls.at(-1).focus();
  key(window, controls.at(-1), "Tab");
  assert.equal(document.activeElement, controls[0], "Tab past the last control comes back to the first");
  key(window, controls[0], "Tab", { shiftKey: true });
  assert.equal(document.activeElement, controls.at(-1), "and Shift+Tab the other way");
  key(window, document.activeElement, "Escape");
  assert.equal(dlg.hidden, true);
  assert.equal(document.activeElement, xopen, "Escape gives the keyboard back to Figure export…");
  // opened from a chart's own Export… button (which the preview draws again)
  const btn = [...document.querySelectorAll("#volcano .tools button")].find((b) => b.textContent === "Export…");
  btn.focus();
  btn.click();
  document.querySelector("#xclose").click();
  const again = [...document.querySelectorAll("#volcano .tools button")].find((b) => b.textContent === "Export…");
  assert.equal(document.activeElement, again, "back on the volcano's Export…");
  assert.deepEqual(errors, []);
});

test("the file pickers (Import .gmt, Load style) can be reached with Tab", async () => {
  const { window, document } = await loadReport();
  document.querySelector("#xopen").click();
  for (const id of ["gimport", "xload"]) {
    const el = document.querySelector("#" + id);
    assert.notEqual(window.getComputedStyle(el).display, "none", `#${id} is not display:none`);
    assert.ok(el.closest("label.filebtn"), `#${id} is inside its label`);
  }
});

test("the two-way switches say which half is on; every chart and the heatmap have a name", async () => {
  const { document, errors } = await loadReport();
  assert.equal(document.querySelector("#volc").getAttribute("aria-pressed"), "true");
  assert.equal(document.querySelector("#ma").getAttribute("aria-pressed"), "false");
  document.querySelector("#ma").click();
  assert.equal(document.querySelector("#ma").getAttribute("aria-pressed"), "true");
  assert.equal(document.querySelector("#volc").getAttribute("aria-pressed"), "false");
  assert.equal(document.querySelector("#dzoom").getAttribute("aria-pressed"), "true");
  for (const k of [...document.querySelectorAll("#qc .tabs button")].map((b) => b.dataset.k)) {
    document.querySelector(`#qc .tabs button[data-k='${k}']`).click();
    for (const s of document.querySelectorAll("main svg[role=img]")) {
      if (s.closest(".tile")) continue;  // the time-course pattern tiles carry a <title>
      assert.ok((s.getAttribute("aria-label") || "").length > 3, `a chart without a name (QC tab ${k}): ${s.parentElement.id}`);
    }
  }
  assert.match(document.querySelector("#volcano svg").getAttribute("aria-label"), /^Volcano plot|^MA/);
  assert.match(document.querySelector("#heatmap canvas").getAttribute("aria-label"), /^Heatmap of \d+ significant features/);
  assert.equal(document.querySelector("#heatmap canvas").getAttribute("role"), "img");
  assert.deepEqual(errors, []);
});
