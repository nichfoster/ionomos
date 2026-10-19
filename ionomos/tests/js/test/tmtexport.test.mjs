// #178: the report's own Export of the TMT QC figures says what sectionfigs.py's do (ionomos export --figures tmt).
// Both are checked against ../tmt_parity.json (the Python half: tests/test_tmt_export_parity.py): the footer is the
// experiment and the chart's own line, never the hits or the statistics; the title is not "<what>: <what>"; a
// channel chart is named by its plex ("plexB", not "plex plexB").
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { loadReport, baseData, blobText } from "../lib/harness.mjs";

const PARITY = JSON.parse(readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "tmt_parity.json"), "utf8"));
const SVGNS = "http://www.w3.org/2000/svg";

/** What a composed figure says around its chart, read the way the Python test reads it. */
function words(window, text) {
  const root = new window.DOMParser().parseFromString(text, "image/svg+xml").documentElement;
  const kids = [...root.children], at = kids.findIndex((e) => e.localName === "g");
  const txt = (e) => e.namespaceURI === SVGNS && e.localName === "text";
  const before = kids.slice(0, at).filter(txt).map((e) => e.textContent), after = kids.slice(at + 1).filter(txt).map((e) => e.textContent);
  const kid = (n) => kids.find((e) => e.localName === n);
  return { svg_title: kid("title").textContent, title: before[0], subtitle: before[1], note: after.at(-1) || "",
    desc: kid("desc").textContent.split("\n"), outside: kids.filter((e) => e.localName !== "g").map((e) => e.textContent).join("\n") };
}

async function exportSvg(window, document, host) {
  const n = window.__downloads.length;
  [...document.querySelectorAll(host + " > .tools button")].find((b) => b.textContent === "SVG").click();
  assert.equal(window.__downloads.length, n + 1, host + ": one file");
  const d = window.__downloads.at(-1);
  return { name: d.name, text: await blobText(window, d.blob) };
}

function check(window, key, text) {
  const want = PARITY.expect[key], got = words(window, text);
  for (const k of ["svg_title", "title", "subtitle", "note"]) assert.equal(got[k], want[k], key + " " + k);
  assert.deepEqual(got.desc.slice(0, want.desc.length), want.desc, key + " desc");
  for (const bad of PARITY.never) assert.ok(!got.outside.includes(bad), key + ": no '" + bad + "' around the chart");
}

test("the report's Export of the TMT QC figures matches sectionfigs.py (tmt_parity.json)", async () => {
  const data = { ...baseData(), title: PARITY.title, tmtqc: PARITY.tmtqc };
  const { window, document, errors } = await loadReport({ data });
  const seen = new Set();
  for (const [host, key] of [["#tmch", "tmt_channels_plexB"], ["#tmlab", "tmt_labelling"], ["#tmpur", "tmt_purity"]]) {
    const { name, text } = await exportSvg(window, document, host);
    assert.equal(name, key + ".svg");
    check(window, key, text);
    seen.add(key);
  }
  // the other plex: "plex KL6166", as Python names a plex whose name does not start with "plex"
  const sel = document.querySelector("#tmplex");
  sel.value = "1";
  sel.dispatchEvent(new window.Event("change", { bubbles: true }));
  const { name, text } = await exportSvg(window, document, "#tmch");
  assert.equal(name, "tmt_channels_KL6166.svg");
  check(window, "tmt_channels_KL6166", text);
  seen.add("tmt_channels_KL6166");
  assert.deepEqual([...seen].sort(), Object.keys(PARITY.expect).sort(), "every figure of the parity file compared");
  // a statistics figure keeps its hits and analysis line
  const v = words(window, (await exportSvg(window, document, "#volcano")).text);
  assert.match(v.note, / · hits: /);
  assert.ok(v.desc.some((l) => l.startsWith("Analysis: ")));
  assert.equal(document.querySelector("#tmch svg").getAttribute("aria-label"), "TMT channels: plex KL6166");
  assert.deepEqual(errors, []);
});
