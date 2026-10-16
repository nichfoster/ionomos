// The search grammar (a word, a list, KRT*, /regex/flags, desc:, term:) fed junk (round-4 audit): empty and
// separator-only input, lone operators, flags that make a regex stateful, runs of wildcards, control
// characters, markup, 10,000-character input and a 5,000-name pasted list. Nothing may throw, hang or
// inject, and the answer under the box must say what was and was not found.
import { test } from "node:test";
import assert from "node:assert/strict";

import { loadReport, baseData, withData } from "../lib/harness.mjs";

const BASE = baseData();
const labels = BASE.f.label;

function type(window, document, value) {
  const box = document.querySelector("#search");
  box.focus();
  box.value = value;
  box.dispatchEvent(new window.Event("input", { bubbles: true }));
  return document.querySelector("#searchinfo").textContent;
}
const shown = (document) => [...document.querySelectorAll("#table tbody tr[data-i]")].map((r) => labels[+r.dataset.i]);

test("empty, blank and separator-only input: no search, nothing marked, nothing thrown", async () => {
  const { window, document, errors } = await loadReport();
  for (const s of ["", "   ", "\t", ",", ",,, ;;", " , ; "]) {
    assert.equal(type(window, document, s), "", `${JSON.stringify(s)}: nothing was asked, so no answer under the box`);
    assert.ok(shown(document).length > 0, `${JSON.stringify(s)}: the table still shows the hits`);
  }
  type(window, document, "");
  assert.ok(shown(document).length > 0, "the table is back to the hits");
  assert.deepEqual(errors, []);
});

test("lone operators and empty prefixes are read as words, not as broken patterns", async () => {
  const { window, document, errors } = await loadReport();
  for (const s of ["/", "//", "///", "desc:", "term:", "set:", "*", "?", "**", "*?*", "\\", "%", "$", "^", "(", "[", "{", "|"]) {
    const info = type(window, document, s);
    assert.ok(!/could not be drawn/.test(document.body.textContent), `${s}: a section broke`);
    assert.ok(info.length > 0, `${s}: an answer`);
  }
  assert.match(type(window, document, "*"), new RegExp(`^${labels.length} matches`), "* is every feature");
  assert.match(type(window, document, "/(/"), /not a valid pattern/);
  assert.match(type(window, document, "/a/qq"), /not a valid pattern/, "an unknown flag is reported, not thrown");
  assert.deepEqual(errors, []);
});

test("a word, a description or a pattern that finds nothing says so, and the table shows nothing", async () => {
  const { window, document, errors } = await loadReport();
  assert.match(type(window, document, "NOTAGENE"), /^0 matches · not found: NOTAGENE/);
  assert.match(document.querySelector("#table").textContent, /Nothing matches\./, "not the unfiltered hits");
  assert.match(type(window, document, "desc:no such words here"), /no description contains “no such words here”/);
  assert.match(type(window, document, "/^ZZZ\\d$/"), /nothing matches \/\^ZZZ\\d\$\//);
  assert.match(document.querySelector("#table").textContent, /Nothing matches\./);
  assert.match(type(window, document, "GAP"), /^1 match\b/, "a part of a name still finds it");
  assert.deepEqual(errors, []);
});

test("/regex/ with the g or y flag gives the same matches as without (test() would otherwise carry its position)", async () => {
  const data = withData(BASE, { f: { ...BASE.f, label: BASE.f.label.map((l, i) => (i < 6 ? "KIN" + i : l)) } });
  const { window, document, errors } = await loadReport({ data });
  for (const s of ["/^KIN\\d$/", "/^KIN\\d$/g", "/^KIN\\d$/y", "/^kin\\d$/gi", "/^KIN\\d$/gimsu"]) {
    assert.match(type(window, document, s), /^6 matches/, s);
  }
  assert.deepEqual(errors, []);
});

test("runs of wildcards collapse; a 10,000-character input answers at once", async () => {
  const { window, document, errors } = await loadReport();
  const t0 = Date.now();
  const many = "*".repeat(3000) + "a" + "*".repeat(3000) + "b" + "*".repeat(3000) + "zzzz";
  assert.match(type(window, document, many), /^0 matches/);
  assert.match(type(window, document, "x".repeat(10000)), /^0 matches/);
  assert.match(type(window, document, "GAPDH" + "*".repeat(50)), /^1 match\b/);
  const ms = Date.now() - t0;
  assert.ok(ms < 5000, `${ms} ms`);
  assert.deepEqual(errors, []);
});

test("markup, quotes and control characters in the search are shown as text, in the address too", async () => {
  const { window, document, errors } = await loadReport();
  const evil = `<img src=x onerror="window.__xss=1"> NOTAGENE "q" 'a' \u0000 &amp;`;
  const info = type(window, document, evil);
  assert.equal(window.__xss, undefined);
  assert.equal(document.querySelector("#searchinfo img"), null);
  assert.match(info, /not found: .*<img/, "the missing names are listed as typed");
  assert.match(type(window, document, "term:<b>x</b>"), /no gene set matches “<b>x<\/b>”/);
  assert.equal(document.querySelector("#searchinfo b b"), null);
  assert.match(window.location.hash, /q=/, "the search is kept in the address");
  assert.ok(!/[<>"]/.test(window.location.hash), "escaped there");
  assert.deepEqual(errors, []);
});

test("a pasted list of 5,000 names: found and not found are counted, the list of misses is cut short", async () => {
  const { window, document, errors } = await loadReport();
  const names = labels.slice(0, 20).concat(Array.from({ length: 4980 }, (_, i) => "NOPE" + i));
  const t0 = Date.now();
  const info = type(window, document, names.join("\n").replace(/\n/g, ", "));
  assert.match(info, /found 20 of 5000/);
  assert.match(info, /not found: NOPE0, NOPE1/);
  assert.match(info, /…/, "the misses are cut after a dozen");
  assert.ok(info.length < 1000, "the line stays readable");
  assert.ok(Date.now() - t0 < 5000);
  // duplicates count once in the matches
  assert.match(type(window, document, "ACTB, ACTB, actb"), /^1 match\b/);
  assert.deepEqual(errors, []);
});

test("unicode case folding and accents do not throw (İ, ß, combining marks, emoji, RTL)", async () => {
  const data = withData(BASE, { f: { ...BASE.f, label: BASE.f.label.map((l, i) => (i === 0 ? "İSTANBUL" : i === 1 ? "straße" : i === 2 ? "🧬GENE" : l)) } });
  const { window, document, errors } = await loadReport({ data });
  assert.match(type(window, document, "🧬"), /^1 match\b/);
  assert.match(type(window, document, "straße"), /^1 match\b/);
  for (const s of ["İ", "ı", "é", "‮", "ﬁ", "🧬*", "/\\p{L}+/u"]) type(window, document, s);
  assert.deepEqual(errors, []);
});

test("Enter on one match opens it; Escape clears; the suggestions ignore junk", async () => {
  const { window, document, errors } = await loadReport();
  const box = document.querySelector("#search");
  const key = (k) => box.dispatchEvent(new window.KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true }));
  type(window, document, "/^GAPDH$/");
  assert.equal(document.querySelector("#suggest").hidden, true, "no suggestions for a regex");
  key("Enter");
  assert.equal(document.querySelector("#detail h4").textContent, "GAPDH");
  type(window, document, "***");
  assert.equal(document.querySelector("#suggest").hidden, true, "none for wildcards");
  key("ArrowDown");
  key("Escape");
  assert.equal(box.value, "");
  assert.deepEqual(errors, []);
});
