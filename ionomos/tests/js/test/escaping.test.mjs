// Escaping tests: gene/sample/description strings come from user data, so
// nothing they contain may reach a parsing context. The sinks that matter:
// table rows, tiles, the detail panel, every showTip() caller (tip.innerHTML
// is assigned directly — report.js's central XSS invariant), and the CSV
// export. Then finding F-01 (a `<!--` in the data used to break the data script), and the figure export
// (D62): file names, the SVG's title and <desc>, the zip's README and tables, and a loaded style file.
import { test } from "node:test";
import assert from "node:assert/strict";

import { loadReport, baseData, withData, loadReportRaw, blobText, blobBytes, unzip, until, readFixture, embedData } from "../lib/harness.mjs";

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

test("F-01: a data string containing <!-- can't break script-data tokenization", async () => {
  // In the HTML script-data state `<!--` starts a double-escaped tokenization; the later `</script>` of the
  // report code tag is then not a real end tag and the data script swallows the report script. report.py
  // now writes `<!--` as `<\u0021--` (and `</` as `<\/`); the harness embeds data the same way.
  const data = JSON.parse(JSON.stringify(baseData()));
  data.f.label[0] = "<!--<script>window.__xss=1</script>";
  const html = readFixture().replace(
    /(<script id='ionomos-data' type='application\/json'>)[\s\S]*?(<\/script>)/,
    (_m, open, close) => open + embedData(data) + close
  );
  const { window, document } = await loadReportRaw({ html });
  assert.equal(window.__xss, undefined, "nothing executes");
  assert.ok(document.querySelector("#differential-body svg"), "the report still renders");
  const embedded = JSON.parse(document.querySelector("#ionomos-data").textContent);
  assert.equal(embedded.f.label[0], "<!--<script>window.__xss=1</script>", "the label survives as data");
});

// ---- figure export: names from the data reach file names, SVG text, a text file and CSV cells

function hostileExport() {
  const d = hostileData();
  d.title = `..\\..\\CON<script>window.__xss=1</script>\r\nExported: never`;
  d.comps[0].name = `<<"&">> vs <script>window.__xss=1</script>\r\n  figures/00_forged.svg  a forged line`;
  d.comps[0].slug = `..\\../evil<script>:*?"|\u0000name.exe.svg`;
  d.f.label[1] = `=HYPERLINK("http://evil.example","x")`;
  d.f.label[2] = "bell\u0007 and null\u0000 in a name";
  d.cond = d.cond.map((c) => (c === d.conditions[0] ? `</text><script>window.__xss=1</script>` : c));
  d.conditions = [`</text><script>window.__xss=1</script>`, d.conditions[1]];
  return d;
}
const SAFE_FILE = /^[A-Za-z0-9][A-Za-z0-9_+-]*(\.[A-Za-z0-9_+-]+)*$/;

test("export: a hostile comparison, experiment and gene name stay text in the SVG and never shape a file name", async () => {
  const { window, document, errors } = await loadReport({ data: hostileExport() });
  assert.deepEqual(errors, []);
  document.querySelector("#volcano .tools button").click();
  const dl = window.__downloads.at(-1);
  assert.match(dl.name, SAFE_FILE, `a safe file name: ${dl.name}`);
  assert.ok(!/\.\.|[\\/:*?"<>|\u0000]/.test(dl.name) && dl.name.endsWith(".svg") && dl.name.length <= 120);
  const text = await blobText(window, dl.blob);
  assert.ok(!/<script|<img|<\/text><script/i.test(text), "no markup from the data: every < of a name is written &lt;");
  assert.ok(!/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(text), "no control characters: the file stays valid XML");
  const doc = new window.DOMParser().parseFromString(text, "image/svg+xml");
  assert.equal(doc.querySelector("parsererror"), null, "well-formed");
  assert.equal(doc.querySelector("script, img, foreignObject"), null);
  const all = [...doc.documentElement.querySelectorAll("*")];
  assert.ok(all.every((e) => [...e.attributes].every((a) => !/^on/i.test(a.name) && !/javascript:/i.test(a.value))), "no handler or script URL in any attribute");
  assert.ok(doc.querySelector("title").textContent.includes(`<<"&">> vs <script>window.__xss=1</script>`), "the name is there, as text");
  assert.match(doc.querySelector("desc").textContent, /Comparison: <<"&">> vs <script>window\.__xss=1<\/script> {1,3}figures\/00_forged\.svg/);
  assert.equal(doc.querySelector("desc").textContent.split("\n").filter((l) => /^Exported: never|^ *figures\/00_forged/.test(l)).length, 0, "a name cannot add a line of its own");
  // the dialog lists the figure by name, as text
  document.querySelector("#volcano .tools button:nth-child(3)").click();
  const dlg = document.querySelector("#xdlg");
  assert.equal(dlg.querySelector("script, img[src='x']"), null);
  assert.ok([...dlg.querySelectorAll("#xfig option")].some((o) => o.textContent.includes("<script>window.__xss=1</script>")));
  assert.ok(document.querySelector("#xtitletext").placeholder.includes("<script>"), "the automatic title is shown as text");
  assert.equal(window.__xss, undefined);
});

test("export: the zip's names are safe, its README cannot be forged by a name, its CSV cells cannot be formulas", async () => {
  const { window, document, errors } = await loadReport({ data: hostileExport(), storage: { "ionomos.export.v1": { zip_format: "svg" } } });
  document.querySelector("#slides").click();
  await until(() => window.__downloads.length === 1);
  assert.deepEqual(errors, []);
  const zipName = window.__downloads[0].name;
  assert.match(zipName, SAFE_FILE);
  assert.ok(zipName.endsWith("_figures.zip") && !/\.\.|<|>/.test(zipName));
  const files = unzip(await blobBytes(window, window.__downloads[0].blob));
  assert.ok(files.every((f) => f.crcOk));
  for (const f of files) {
    const parts = f.name.split("/");
    assert.ok(parts.length === 2 || parts.length === 3, `no deeper folder: ${f.name}`);
    for (const part of parts) {
      assert.match(part, SAFE_FILE, `a safe name: ${f.name}`);
      assert.ok(!/^(con|prn|aux|nul|com\d|lpt\d)(\.|$)/i.test(part), `not a Windows device name: ${part}`);
    }
    assert.ok(!f.name.includes("..") && f.name.length < 180);
  }
  assert.ok(files[0].name.startsWith("CON_script_window"), "the folder is named after the experiment, made safe");
  const text = (n) => new TextDecoder().decode(files.find((f) => f.name.endsWith(n)).data);
  const readme = text("README.txt").split("\r\n");
  assert.ok(readme.some((l) => l.includes("<script>window.__xss=1</script>")), "names are plain text there");
  assert.equal(readme.filter((l) => /^Exported: never/.test(l) || /^ {2}figures\/00_forged\.svg/.test(l)).length, 0, "a name with a line break cannot add a line");
  assert.equal(readme.filter((l) => /^Exported: {4}\d{4}-\d\d-\d\d \d\d:\d\d$/.test(l)).length, 1, "the real date line");
  assert.ok(!/[\u0000-\u0009\u000b\u000c\u000e-\u001f]/.test(readme.join("\n")), "no control characters");
  const csvName = files.find((f) => /tables\/results_/.test(f.name)).name;
  const csv = text(csvName.split("/").slice(1).join("/")).split("\n");
  const formula = csv.find((l) => l.includes("HYPERLINK"));
  assert.ok(formula.split(",")[1].startsWith(`"'=HYPERLINK(""http://evil.example""`), "a leading = is defused with an apostrophe");
  assert.ok(csv.every((l) => !/^[=+@-]/.test(l)));
  const samples = text("tables/samples.csv").split("\n");
  assert.ok(samples.some((l) => l.startsWith(`"<script>alert(""s"")</script>",`)), "sample names quoted");
  for (const f of files.filter((x) => x.name.endsWith(".svg"))) {
    const doc = new window.DOMParser().parseFromString(new TextDecoder().decode(f.data), "image/svg+xml");
    assert.equal(doc.querySelector("parsererror"), null, `${f.name} is well-formed`);
    assert.equal(doc.querySelector("script, img, foreignObject"), null, `${f.name} holds no markup from the data`);
  }
  assert.equal(window.__xss, undefined);
});

test("export: a loaded style file is untrusted input; only checked values are used", async () => {
  const { window, document, errors } = await loadReport({ storage: {} });
  document.querySelector("#xopen").click();
  const input = document.querySelector("#xload");
  const hostile = '{"ionomos_export_style":1,"font_family":"Arial\\" onload=\\"window.__xss=1","up":"url(javascript:window.__xss=1)","down":"#12345","background":"<script>window.__xss=1</script>",' +
    '"size":"custom","width":"1e9","unit":"em","palette":"colorblind","__proto__":{"polluted":1},"constructor":{"prototype":{"polluted":1}},"<img src=x onerror=window.__xss=1>":true}';
  Object.defineProperty(input, "files", { configurable: true, value: [new window.File([hostile], `"><img src=x onerror=window.__xss=1>.json`)] });
  input.dispatchEvent(new window.Event("change", { bubbles: true }));
  await until(() => /Style loaded/.test(document.querySelector("#xmsg").textContent));
  const msg = document.querySelector("#xmsg");
  assert.equal(msg.children.length, 0, "the report of what was left out is text");
  assert.match(msg.textContent, /left out: .*font_family.*up.*down.*background/);
  assert.equal(document.querySelector("#xdlg img[src='x']"), null);
  const kept = JSON.parse(window.localStorage.getItem("ionomos.export.v1"));
  assert.equal(kept.palette, "colorblind", "the one valid choice is taken");
  assert.equal(kept.size, "custom");
  assert.deepEqual([kept.font_family, kept.up, kept.down, kept.background, kept.width, kept.unit], ["Arial", "#e34948", "#2a78d6", "light", 1280, "px"], "bad values keep the defaults");
  assert.deepEqual(Object.keys(kept).filter((k) => /proto|constructor|img/.test(k)), []);
  assert.equal(window.Object.prototype.polluted, undefined);
  document.querySelector("#xsvg").click();
  const text = await blobText(window, window.__downloads.at(-1).blob);
  assert.ok(!/onload|javascript:|<script/i.test(text));
  assert.equal(new window.DOMParser().parseFromString(text, "image/svg+xml").documentElement.getAttribute("font-family"), "Arial, Helvetica, sans-serif");
  assert.equal(window.__xss, undefined);
  assert.deepEqual(errors, []);
});
