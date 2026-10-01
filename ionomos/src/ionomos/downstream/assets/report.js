/* Ionomos report: everything is drawn here from the JSON in #ionomos-data. No libraries, works offline. */
(function () {
  "use strict";
  const D = JSON.parse(document.getElementById("ionomos-data").textContent);
  const SVGNS = "http://www.w3.org/2000/svg";
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
  const nF = D.f.id.length, nS = D.samples.length;
  D.qc = D.qc || {};
  D.gsea = D.gsea || [];
  D.enr = D.enr || [];
  const tip = $("#tip");
  const ST = {
    ci: 0, lfc: D.settings.log2fc, alpha: D.settings.alpha, adj: D.settings.use_adjusted, labels: D.settings.top_labels,
    search: "", q: null, focus: null, pinned: [], mode: "volcano", drag: "zoom", zoom: null, sigOnly: true, sortKey: "p",
    sortDir: 1, page: 0, highlight: null, highlightName: "", rows: "features",
    opt: { pt: 1, lab: 11.5, labelMatches: true, lines: true, onoff: true, hideImp: false, minPep: 0 },
    groups: [],
  };

  // ---------------------------------------------------------------- helpers
  let EX = null;  // set while a figure is drawn for export ("figure export" below): the style then answers css(), widthOf(), heightOf()
  const css = (v) => (EX && EX.tokens[v] != null ? EX.tokens[v] : getComputedStyle(document.documentElement).getPropertyValue(v).trim());
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function svg(tag, attrs, parent) {
    const e = document.createElementNS(SVGNS, tag);
    for (const k in attrs || {}) if (attrs[k] != null) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function text(parent, x, y, s, attrs) {
    const t = svg("text", Object.assign({ x: x, y: y, fill: css("--muted"), "font-size": 12 }, attrs || {}), parent);
    t.textContent = s;
    return t;
  }
  function title(el, s) { el.appendChild(document.createElementNS(SVGNS, "title")).textContent = s; return el; }
  const fmt = (v, d) => (v == null || isNaN(v) ? "–" : (+v).toFixed(d == null ? 2 : d));
  function fmtP(p) {
    if (p == null || isNaN(p)) return "–";
    if (p === 0) return "0";
    if (p < 1e-3) { const e = Math.floor(Math.log10(p)); return (p / Math.pow(10, e)).toFixed(1) + "e" + e; }
    return p.toFixed(p < 0.01 ? 4 : 3);
  }
  const fmtInt = (n) => (n == null ? "–" : n.toLocaleString());
  const pct = (v, d) => (v == null ? "–" : (100 * v).toFixed(d == null ? 0 : d) + "%");
  function niceTicks(lo, hi, n) {
    if (!(hi > lo)) { hi = lo + 1; }
    const span = hi - lo, step0 = span / (n || 5), mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const err = step0 / mag, step = mag * (err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1);
    const out = [];
    for (let v = Math.ceil(lo / step - 1e-9) * step; v <= hi + 1e-9 * step; v += step) out.push(+v.toFixed(10));
    return out;
  }
  const condIndex = {};
  D.conditions.forEach((c, i) => (condIndex[c] = i));
  const condColor = (c) => css("--c" + ((condIndex[c] == null ? 0 : condIndex[c]) % 8));
  const nameOf = (i) => D.f.label[i] || D.f.id[i] || "";
  function showTip(e, html) {
    tip.innerHTML = html;
    tip.style.display = "block";
    const x = Math.min(e.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
    const y = Math.min(e.clientY + 14, window.innerHeight - tip.offsetHeight - 8);
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }
  const hideTip = () => (tip.style.display = "none");
  function download(name, content, type) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(content instanceof Blob ? content : new Blob([content], { type: type }));
    a.download = safeName(name);
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }
  function copyText(s, done) {
    const fallback = () => {
      const ta = document.createElement("textarea");
      ta.value = s; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand && document.execCommand("copy"); } catch (e) { /* nothing more to try */ }
      ta.remove();
      if (done) done();
    };
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(s).then(done || (() => {}), fallback);
      else fallback();
    } catch (e) { fallback(); }
  }
  function flash(el, msg) { if (!el) return; el.textContent = msg; clearTimeout(el._t); el._t = setTimeout(() => (el.textContent = ""), 2500); }
  /** A file name that is safe on Windows, macOS and inside a zip: ASCII letters, digits and . _ + - only, no
   * dot or underscore at either end, no "..", not a device name (CON, NUL, COM1 ...), at most 120 characters. */
  function safeName(name) {
    const s = String(name == null ? "" : name).replace(/[^\w.+-]+/g, "_");
    const m = /\.([A-Za-z0-9]{1,5})$/.exec(s), ext = m && m.index > 0 ? "." + m[1] : "";
    let stem = (ext ? s.slice(0, m.index) : s).replace(/[._]{2,}/g, "_").replace(/^[._-]+|[._]+$/g, "").slice(0, 110).replace(/[._]+$/, "");
    if (!stem) stem = "figure";
    if (/^(con|prn|aux|nul|com[0-9]|lpt[0-9])$/i.test(stem.split(".")[0])) stem = "_" + stem;
    return stem + ext;
  }
  /** The export buttons of one chart (the work is in "figure export" below). `again` draws just this chart
   * again (default: its section is drawn again); root is null for a chart that is a canvas on screen and has
   * an SVG twin for export (the heatmap). While a figure is drawn for export, the chart is handed over here. */
  function svgTools(host, root, name, again) {
    if (EX && root) EX.cap.push({ host: host, root: root, name: name });
    let t = $(".tools", host);
    if (!t) { t = document.createElement("div"); t.className = "tools"; host.appendChild(t); }
    t.innerHTML = "";
    const chart = { host: host, root: root, name: name, again: again };
    [["SVG", "Download this chart as SVG at the export settings: stays sharp, and can be edited in PowerPoint, Illustrator or Inkscape", () => exportOne(chart, "svg")],
      ["PNG", "Download this chart as a PNG picture at the export settings", () => exportOne(chart, "png")],
      ["Export…", "Size, text, colours, title and legend of exported figures; copy as an image; every figure in one .zip", () => openExport(chart)]].forEach(([label, what, fn]) => {
      const b = document.createElement("button");
      b.textContent = label;
      b.title = what;
      b.onclick = fn;
      t.appendChild(b);
    });
    return t;
  }
  function frame(host, w, h) {
    host.querySelectorAll(":scope > svg").forEach((s) => s.remove());
    const root = svg("svg", { viewBox: "0 0 " + w + " " + h, width: w, height: h, role: "img", style: "max-width:" + w + "px" });
    host.insertBefore(root, host.firstChild);
    return root;
  }
  function axes(g, X, Y, xt, yt, L, R, T, B, W, H, xl, yl) {
    const grid = css("--grid"), axis = css("--axis");
    yt.forEach((v) => {
      svg("line", { x1: L, x2: W - R, y1: Y(v), y2: Y(v), stroke: grid }, g);
      text(g, L - 6, Y(v) + 4, fmtTick(v), { "text-anchor": "end" });
    });
    xt.forEach((v) => {
      svg("line", { x1: X(v), x2: X(v), y1: T, y2: H - B, stroke: grid }, g);
      text(g, X(v), H - B + 16, fmtTick(v), { "text-anchor": "middle" });
    });
    svg("line", { x1: L, x2: W - R, y1: H - B, y2: H - B, stroke: axis }, g);
    svg("line", { x1: L, x2: L, y1: T, y2: H - B, stroke: axis }, g);
    if (xl) text(g, (L + W - R) / 2, H - 6, xl, { "text-anchor": "middle", fill: css("--text2"), "font-size": 13 });
    if (yl) text(g, 14, (T + H - B) / 2, yl, { "text-anchor": "middle", fill: css("--text2"), "font-size": 13, transform: "rotate(-90 14 " + (T + H - B) / 2 + ")" });
  }
  function goTo(id) { const e = document.getElementById(id); if (e) e.scrollIntoView({ behavior: "smooth", block: "start" }); }
  function fmtTick(v) { return Math.abs(v) >= 1000 ? v.toLocaleString() : String(+v.toFixed(3)); }
  function widthOf(host, fallback) { return EX ? EX.w : Math.max(320, Math.floor(host.clientWidth || fallback || 760)); }
  function heightOf(h) { return EX ? EX.h : h; }  // a chart that can take any height asks here, so an exported figure fills its size
  function median(xs) { if (!xs.length) return null; const s = xs.slice().sort((a, b) => a - b), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; }
  function pearson(a, b) {
    let n = 0, sa = 0, sb = 0;
    for (let k = 0; k < a.length; k++) if (a[k] != null && b[k] != null) { n++; sa += a[k]; sb += b[k]; }
    if (n < 3) return null;
    const ma = sa / n, mb = sb / n;
    let sab = 0, saa = 0, sbb = 0;
    for (let k = 0; k < a.length; k++) if (a[k] != null && b[k] != null) { const x = a[k] - ma, y = b[k] - mb; sab += x * y; saa += x * x; sbb += y * y; }
    return saa > 0 && sbb > 0 ? sab / Math.sqrt(saa * sbb) : null;
  }
  // One failing chart must never blank the page: draw each section in its own try.
  function safe(fn, hostSel) {
    try { fn(); } catch (e) {
      const h = typeof hostSel === "string" ? $(hostSel) : hostSel;
      if (h) h.innerHTML = "<div class='empty'>This part of the report could not be drawn (" + esc(e && e.message) + "). The TSV files hold the same numbers.</div>";
      if (window.console && console.warn) console.warn("Ionomos report:", e);
    }
  }
  const store = {
    get(k, d) { try { const v = window.localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { window.localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode: not remembered */ } },
  };

  // ------------------------------------------------------- feature lookup
  const geneOf = (i) => (D.f.label[i] || "").trim().split(/[;\s]/)[0].replace(/\.\d+$/, "").toUpperCase();
  let KEYS = null, FKEYS = null;
  function keyIndex() {
    if (KEYS) return KEYS;
    KEYS = new Map(); FKEYS = new Array(nF);
    for (let i = 0; i < nF; i++) {
      const ks = new Set();
      const lab = (D.f.label[i] || "").trim(), id = (D.f.id[i] || "").trim();
      if (lab) ks.add(lab.toUpperCase());
      lab.split(/[;,\s]+/).forEach((t) => { if (t) { ks.add(t.toUpperCase()); ks.add(t.replace(/\.\d+$/, "").toUpperCase()); } });
      id.split(/[|;,\s]+/).forEach((t) => {
        if (!t || /^(sp|tr)$/i.test(t)) return;
        ks.add(t.toUpperCase());
        ks.add(t.replace(/-\d+$/, "").toUpperCase());
        const m = /^([A-Za-z0-9]+)_[A-Za-z]+$/.exec(t);
        if (m) ks.add(m[1].toUpperCase());
      });
      FKEYS[i] = Array.from(ks);
      FKEYS[i].forEach((k) => { let a = KEYS.get(k); if (!a) KEYS.set(k, (a = [])); a.push(i); });
    }
    return KEYS;
  }
  function geneSetIndex() {
    // every gene set the page knows about: rank-based sets (all measured members) and ORA terms (the hits)
    const out = new Map();
    D.gsea.forEach((b) => b.terms.forEach((t) => { const k = t.term; const e = out.get(k) || { term: k, genes: new Set(), src: b.library }; t.genes.forEach((g) => e.genes.add(g)); out.set(k, e); }));
    D.enr.forEach((b) => b.terms.forEach((t) => { if (!out.has(t.term)) out.set(t.term, { term: t.term, genes: new Set(t.genes), src: b.library + " (hits)" }); }));
    return out;
  }
  let GSI = null;
  const gsets = () => GSI || (GSI = geneSetIndex());
  function indicesOfGenes(genes) {
    const K = keyIndex(), set = new Set();
    genes.forEach((g) => (K.get(String(g).toUpperCase()) || []).forEach((i) => set.add(i)));
    return set;
  }
  function wildRe(tok) { return new RegExp("^" + tok.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".") + "$", "i"); }

  /** Parse the search box: a word (substring), a list (exact names / accessions), wildcards, /regex/,
   * desc:words, term:gene set. Returns {set, found, missing, label, error}. */
  function parseQuery(raw) {
    const s = (raw || "").trim();
    if (!s) return null;
    keyIndex();
    const out = { set: new Set(), found: [], missing: [], label: "", error: "" };
    let m;
    if ((m = /^(term|set|go|pathway):\s*(.+)$/i.exec(s))) {
      const want = m[2].trim().toLowerCase(), hits = [];
      gsets().forEach((e) => { if (e.term.toLowerCase().includes(want)) hits.push(e); });
      hits.sort((a, b) => (a.term.toLowerCase() === want ? -1 : b.term.toLowerCase() === want ? 1 : a.term.length - b.term.length));
      const use = hits.filter((e) => e.term.toLowerCase() === want).length ? hits.filter((e) => e.term.toLowerCase() === want) : hits.slice(0, 5);
      use.forEach((e) => indicesOfGenes(e.genes).forEach((i) => out.set.add(i)));
      out.label = use.length ? use.map((e) => e.term).join(", ") + (hits.length > use.length ? " (+" + (hits.length - use.length) + " more terms)" : "") : "";
      if (!use.length) out.error = D.gsea.length || D.enr.length ? "no gene set matches “" + m[2].trim() + "”" : "no gene sets in this report (enrichment was off or offline)";
      return out;
    }
    if ((m = /^desc:\s*(.+)$/i.exec(s))) {
      const w = m[1].toLowerCase();
      for (let i = 0; i < nF; i++) if ((D.f.desc[i] || "").toLowerCase().includes(w)) out.set.add(i);
      return out;
    }
    if ((m = /^\/(.+)\/([a-z]*)$/.exec(s))) {
      let re;
      try { re = new RegExp(m[1], m[2].includes("i") ? m[2] : m[2] + "i"); } catch (e) { out.error = "not a valid pattern: " + e.message; return out; }
      for (let i = 0; i < nF; i++) if (re.test(D.f.label[i] || "") || re.test(D.f.id[i] || "") || FKEYS[i].some((k) => re.test(k))) out.set.add(i);
      return out;
    }
    const whole = KEYS.get(s.toUpperCase());
    const toks = s.split(/[\s,;]+/).filter(Boolean);
    if (toks.length === 1 && !/[*?]/.test(s)) {  // one word: anything containing it
      const q = s.toLowerCase();
      for (let i = 0; i < nF; i++) {
        if ((D.f.label[i] || "").toLowerCase().includes(q) || (D.f.id[i] || "").toLowerCase().includes(q) || (D.f.desc[i] || "").toLowerCase().includes(q)) out.set.add(i);
      }
      if (whole) out.found.push(s);
      return out;
    }
    if (whole && toks.length === 2 && !/[*?]/.test(s)) {  // "GAPDH C152": a site label with a space
      whole.forEach((i) => out.set.add(i));
      out.found.push(s);
      return out;
    }
    toks.forEach((t) => {
      let hit = [];
      if (/[*?]/.test(t)) {
        const re = wildRe(t);
        for (let i = 0; i < nF; i++) if (FKEYS[i].some((k) => re.test(k))) hit.push(i);
      } else hit = KEYS.get(t.toUpperCase()) || [];
      if (hit.length) { out.found.push(t); hit.forEach((i) => out.set.add(i)); } else out.missing.push(t);
    });
    return out;
  }

  // --------------------------------------------------------- comparisons
  const C = () => D.comps[ST.ci];
  function score(c, i) { return ST.adj ? c.q[i] : c.p[i]; }
  function rawSig(c, i) {
    const s = score(c, i), fc = c.fc[i];
    if (c.conf === "none") return fc == null || isNaN(fc) || Math.abs(fc) < (ST.lfc || 1) ? "" : (fc > 0 ? "up" : "down");
    if (s == null || fc == null || isNaN(s) || isNaN(fc)) return "";
    return s <= ST.alpha && Math.abs(fc) >= ST.lfc ? (fc > 0 ? "up" : "down") : "";
  }
  const IMPD = new Map();
  function groupsOf(c) {
    const t = [], k = [];
    D.cond.forEach((x, j) => { if (x === c.t1) t.push(j); else if (c.t2 === "others" || x === c.t2) k.push(j); });
    return [t, k];
  }
  /** 1 where at least half of either group's values were imputed (the fold change is partly made up). */
  function impDriven(c) {
    if (IMPD.has(c)) return IMPD.get(c);
    const out = new Uint8Array(nF);
    if (D.imp) {
      const [a, b] = groupsOf(c);
      for (let i = 0; i < nF; i++) {
        const r = D.imp[i];
        if (!r) continue;
        for (const g of [a, b]) {
          if (!g.length) continue;
          let k = 0;
          g.forEach((j) => { if (r[j] === "1") k++; });
          if (k / g.length >= 0.5) { out[i] = 1; break; }
        }
      }
    }
    IMPD.set(c, out);
    return out;
  }
  const lowEvidence = (i) => ST.opt.minPep > 0 && D.f.pep && D.f.pep[i] != null && D.f.pep[i] < ST.opt.minPep;
  function filteredOut(c, i) { return (ST.opt.hideImp && impDriven(c)[i] === 1) || lowEvidence(i); }
  function sigOf(c, i) { const s = rawSig(c, i); return s && !filteredOut(c, i) ? s : ""; }
  function pThreshold(c) {
    if (!ST.adj) return ST.alpha;
    let t = null;
    for (let i = 0; i < nF; i++) if (c.q[i] != null && c.q[i] <= ST.alpha && c.p[i] != null) t = t == null ? c.p[i] : Math.max(t, c.p[i]);
    return t;
  }
  function counts(c) {
    let up = 0, down = 0, tested = 0, filtered = 0;
    for (let i = 0; i < nF; i++) {
      if (c.p[i] != null || (c.conf === "none" && c.fc[i] != null)) tested++;
      const s = sigOf(c, i);
      if (s === "up") up++; else if (s === "down") down++;
      else if (rawSig(c, i)) filtered++;
    }
    return { up: up, down: down, tested: tested, filtered: filtered };
  }
  function matches(i) {
    if (ST.highlight) return ST.highlight.has(i);
    return !!(ST.q && ST.q.set.has(i));
  }
  const anyMark = () => !!(ST.highlight || (ST.q && (ST.q.set.size || ST.q.error)));
  function onoffOf(c) {
    const m = new Map();
    (c.onoff || []).forEach((x) => m.set(x[0], x));
    return m;
  }
  function confBadge(c) {
    const t = { low: "Low confidence", none: "Fold change only" }[c.conf];
    return t ? "<div class='notes' style='margin:6px 0 0;padding:3px 8px;font-size:12px'>" + t + "</div>" : "";
  }
  function topHits(c, dir, n) {
    const out = [];
    for (let i = 0; i < nF; i++) if (sigOf(c, i) === dir) out.push(i);
    out.sort((a, b) => (c.conf === "none" ? Math.abs(c.fc[b]) - Math.abs(c.fc[a]) : (c.p[a] - c.p[b]) || (Math.abs(c.fc[b]) - Math.abs(c.fc[a]))));
    return out.slice(0, n);
  }

  // ------------------------------------------------------------- overview
  function renderTiles() {
    const host = $("#tiles");
    if (!host) return;
    let h = '<div class="tile"><div class="k">' + esc(D.levelTitle) + ' in the analysis</div><div class="v">' + fmtInt(nF) +
      '</div><div class="d">' + esc(D.sourceName) + "</div></div>" +
      '<div class="tile"><div class="k">Samples</div><div class="v">' + nS + '</div><div class="d">' + D.conditions.length +
      " condition(s): " + esc(D.conditions.join(", ")) + "</div></div>";
    const card = D.qc.scorecard;
    if (card && card.length) {
      const bad = card.filter((r) => r.status !== "ok");
      h += '<div class="tile" style="cursor:pointer" data-go="quality"><div class="k">Sample quality</div><div class="v">' + (card.length - bad.length) + "/" + card.length +
        '</div><div class="d">' + (bad.length ? '<span class="pill ' + (bad.some((r) => r.status === "fail") ? "fail" : "warn") + '">' + bad.length + " flagged</span> " + esc(bad.map((r) => r.sample).slice(0, 3).join(", ")) : "all samples pass") + "</div></div>";
    }
    D.comps.forEach((c, k) => {
      const n = counts(c);
      h += '<div class="tile" style="cursor:pointer" data-ci="' + k + '"><div class="k">' + esc(c.name) + '</div><div class="v">' +
        fmtInt(n.up + n.down) + '</div><div class="d"><span class="dot up"></span> ' + fmtInt(n.up) + ' up · <span class="dot down"></span> ' +
        fmtInt(n.down) + " down of " + fmtInt(n.tested) + "</div>" + confBadge(c) + "</div>";
    });
    if (D.F) { // the moderated F (3+ conditions): features that change between any of the conditions
      let nf = 0, tf = 0;
      for (let i = 0; i < nF; i++) if (D.F.q[i] != null) { tf++; if (D.F.q[i] <= ST.alpha) nf++; }
      h += '<div class="tile" title="limma moderated F on every condition against ' + esc(D.F.ref) + ' (adjusted p; no fold-change cut-off)"><div class="k">Any change (F)</div><div class="v">' +
        fmtInt(nf) + '</div><div class="d">adj. p ≤ ' + esc(String(ST.alpha)) + " across " + D.F.conds.length + " conditions, of " + fmtInt(tf) + "</div></div>";
    }
    host.innerHTML = h;
    $$(".tile[data-ci]", host).forEach((t) => (t.onclick = () => { ST.ci = +t.dataset.ci; syncControls(); renderDiff(); goTo("differential"); }));
    $$(".tile[data-go]", host).forEach((t) => (t.onclick = () => { qcTab = "card"; renderQC(); goTo(t.dataset.go); }));
  }
  function geneLinks(list) {
    return list.map((i) => "<a href='#differential' class='gl' data-i='" + i + "'>" + esc(nameOf(i)) + "</a>").join(", ");
  }
  function renderFindings() {
    const host = $("#findings");
    if (!host) return;
    const items = [];
    D.comps.forEach((c, k) => {
      const n = counts(c);
      let s = "<b>" + esc(c.name) + "</b>: ";
      if (c.conf === "none") s += "fold change only (no replicates). ";
      s += fmtInt(n.up) + " up, " + fmtInt(n.down) + " down";
      if (c.pi0 != null && c.conf !== "none") {
        const moved = Math.max(0, 1 - c.pi0);
        s += " <span class='muted'>(from the p-values, roughly " + pct(moved) + " of " + esc(D.levelWord) + "s change in some way)</span>";
      }
      s += ".";
      const up = topHits(c, "up", 5), dn = topHits(c, "down", 5);
      if (up.length) s += " Strongest up: " + geneLinks(up) + ".";
      if (dn.length) s += " Down: " + geneLinks(dn) + ".";
      const oo = c.onoff || [];
      if (oo.length) {
        const t = oo.filter((x) => x[1] === "t").length, cc = oo.length - t;
        s += " <a href='#onoff' class='oo' data-ci='" + k + "'>" + (t ? t + " only in " + esc(c.t1) : "") + (t && cc ? ", " : "") + (cc ? cc + " only in " + esc(c.t2 === "others" ? "the others" : c.t2) : "") + "</a>.";
      }
      const gs = D.gsea.filter((b) => b.comparison === c.name).flatMap((b) => b.terms.filter((t) => t.q <= 0.05).map((t) => Object.assign({ lib: b.library }, t)));
      gs.sort((a, b) => a.p - b.p);
      if (gs.length) s += " Pathways: " + gs.slice(0, 3).map((t) => "<a href='#enrichment' class='gs' data-term='" + esc(t.term) + "'>" + (t.dir === "up" ? "↑ " : "↓ ") + esc(t.term) + "</a>").join(", ") + ".";
      const nimp = (() => { let x = 0; const f = impDriven(c); for (let i = 0; i < nF; i++) if (f[i] && rawSig(c, i)) x++; return x; })();
      if (nimp && !ST.opt.hideImp) s += " <span class='muted'>" + nimp + " hit(s) rest on imputed values.</span>";
      items.push(s);
    });
    const qc = [];
    const card = D.qc.scorecard;
    if (card && card.length) {
      const bad = card.filter((r) => r.status !== "ok");
      qc.push(bad.length ? "<b>Samples</b>: " + bad.map((r) => "<span class='pill " + r.status + "'>" + esc(r.sample) + "</span> " + esc(r.flags.join("; "))).join(" · ") : "<b>Samples</b>: all " + card.length + " pass the scorecard.");
    }
    const pcs = D.qc.pcs;
    if (pcs && pcs.pcs && pcs.pcs.length) {
      const p1 = pcs.pcs[0];
      if (pcs.batch) qc.push("<b>Batch?</b> PC" + pcs.batch.pc + " (" + fmt(pcs.batch.percent, 0) + "%) follows the replicate number (R² " + fmt(pcs.batch.r2_replicate) + ") more than the condition (R² " + fmt(pcs.batch.r2_condition) + ").");
      else if (p1.r2_condition != null) qc.push("<b>PCA</b>: PC1 (" + fmt(p1.percent, 0) + "%) " + (p1.r2_condition >= 0.5 ? "separates the conditions" : "is mostly not about condition") + " (R² " + fmt(p1.r2_condition) + ").");
    }
    const mn = D.qc.mnar;
    if (mn && mn.verdict) qc.push("<b>Missing values</b>: " + ({ intensity: "go missing mostly at low abundance (below detection), so low-value imputation fits.", random: "go missing at every abundance, so low-value imputation can create false fold changes.", mixed: "partly intensity-dependent.", few: "hardly any." })[mn.verdict]);
    if (!items.length && !qc.length) { host.innerHTML = ""; return; }
    host.innerHTML = "<div class='card findings'><h3>Key findings</h3><ul>" + items.map((x) => "<li>" + x + "</li>").join("") + "</ul>" +
      (qc.length ? "<h3>Data quality</h3><ul>" + qc.map((x) => "<li>" + x + "</li>").join("") + "</ul>" : "") +
      "<p class='muted'>At the current cut-offs; everything below updates when you change them.</p></div>";
    $$("a.gl", host).forEach((a) => (a.onclick = (e) => { e.preventDefault(); setFocus(+a.dataset.i); goTo("differential"); }));
    $$("a.oo", host).forEach((a) => (a.onclick = () => { onoffCi = +a.dataset.ci; renderOnOff(); }));
    $$("a.gs", host).forEach((a) => (a.onclick = (e) => { e.preventDefault(); setSearch("term:" + a.dataset.term); goTo("differential"); }));
    addHelp($("h3", host), "report.findings");
  }

  // -------------------------------------------------------------- volcano
  function renderVolcano() {
    const host = $("#volcano");
    if (!host) return;
    const c = C();
    const W = widthOf(host), H = heightOf(Math.round(Math.min(560, Math.max(360, W * 0.62))));
    const L = 56, R = 18, T = 26, B = 44;
    const root = frame(host, W, H);
    const fco = c.conf === "none";  // no replicates: fold change against abundance (or rank), no p-values
    const ma = ST.mode === "ma" || fco;
    const pts = [];
    let xmax = 1, ymax = 1, amin = Infinity, amax = -Infinity;
    for (let i = 0; i < nF; i++) {
      const fc = c.fc[i], p = c.p[i];
      if (fc == null || (p == null && !fco)) continue;
      const y = ma ? fc : -Math.log10(Math.max(p, 1e-300));
      const x = ma ? c.a[i] : fc;
      if (x == null) continue;
      pts.push([i, x, y]);
      if (!ma) { xmax = Math.max(xmax, Math.abs(fc)); ymax = Math.max(ymax, y); }
      else { amin = Math.min(amin, x); amax = Math.max(amax, x); ymax = Math.max(ymax, Math.abs(fc)); }
    }
    let x0, x1, y0, y1;
    if (ma) { x0 = amin - 0.5; x1 = amax + 0.5; y0 = -ymax * 1.08; y1 = ymax * 1.08; }
    else { x0 = -xmax * 1.08; x1 = xmax * 1.08; y0 = 0; y1 = ymax * 1.06; }
    if (!isFinite(x0)) { x0 = -1; x1 = 1; }
    if (ST.zoom && ST.zoom.mode === ST.mode) { x0 = ST.zoom.x0; x1 = ST.zoom.x1; y0 = ST.zoom.y0; y1 = ST.zoom.y1; }
    const X = (v) => L + ((v - x0) / (x1 - x0)) * (W - L - R);
    const Y = (v) => H - B - ((v - y0) / (y1 - y0)) * (H - T - B);
    const g = svg("g", {}, root);
    axes(g, X, Y, niceTicks(x0, x1, 8), niceTicks(y0, y1, 6), L, R, T, B, W, H,
      ma ? (c.aRank ? (c.t2 ? "rank (sorted by log2 fold change)" : "rank (sorted by log2 H/L)") : "mean log2 abundance") : (D.kind === "ratio" ? "log2 H/L" : "log2 fold change"),
      ma ? "log2 fold change" : (ST.adj ? "−log10 p (line: adjusted p cut-off)" : "−log10 p"));
    const clip = svg("clipPath", { id: "vclip" }, svg("defs", {}, root));
    svg("rect", { x: L, y: T, width: W - L - R, height: H - T - B }, clip);
    const pg = svg("g", { "clip-path": "url(#vclip)" }, root);
    const thrP = pThreshold(c), muted = css("--muted");
    if (ST.opt.lines) {
      if (!ma) {
        [-ST.lfc, ST.lfc].forEach((v) => { if (ST.lfc > 0) svg("line", { x1: X(v), x2: X(v), y1: T, y2: H - B, stroke: muted, "stroke-dasharray": "4 4" }, pg); });
        if (thrP != null) svg("line", { x1: L, x2: W - R, y1: Y(-Math.log10(thrP)), y2: Y(-Math.log10(thrP)), stroke: muted, "stroke-dasharray": "4 4" }, pg);
      } else {
        [-ST.lfc, 0, ST.lfc].forEach((v) => svg("line", { x1: L, x2: W - R, y1: Y(v), y2: Y(v), stroke: muted, "stroke-dasharray": v ? "4 4" : null }, pg));
      }
    }
    const colUp = css("--up"), colDown = css("--down"), colNs = css("--ns"), surf = css("--surface"), selC = css("--sel");
    const sigs = new Map(), raws = new Map();
    pts.forEach((p) => { sigs.set(p[0], sigOf(c, p[0])); raws.set(p[0], rawSig(c, p[0])); });
    const order = pts.slice().sort((a, b) => (sigs.get(a[0]) ? 2 : raws.get(a[0]) ? 1 : 0) - (sigs.get(b[0]) ? 2 : raws.get(b[0]) ? 1 : 0));
    const screen = [], at = new Map(), k = ST.opt.pt;
    order.forEach(([i, x, y]) => {
      const s = sigs.get(i), r = raws.get(i), cx = X(x), cy = Y(y);
      screen.push([i, cx, cy]);
      at.set(i, [cx, cy]);
      if (cx < L - 5 || cx > W - R + 5 || cy < T - 5 || cy > H - B + 5) return;
      if (s) svg("circle", { cx: cx.toFixed(1), cy: cy.toFixed(1), r: 3.4 * k, fill: s === "up" ? colUp : colDown, stroke: surf, "stroke-width": 1 }, pg);
      else if (r) svg("circle", { cx: cx.toFixed(1), cy: cy.toFixed(1), r: 3 * k, fill: colNs, "fill-opacity": 0.8, stroke: r === "up" ? colUp : colDown, "stroke-width": 1.2 }, pg);
      else svg("circle", { cx: cx.toFixed(1), cy: cy.toFixed(1), r: 2.6 * k, fill: colNs, "fill-opacity": 0.7 }, pg);
    });
    // on/off features that were tested (with imputation): a triangle over the point
    if (ST.opt.onoff && c.onoff && c.onoff.length) {
      const tri = css("--c3");
      c.onoff.forEach((o) => {
        const p = at.get(o[0]);
        if (!p) return;
        const s = 5.5 * k;
        svg("path", { d: "M" + p[0] + " " + (p[1] - s) + "l" + s * 0.9 + " " + s * 1.55 + "h" + -s * 1.8 + "z", fill: "none", stroke: tri, "stroke-width": 1.4 }, pg);
      });
    }
    // highlight groups (saved lists), search / term highlight, pinned, focus
    ST.groups.forEach((gr, gi) => {
      if (!gr.on) return;
      const col = groupColor(gi);
      gr.idx.forEach((i) => { const p = at.get(i); if (p) svg("circle", { cx: p[0], cy: p[1], r: 4.8 * k, fill: col, "fill-opacity": 0.35, stroke: col, "stroke-width": 1.6 }, pg); });
    });
    if (anyMark()) screen.forEach((p) => { if (matches(p[0])) svg("circle", { cx: p[1], cy: p[2], r: 5.5 * k, fill: "none", stroke: selC, "stroke-width": 1.3 }, pg); });
    const ring = (i, color, r, w) => { const p = at.get(i); if (p) svg("circle", { cx: p[0], cy: p[1], r: r, fill: "none", stroke: color, "stroke-width": w }, pg); };
    ST.pinned.forEach((i) => ring(i, selC, 6.5 * k, 2));
    if (ST.focus != null) ring(ST.focus, css("--accent"), 8 * k, 2.5);
    // labels: pinned + focus + search matches (when few) + top hits, greedy non-overlap
    const want = [];
    const ranked = pts.filter((p) => sigs.get(p[0])).sort((a, b) => fco ? Math.abs(c.fc[b[0]]) - Math.abs(c.fc[a[0]]) : c.p[a[0]] - c.p[b[0]]).slice(0, ST.labels).map((p) => p[0]);
    let found = [];
    if (ST.opt.labelMatches && anyMark()) {
      found = screen.filter((p) => matches(p[0])).map((p) => p[0]);
      if (found.length > 40) found = found.sort((a, b) => (c.p[a] == null ? 2 : c.p[a]) - (c.p[b] == null ? 2 : c.p[b])).slice(0, 40);
    }
    ST.pinned.concat(ST.focus != null ? [ST.focus] : []).concat(found).concat(ranked).forEach((i) => { if (!want.includes(i)) want.push(i); });
    placeLabels(pg, want.map((i) => [i].concat(at.get(i) || [])).filter((x) => x.length === 3), [L, W - R, T, H - B], ST.opt.lab,
      (i) => ST.pinned.includes(i) || i === ST.focus || matches(i));
    // corner names (FragPipe-Analyst draws the two conditions)
    if (!ma && c.t2 && D.kind !== "ratio") {
      text(root, W - R - 4, H - B - 8, c.t1 + " ↑", { "text-anchor": "end", fill: colUp, "font-size": 12.5, "font-weight": 600 });
      text(root, L + 6, H - B - 8, "↑ " + (c.t2 === "others" ? "others" : c.t2), { fill: colDown, "font-size": 12.5, "font-weight": 600 });
    }
    // interaction: hover nearest, click focus, drag zoom or select, dblclick reset
    const hit = svg("rect", { x: L, y: T, width: W - L - R, height: H - T - B, fill: "transparent", style: "cursor:crosshair" }, root);
    const band = svg("rect", { fill: css("--accent"), "fill-opacity": 0.08, stroke: css("--accent"), "stroke-dasharray": "3 3", visibility: "hidden" }, root);
    const toLocal = (e) => { const r = root.getBoundingClientRect(); return [(e.clientX - r.left) * (W / r.width), (e.clientY - r.top) * (H / r.height)]; };
    const nearest = (mx, my) => {
      let best = null, bd = 64;
      for (const p of screen) { const d = (p[1] - mx) ** 2 + (p[2] - my) ** 2; if (d < bd) { bd = d; best = p[0]; } }
      return best;
    };
    let drag = null;
    hit.addEventListener("mousedown", (e) => { drag = toLocal(e); e.preventDefault(); });
    hit.addEventListener("mousemove", (e) => {
      const [mx, my] = toLocal(e);
      if (drag && (Math.abs(mx - drag[0]) > 4 || Math.abs(my - drag[1]) > 4)) {
        band.setAttribute("visibility", "visible");
        band.setAttribute("x", Math.min(mx, drag[0])); band.setAttribute("y", Math.min(my, drag[1]));
        band.setAttribute("width", Math.abs(mx - drag[0])); band.setAttribute("height", Math.abs(my - drag[1]));
        hideTip();
        return;
      }
      const i = nearest(mx, my);
      if (i == null) { hideTip(); hit.style.cursor = "crosshair"; return; }
      hit.style.cursor = "pointer";
      showTip(e, featureTip(i));
    });
    hit.addEventListener("mouseleave", () => { hideTip(); drag = null; band.setAttribute("visibility", "hidden"); });
    hit.addEventListener("mouseup", (e) => {
      const [mx, my] = toLocal(e);
      if (drag && (Math.abs(mx - drag[0]) > 8 && Math.abs(my - drag[1]) > 8)) {
        const xa = Math.min(mx, drag[0]), xb = Math.max(mx, drag[0]), ya = Math.min(my, drag[1]), yb = Math.max(my, drag[1]);
        drag = null;
        if (ST.drag === "select" || e.altKey) {
          const set = new Set(screen.filter((p) => p[1] >= xa && p[1] <= xb && p[2] >= ya && p[2] <= yb).map((p) => p[0]));
          setHighlight(set, "box selection");
          return;
        }
        const inv = (px, lo, hi, a, b) => lo + ((px - a) / (b - a)) * (hi - lo);
        ST.zoom = { mode: ST.mode, x0: inv(xa, x0, x1, L, W - R), x1: inv(xb, x0, x1, L, W - R),
          y0: y0 + ((H - B - yb) / (H - T - B)) * (y1 - y0), y1: y0 + ((H - B - ya) / (H - T - B)) * (y1 - y0) };
        renderVolcano();
        return;
      }
      drag = null;
      band.setAttribute("visibility", "hidden");
      const i = nearest(mx, my);
      if (i != null) { if (e.shiftKey) togglePin(i); setFocus(i); }
    });
    hit.addEventListener("dblclick", () => { ST.zoom = null; renderVolcano(); });
    const tools = svgTools(host, root, "volcano_" + c.slug + (ma ? "_MA" : ""));
    if (ST.zoom) {
      const z = document.createElement("button");
      z.textContent = "Reset zoom";
      z.onclick = () => { ST.zoom = null; renderVolcano(); };
      tools.insertBefore(z, tools.firstChild);
    }
    const n = counts(c);
    $("#vcount").innerHTML = (c.conf ? "<div class='notes' style='margin:0 0 8px'><b>" + (fco ? "Fold change only" : "Low confidence") +
      ":</b> " + esc(c.confNote || "") + "</div>" : "") + '<span class="dot up"></span> <b>' + fmtInt(n.up) + '</b> up &nbsp; <span class="dot down"></span> <b>' +
      fmtInt(n.down) + "</b> down &nbsp;<span class='muted'>of " + fmtInt(n.tested) + (fco ? " ranked by fold change" : " tested") +
      (n.filtered ? " · " + fmtInt(n.filtered) + " filtered out (grey with a ring)" : "") +
      " · drag to " + (ST.drag === "select" ? "select" : "zoom (alt-drag selects)") + ", double-click to reset, shift-click to pin</span>";
    renderGroupLegend();
  }
  /** Greedy non-overlapping labels: each [index, x, y] tries eight spots around its point (the far ones with a
   * leader line), in priority order; a label that fits nowhere is skipped. */
  function placeLabels(g, items, bounds, fs, bold) {
    const boxes = [], muted = css("--muted"), ink = css("--text");
    items.forEach(([i, px, py]) => {
      const s = nameOf(i).slice(0, 22), w = s.length * fs * 0.575 + 4;
      const cands = [[px + 6, py - 6], [px - 6 - w, py - 6], [px + 6, py + fs + 2], [px - 6 - w, py + fs + 2],
        [px + 12, py - 16], [px - 12 - w, py - 16], [px + 12, py + fs + 12], [px - 12 - w, py + fs + 12]];
      for (let ci = 0; ci < cands.length; ci++) {
        const [bx, by] = cands[ci];
        const box = [bx, by - fs + 0.5, bx + w, by + 2];
        if (box[0] < bounds[0] || box[2] > bounds[1] || box[1] < bounds[2] || box[3] > bounds[3]) continue;
        if (boxes.some((o) => !(box[2] < o[0] || box[0] > o[2] || box[3] < o[1] || box[1] > o[3]))) continue;
        boxes.push(box);
        if (ci >= 4) svg("line", { x1: px, y1: py, x2: bx < px ? bx + w : bx, y2: by - fs / 2, stroke: muted, "stroke-width": 0.8 }, g);
        text(g, bx, by, s, { fill: ink, "font-size": fs, "font-weight": bold && bold(i) ? 650 : 400 });
        break;
      }
    });
  }
  function flagsOf(c, i) {
    const out = [];
    if (impDriven(c)[i]) out.push(["imp", "imputed", "At least half of a group's values are imputed"]);
    if (D.f.pep && D.f.pep[i] != null && D.f.pep[i] <= 1) out.push(["pep", "1 " + (D.evidence === "PSMs" ? "PSM" : "pep"), "Identified by a single " + (D.evidence === "PSMs" ? "PSM" : "peptide")]);
    const oo = onoffOf(c).get(i);
    if (oo) out.push(["oo", "only " + (oo[1] === "t" ? c.t1 : c.t2 === "others" ? "others" : c.t2), "Measured in " + oo[2] + " of " + oo[3] + " of one group, never in the other"]);
    return out;
  }
  const badges = (c, i) => flagsOf(c, i).map((f) => "<span class='badge " + f[0] + "' title='" + esc(f[2]) + "'>" + esc(f[1]) + "</span>").join("");
  function featureTip(i) {
    const c = C();
    return "<b>" + esc(nameOf(i)) + "</b> <span class='muted'>" + esc(D.f.id[i]) + "</span><br>log2FC " + fmt(c.fc[i]) +
      " · p " + fmtP(c.p[i]) + " · adj. p " + fmtP(c.q[i]) + (D.f.pep && D.f.pep[i] != null ? " · " + D.f.pep[i] + " " + esc(D.evidence) : "") +
      (flagsOf(c, i).length ? "<br>" + badges(c, i) : "") + (D.f.desc[i] ? "<br><span class='muted'>" + esc(D.f.desc[i].slice(0, 90)) + "</span>" : "");
  }

  // ------------------------------------------------------ search + groups
  function setSearch(s) {
    const box = $("#search");
    if (box) box.value = s;
    ST.search = s.trim();
    ST.q = parseQuery(ST.search);
    ST.highlight = null;
    syncControls();
    renderSearchInfo();
    renderVolcano(); renderTable(true); renderCompare(); safe(renderHeatmap, "#heatmap"); safe(doseOnSearch, "#dosebody"); safe(renderCys, "#cysbody"); safe(timeOnSearch, "#timebody");
    writeHash();
  }
  function setHighlight(set, name) {
    ST.highlight = set && set.size ? set : null;
    ST.highlightName = name || "";
    syncControls();
    renderSearchInfo();
    renderVolcano(); renderTable(true); renderCompare(); safe(renderHeatmap, "#heatmap"); safe(doseOnSearch, "#dosebody"); safe(renderCys, "#cysbody"); safe(timeOnSearch, "#timebody");
  }
  function markedList() {
    const out = [];
    for (let i = 0; i < nF; i++) if (matches(i)) out.push(i);
    return out;
  }
  function renderSearchInfo() {
    const host = $("#searchinfo");
    if (!host) return;
    const q = ST.q;
    if (!anyMark()) { host.innerHTML = ""; return; }
    const list = markedList();
    let h = "";
    if (q && q.error && !ST.highlight) h += "<span class='warnink'>" + esc(q.error) + "</span> ";
    else {
      h += "<b>" + fmtInt(list.length) + "</b> match" + (list.length === 1 ? "" : "es");
      if (!ST.highlight && q) {
        if (q.label) h += " in <i>" + esc(q.label) + "</i>";
        if (q.found.length + q.missing.length > 1) h += " · found " + q.found.length + " of " + (q.found.length + q.missing.length);
        if (q.missing.length) h += " · not found: <span class='warnink'>" + esc(q.missing.slice(0, 12).join(", ")) + (q.missing.length > 12 ? "…" : "") + "</span>";
      }
      const c = C();
      const sig = list.filter((i) => sigOf(c, i));
      if (list.length) h += " · " + sig.length + " significant here";
      h += " <button id='sq-copy'>Copy names</button> <button id='sq-group'>Save as group</button> <button id='sq-pin'>Pin all</button>";
    }
    h += " <button id='sq-clear'>Clear</button>";
    host.innerHTML = h;
    const cp = $("#sq-copy"); if (cp) cp.onclick = () => copyText(list.map(nameOf).join("\n"), () => flash($("#copied"), "copied " + list.length));
    const sg = $("#sq-group"); if (sg) sg.onclick = () => { openOptions(true); $("#gname").value = ST.highlight ? ST.highlightName : (q && q.label) || ST.search.slice(0, 40); $("#ggenes").value = list.map(nameOf).join("\n"); $("#gname").focus(); };
    const pa = $("#sq-pin"); if (pa) pa.onclick = () => { list.slice(0, 12).forEach((i) => { if (!ST.pinned.includes(i)) ST.pinned.push(i); }); renderPins(); renderVolcano(); };
    $("#sq-clear").onclick = () => { ST.highlight = null; setSearch(""); };
  }
  // suggestions for the word being typed (the last one in a list)
  let SUG = [], sugAt = -1;
  function suggest() {
    const box = $("#search"), host = $("#suggest");
    if (!box || !host) return;
    const val = box.value, m = /([^\s,;]*)$/.exec(val), word = (m ? m[1] : "").replace(/^(term|set|go|pathway):/i, "");
    const isTerm = /^(term|set|go|pathway):/i.test(val.trim());
    const w = (isTerm ? val.trim().replace(/^(term|set|go|pathway):\s*/i, "") : word).toLowerCase();
    SUG = [];
    if (w.length >= 1 && !/^\//.test(val.trim()) && !/[*?]/.test(word)) {
      const c = C(), pre = [], sub = [], des = [];
      if (!isTerm) {
        keyIndex();
        for (let i = 0; i < nF && pre.length < 400; i++) {
          const lab = (D.f.label[i] || "").toLowerCase(), id = (D.f.id[i] || "").toLowerCase();
          if (lab.startsWith(w) || FKEYS[i].some((k) => k.toLowerCase().startsWith(w))) pre.push(i);
          else if (lab.includes(w) || id.includes(w)) sub.push(i);
          else if (w.length >= 3 && (D.f.desc[i] || "").toLowerCase().includes(w)) des.push(i);
        }
        const byP = (a, b) => (c && c.p ? ((c.p[a] == null ? 2 : c.p[a]) - (c.p[b] == null ? 2 : c.p[b])) : 0);
        const exact = pre.filter((i) => (D.f.label[i] || "").toLowerCase() === w);
        pre.sort((a, b) => (D.f.label[a] || "").length - (D.f.label[b] || "").length || byP(a, b));
        const seen = new Set();
        exact.concat(pre, sub.sort(byP), des.sort(byP)).forEach((i) => { if (!seen.has(i) && SUG.length < 9) { seen.add(i); SUG.push({ i: i }); } });
      }
      if (w.length >= 3) {
        let n = 0;
        gsets().forEach((e) => { if (n < (isTerm ? 12 : 3) && e.term.toLowerCase().includes(w)) { SUG.push({ term: e.term, n: e.genes.size, src: e.src }); n++; } });
      }
    }
    sugAt = -1;
    if (!SUG.length || document.activeElement !== box) { host.hidden = true; return; }
    const c = C();
    host.innerHTML = SUG.map((s, k) => s.term != null
      ? "<div class='opt' role='option' data-k='" + k + "'><span class='badge term'>term</span> " + esc(s.term) + " <span class='muted'>" + s.n + " genes · " + esc(s.src) + "</span></div>"
      : "<div class='opt' role='option' data-k='" + k + "'>" + (c && sigOf(c, s.i) ? "<span class='dot " + sigOf(c, s.i) + "'></span> " : "") + "<b>" + esc(nameOf(s.i)) + "</b> <span class='muted'>" + esc((D.f.id[s.i] || "").slice(0, 28)) +
        (c ? " · log2FC " + fmt(c.fc[s.i]) + " · p " + fmtP(c.p[s.i]) : "") + "</span><div class='muted sd'>" + esc((D.f.desc[s.i] || "").slice(0, 70)) + "</div></div>").join("");
    host.hidden = false;
    $$(".opt", host).forEach((el) => (el.onmousedown = (e) => { e.preventDefault(); pick(+el.dataset.k); }));
  }
  function pick(k) {
    const s = SUG[k], box = $("#search"), host = $("#suggest");
    if (!s || !box) return;
    host.hidden = true;
    if (s.term != null) { setSearch("term:" + s.term); return; }
    const val = box.value, head = val.replace(/[^\s,;]*$/, "");
    const lab = D.f.label[s.i] || D.f.id[s.i];
    const next = head ? head + lab : lab;
    setSearch(next);
    if (!head) setFocus(s.i);
  }
  function moveSug(d) {
    const host = $("#suggest");
    if (!SUG.length || host.hidden) return false;
    sugAt = (sugAt + d + SUG.length) % SUG.length;
    $$(".opt", host).forEach((el, k) => el.classList.toggle("on", k === sugAt));
    return true;
  }
  const GROUP_KEY = "ionomos.groups.v1";
  function groupColor(k) { return css("--c" + ((k + 2) % 8)); }
  function loadGroups() {
    const saved = store.get(GROUP_KEY, []);
    ST.groups = (Array.isArray(saved) ? saved : []).filter((g) => g && g.name && Array.isArray(g.genes)).map((g) => ({ name: String(g.name), genes: g.genes.map(String), on: g.on !== false, idx: new Set() }));
    ST.groups.forEach((g) => (g.idx = indicesOfGenes(g.genes)));
  }
  function saveGroups() { store.set(GROUP_KEY, ST.groups.map((g) => ({ name: g.name, genes: g.genes, on: g.on }))); }
  function addGroup(name, genes) {
    genes = Array.from(new Set(genes.map((x) => x.trim()).filter(Boolean)));
    if (!genes.length) return;
    name = (name || "").trim() || "Group " + (ST.groups.length + 1);
    const old = ST.groups.findIndex((g) => g.name === name);
    const g = { name: name, genes: genes, on: true, idx: indicesOfGenes(genes) };
    if (old >= 0) ST.groups[old] = g; else ST.groups.push(g);
    saveGroups();
    renderGroups(); renderVolcano(); renderCompare();
  }
  function renderGroups() {
    const host = $("#groups");
    if (!host) return;
    host.innerHTML = ST.groups.length ? ST.groups.map((g, k) => "<div class='grow'><label><input type='checkbox' data-k='" + k + "'" + (g.on ? " checked" : "") + "> <span class='sw' style='background:" + groupColor(k) + "'></span> " + esc(g.name) +
      " <span class='muted'>" + g.idx.size + " here / " + g.genes.length + "</span></label> <button data-show='" + k + "' title='Search for this group'>find</button> <button data-del='" + k + "' title='Delete'>✕</button></div>").join("") : "<p class='muted'>No groups yet: search, then “Save as group”, or type a list below.</p>";
    $$("input[data-k]", host).forEach((b) => (b.onchange = () => { ST.groups[+b.dataset.k].on = b.checked; saveGroups(); renderVolcano(); renderCompare(); }));
    $$("button[data-del]", host).forEach((b) => (b.onclick = () => { ST.groups.splice(+b.dataset.del, 1); saveGroups(); renderGroups(); renderVolcano(); renderCompare(); }));
    $$("button[data-show]", host).forEach((b) => (b.onclick = () => { const g = ST.groups[+b.dataset.show]; setHighlight(g.idx, g.name); }));
  }
  function renderGroupLegend() {
    const host = $("#glegend");
    if (!host) return;
    const on = ST.groups.map((g, k) => [g, k]).filter(([g]) => g.on && g.idx.size);
    const hasOO = ST.opt.onoff && C() && (C().onoff || []).length;
    host.innerHTML = on.map(([g, k]) => "<span><span class='sw' style='background:" + groupColor(k) + "'></span>" + esc(g.name) + " (" + g.idx.size + ")</span>").join("") +
      (hasOO ? "<span><span style='color:" + css("--c3") + "'>△</span> only in one condition (tested with imputed values)</span>" : "");
  }
  function exportGroups() {
    const lines = ST.groups.map((g) => [g.name, "Ionomos highlight group"].concat(g.genes).join("\t"));
    download("ionomos_groups.gmt", lines.join("\n") + "\n", "text/plain");
  }
  function importGroups(file) {
    const fr = new FileReader();
    fr.onload = () => {
      const txt = String(fr.result || "");
      const rows = txt.split(/\r?\n/).filter((l) => l.trim());
      const gmt = rows.filter((l) => l.split("\t").length >= 3);
      if (gmt.length) gmt.forEach((l) => { const p = l.split("\t"); addGroup(p[0], p.slice(2)); });
      else addGroup(file.name.replace(/\.[^.]+$/, ""), txt.split(/[\s,;]+/));
    };
    fr.readAsText(file);
  }
  function openOptions(show) {
    const p = $("#optpanel"), b = $("#opts");
    if (!p) return;
    p.hidden = show == null ? !p.hidden : !show;
    if (b) { b.className = p.hidden ? "" : "on"; b.setAttribute("aria-expanded", String(!p.hidden)); }
  }

  // --------------------------------------------------------------- detail
  function isImputed(i, j) { return D.imp && D.imp[i] && D.imp[i][j] === "1"; }
  function setFocus(i) { ST.focus = i; renderVolcano(); renderDetail(); renderTable(false); writeHash(); }
  function togglePin(i) {
    const k = ST.pinned.indexOf(i);
    if (k >= 0) ST.pinned.splice(k, 1); else ST.pinned.push(i);
    renderPins();
  }
  function renderPins() {
    const host = $("#pins");
    if (!host) return;
    host.innerHTML = ST.pinned.length ? ST.pinned.map((i) => '<span class="chip' + (i === ST.focus ? " on" : "") + '" data-i="' + i + '">' +
      esc(nameOf(i)) + " ✕</span>").join("") + ' <button id="pinprof">Profile of pinned</button> <button id="pinclear">Unpin all</button>' : "";
    $$(".chip", host).forEach((ch) => (ch.onclick = (e) => { const i = +ch.dataset.i; if (e.target === ch && e.offsetX > ch.offsetWidth - 18) { togglePin(i); renderVolcano(); } else setFocus(i); }));
    const pp = $("#pinprof");
    if (pp) pp.onclick = () => renderProfile();
    const pc = $("#pinclear");
    if (pc) pc.onclick = () => { ST.pinned = []; renderPins(); renderVolcano(); };
  }
  /** Features whose values across samples go up and down with this one (Pearson, pairwise-complete). */
  function similar(i, k) {
    const a = D.v[i], need = Math.max(4, Math.ceil(nS * 0.6)), out = [];
    if (!a || nS < 4) return out;
    for (let j = 0; j < nF; j++) {
      if (j === i) continue;
      const b = D.v[j];
      let n = 0;
      for (let s = 0; s < nS; s++) if (a[s] != null && b[s] != null) n++;
      if (n < need) continue;
      const r = pearson(a, b);
      if (r != null && r > 0.6) out.push([j, r]);
    }
    out.sort((x, y) => y[1] - x[1]);
    return out.slice(0, k || 8);
  }
  function renderDetail() {
    const host = $("#detail");
    if (!host) return;
    const i = ST.focus;
    if (i == null) { host.innerHTML = '<div class="empty">Click a point or a table row to see that ' + esc(D.levelWord) + " here.<br>Shift-click to pin several.</div>"; return; }
    const c = C();
    let h = "<h4>" + esc(nameOf(i)) + '</h4><div class="id">' + esc(D.f.id[i]) + '</div><div class="desc">' + esc(D.f.desc[i] || "") + "</div>";
    if (c) h += "<div class='chips'>" + badges(c, i) + (D.f.pep && D.f.pep[i] != null ? "<span class='badge'>" + D.f.pep[i] + " " + esc(D.evidence) + "</span>" : "") + "</div>";
    h += '<div id="strip" class="chart"></div>';
    h += "<table><thead><tr><th>Comparison</th><th>log2FC</th><th>95% CI</th><th>adj. p</th></tr></thead><tbody>";
    D.comps.forEach((cc) => {
      const s = sigOf(cc, i);
      h += "<tr><td>" + (s ? '<span class="dot ' + s + '"></span> ' : "") + esc(cc.name) + '</td><td class="n">' + fmt(cc.fc[i]) + '</td><td class="n">' +
        (cc.ciL[i] == null ? "–" : fmt(cc.ciL[i]) + " – " + fmt(cc.ciR[i])) + '</td><td class="n">' + fmtP(cc.q[i]) + "</td></tr>";
    });
    h += "</tbody></table>";
    const sim = similar(i, 8);
    if (sim.length) h += "<div class='sim'><b>Behaves like</b> <span class='muted'>(correlation across samples)</span><div class='chips'>" + sim.map(([j, r]) => "<span class='chip' data-j='" + j + "' title='r = " + fmt(r, 3) + "'>" + esc(nameOf(j)) + " <span class='muted'>" + fmt(r, 2) + "</span></span>").join("") + "</div></div>";
    const g = geneOf(i), acc = (FKEYS && FKEYS[i] || []).find((k) => /^[OPQ][0-9][A-Z0-9]{3}[0-9]$|^[A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2}$/.test(k));
    h += '<div class="chips"><button id="pinbtn">' + (ST.pinned.includes(i) ? "Unpin" : "Pin") + '</button><button id="copybtn">Copy values</button>' +
      (sim.length ? "<button id='simbtn'>Mark similar</button>" : "") + "</div>";
    if (g || acc) h += "<div class='muted ext'>Look up (opens the web): " + (acc ? "<a target='_blank' rel='noopener' href='https://www.uniprot.org/uniprotkb/" + encodeURIComponent(acc) + "'>UniProt</a> · " : "") +
      (g ? "<a target='_blank' rel='noopener' href='https://string-db.org/cgi/network?identifiers=" + encodeURIComponent(g) + "'>STRING</a> · <a target='_blank' rel='noopener' href='https://www.genecards.org/cgi-bin/carddisp.pl?gene=" + encodeURIComponent(g) + "'>GeneCards</a>" : "") + "</div>";
    host.innerHTML = h;
    $("#pinbtn").onclick = () => { togglePin(i); renderDetail(); renderVolcano(); };
    $("#copybtn").onclick = () => {
      const rows = ["sample\tcondition\tlog2\timputed"].concat(D.samples.map((s, j) => s + "\t" + D.cond[j] + "\t" + (D.v[i][j] == null ? "" : D.v[i][j]) + "\t" + (isImputed(i, j) ? "yes" : "")));
      copyText(rows.join("\n"));
    };
    $$(".sim .chip", host).forEach((ch) => (ch.onclick = () => setFocus(+ch.dataset.j)));
    const sb = $("#simbtn");
    if (sb) sb.onclick = () => setHighlight(new Set([i].concat(sim.map((x) => x[0]))), "behaves like " + nameOf(i));
    renderStrip($("#strip"), [i]);
  }
  function renderStrip(host, feats) {
    const W = widthOf(host, 300), H = heightOf(230), L = 44, R = 10, T = 26, B = 50;
    const root = frame(host, W, H);
    let lo = Infinity, hi = -Infinity;
    feats.forEach((i) => D.v[i].forEach((v) => { if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }));
    if (!isFinite(lo)) return;
    const pad = (hi - lo) * 0.1 || 1;
    lo -= pad; hi += pad;
    const groups = D.conditions;
    const bw = (W - L - R) / groups.length;
    const Y = (v) => H - B - ((v - lo) / (hi - lo)) * (H - T - B);
    const g = svg("g", {}, root);
    axes(g, () => 0, Y, [], niceTicks(lo, hi, 4), L, R, T, B, W, H, "", D.kind === "ratio" ? "log2 H/L" : "log2");
    const surf = css("--surface");
    groups.forEach((cnd, k) => {
      const cx = L + bw * (k + 0.5);
      title(text(g, cx, H - B + 16, cnd.length > 14 ? cnd.slice(0, 13) + "…" : cnd, { "text-anchor": "middle" }), cnd);
      feats.forEach((i, fi) => {
        const off = feats.length > 1 ? (fi - (feats.length - 1) / 2) * Math.min(18, bw / (feats.length + 1)) : 0;
        const vals = [];
        D.samples.forEach((s, j) => {
          if (D.cond[j] !== cnd || D.v[i][j] == null) return;
          const v = D.v[i][j], imp = isImputed(i, j);
          vals.push(v);
          const jit = ((j * 7919) % 13 - 6) * (feats.length > 1 ? 0.6 : 1.6);
          const col = feats.length > 1 ? css("--c" + (fi % 8)) : condColor(cnd);
          title(svg("circle", { cx: cx + off + jit, cy: Y(v), r: 4.2, fill: imp ? surf : col, stroke: col, "stroke-width": 1.6 }, g), s + ": " + fmt(v) + (imp ? " (imputed)" : ""));
        });
        if (vals.length) {
          const m = vals.reduce((a, b) => a + b, 0) / vals.length;
          svg("line", { x1: cx + off - 12, x2: cx + off + 12, y1: Y(m), y2: Y(m), stroke: css("--text"), "stroke-width": 2 }, g);
        }
      });
    });
    const hasImp = feats.some((i) => D.samples.some((_, j) => isImputed(i, j)));
    if (hasImp) text(g, L + 4, H - 4, "○ imputed   ● measured", { "font-size": 11 });
    svgTools(host, root, "values_" + feats.map(nameOf).join("_").slice(0, 60), () => renderStrip(host, feats));
  }
  function renderProfile() {
    const host = $("#detail");
    if (!ST.pinned.length) return;
    host.innerHTML = "<h4>Pinned (" + ST.pinned.length + ")</h4><div class='legend'>" + ST.pinned.slice(0, 8).map((i, k) => "<span><span class='sw' style='background:" +
      css("--c" + (k % 8)) + "'></span>" + esc(nameOf(i)) + "</span>").join("") + "</div><div id='strip' class='chart'></div>";
    renderStrip($("#strip"), ST.pinned.slice(0, 8));
  }

  // ---------------------------------------------------------------- table
  const COLS = [
    { k: "sig", t: "", f: (c, i) => { const s = sigOf(c, i); return s ? '<span class="dot ' + s + '"></span>' : ""; }, v: (c, i) => (sigOf(c, i) ? 0 : 1) },
    { k: "label", t: D.kind === "ratio" ? "Site" : "Gene", f: (c, i) => esc(D.f.label[i] || "") + " " + badges(c, i), v: (c, i) => (D.f.label[i] || "").toLowerCase() },
    { k: "id", t: "ID", f: (c, i) => esc((D.f.id[i] || "").slice(0, 40)), v: (c, i) => D.f.id[i] },
    { k: "fc", t: "log2FC", n: 1, f: (c, i) => fmt(c.fc[i]), v: (c, i) => (c.fc[i] == null ? -1e9 : c.fc[i]) },
    { k: "ci", t: "95% CI", n: 1, f: (c, i) => (c.ciL[i] == null ? "–" : fmt(c.ciL[i]) + " – " + fmt(c.ciR[i])), v: (c, i) => c.ciL[i] },
    { k: "p", t: "p", n: 1, f: (c, i) => fmtP(c.p[i]), v: (c, i) => (c.p[i] == null ? 2 : c.p[i]) },
    { k: "q", t: "adj. p", n: 1, f: (c, i) => fmtP(c.q[i]), v: (c, i) => (c.q[i] == null ? 2 : c.q[i]) },
    { k: "n", t: "measured", n: 1, f: (c, i) => (c.nt[i] == null ? "" : c.nt[i] + (c.nc[i] != null && c.t2 ? " / " + c.nc[i] : "")), v: (c, i) => (c.nt[i] || 0) + (c.nc[i] || 0) },
  ].concat(D.F ? [{ k: "F", t: "any change (F) adj. p", n: 1, f: (c, i) => fmtP(D.F.q[i]), v: (c, i) => (D.F.q[i] == null ? 2 : D.F.q[i]) }] : []).concat(D.f.pep ? [{ k: "pep", t: D.evidence || "peptides", n: 1, f: (c, i) => (D.f.pep[i] == null ? "" : String(D.f.pep[i])), v: (c, i) => (D.f.pep[i] == null ? -1 : D.f.pep[i]) }] : []).concat([
    { k: "desc", t: "Description", f: (c, i) => esc((D.f.desc[i] || "").slice(0, 120)), v: (c, i) => D.f.desc[i] || "", cls: "desc" },
  ]);
  function tableRows() {
    const c = C();
    const out = [];
    const mark = anyMark();
    for (let i = 0; i < nF; i++) {
      if (c.p[i] == null && c.fc[i] == null && !(mark && matches(i))) continue;
      if (ST.sigOnly && !sigOf(c, i) && !mark) continue;
      if (mark && !matches(i)) continue;
      out.push(i);
    }
    const col = COLS.find((x) => x.k === ST.sortKey) || COLS[5];
    out.sort((a, b) => { const x = col.v(c, a), y = col.v(c, b); return (x > y ? 1 : x < y ? -1 : 0) * ST.sortDir; });
    return out;
  }
  function renderTable(resetPage) {
    const host = $("#table");
    if (!host || !C()) return;
    if (ST.rows === "proteins") { renderProteinTable(host); return; }
    if (resetPage) ST.page = 0;
    const c = C(), rows = tableRows(), per = 50;
    const pages = Math.max(1, Math.ceil(rows.length / per));
    ST.page = Math.min(ST.page, pages - 1);
    let h = '<div class="tablewrap"><table><thead><tr>' + COLS.map((x) => "<th data-k='" + x.k + "'" + (x.k === ST.sortKey ? " data-dir='" + (ST.sortDir > 0 ? "asc" : "desc") + "'" : "") + ">" + esc(x.t) + "</th>").join("") + "</tr></thead><tbody>";
    rows.slice(ST.page * per, ST.page * per + per).forEach((i) => {
      h += "<tr data-i='" + i + "'" + (i === ST.focus ? " class='focus'" : "") + ">" + COLS.map((x) => "<td" + (x.n ? " class='n'" : x.cls ? " class='" + x.cls + "'" : "") + ">" + x.f(c, i) + "</td>").join("") + "</tr>";
    });
    if (!rows.length) h += "<tr><td colspan='" + COLS.length + "' class='muted'>" + (anyMark() ? "Nothing matches." : ST.sigOnly ? "No significant features at these cut-offs. Untick “significant only” to see everything." : "Nothing matches.") + "</td></tr>";
    h += "</tbody></table></div><div class='pager'>" + fmtInt(rows.length) + " rows · page " + (ST.page + 1) + " of " + pages +
      " <button id='pprev'>‹</button><button id='pnext'>›</button></div>";
    host.innerHTML = h;
    $$("th", host).forEach((th) => (th.onclick = () => { if (ST.sortKey === th.dataset.k) ST.sortDir *= -1; else { ST.sortKey = th.dataset.k; ST.sortDir = 1; } renderTable(true); }));
    $$("tbody tr[data-i]", host).forEach((tr) => (tr.onclick = (e) => { const i = +tr.dataset.i; if (e.shiftKey) togglePin(i); setFocus(i); }));
    $("#pprev").onclick = () => { ST.page = Math.max(0, ST.page - 1); renderTable(false); };
    $("#pnext").onclick = () => { ST.page = Math.min(pages - 1, ST.page + 1); renderTable(false); };
  }
  /** Site data: one row per protein, how many of its sites move. Most sites moving together hints at a
   * protein-level change (expression, degradation) rather than a site-specific one. */
  function proteinRows() {
    const c = C(), by = new Map();
    for (let i = 0; i < nF; i++) {
      if (c.fc[i] == null) continue;
      const pid = (D.f.id[i] || "").split("|").slice(0, -1).join("|") || D.f.id[i];
      let e = by.get(pid);
      if (!e) by.set(pid, (e = { pid: pid, gene: geneOf(i), idx: [], up: 0, down: 0, fcs: [] }));
      e.idx.push(i); e.fcs.push(c.fc[i]);
      const s = sigOf(c, i);
      if (s === "up") e.up++; else if (s === "down") e.down++;
    }
    return Array.from(by.values()).map((e) => Object.assign(e, { med: median(e.fcs), whole: e.idx.length >= 3 && (e.up + e.down) / e.idx.length >= 0.5 }));
  }
  function renderProteinTable(host) {
    const rows = proteinRows().filter((e) => !ST.sigOnly || e.up + e.down).sort((a, b) => (b.up + b.down) - (a.up + a.down) || Math.abs(b.med) - Math.abs(a.med));
    let h = "<div class='tablewrap'><table><thead><tr><th>Gene</th><th>Protein</th><th>sites</th><th>up</th><th>down</th><th>median log2</th><th>pattern</th></tr></thead><tbody>";
    rows.slice(0, 500).forEach((e, k) => {
      h += "<tr data-k='" + k + "'><td>" + esc(e.gene) + "</td><td>" + esc(e.pid.slice(0, 40)) + "</td><td class='n'>" + e.idx.length + "</td><td class='n'>" + e.up + "</td><td class='n'>" + e.down +
        "</td><td class='n'>" + fmt(e.med) + "</td><td>" + (e.whole ? "<span class='badge imp' title='Most quantified sites of this protein change: possibly the protein amount, not the site'>most sites move</span>" : e.up + e.down ? "site-specific" : "") + "</td></tr>";
    });
    if (!rows.length) h += "<tr><td colspan='7' class='muted'>No protein has a significant site at these cut-offs.</td></tr>";
    host.innerHTML = h + "</tbody></table></div><div class='pager'>" + fmtInt(rows.length) + " proteins" + (rows.length > 500 ? " (first 500 shown)" : "") + " · click one to mark its sites</div>";
    $$("tbody tr[data-k]", host).forEach((tr) => (tr.onclick = () => { const e = rows[+tr.dataset.k]; setHighlight(new Set(e.idx), e.gene + " sites"); }));
  }
  function exportCSV() {
    const c = C();
    download(c.slug + "_filtered.csv", resultsCsv(c, tableRows()), "text/csv");
  }
  function copyGenes(dir) {
    const c = C(), out = [];
    for (let i = 0; i < nF; i++) if (sigOf(c, i) === dir) { const g = geneOf(i); if (g && !out.includes(g)) out.push(g); }
    copyText(out.join("\n"), () => flash($("#copied"), "copied " + out.length + " " + dir + " genes (paste into STRING, Enrichr, …)"));
    flash($("#copied"), out.length ? "copying " + out.length + " genes…" : "no " + dir + " hits");
  }

  // -------------------------------------------------------- p histogram
  function renderPHist() {
    const host = $("#phist");
    if (!host || !C()) return;
    const c = C(), bins = new Array(20).fill(0);
    let n = 0;
    for (let i = 0; i < nF; i++) if (c.p[i] != null) { bins[Math.min(19, Math.floor(c.p[i] * 20))]++; n++; }
    const W = widthOf(host, 300), H = heightOf(180), L = 44, R = 8, T = 10, B = 34;
    const root = frame(host, W, H), g = svg("g", {}, root);
    const ymax = Math.max(1, ...bins);
    const X = (v) => L + v * (W - L - R), Y = (v) => H - B - (v / ymax) * (H - T - B);
    axes(g, X, Y, [0, 0.25, 0.5, 0.75, 1], niceTicks(0, ymax, 3), L, R, T, B, W, H, "p-value", "");
    const col = css("--c0");
    bins.forEach((b, k) => title(svg("rect", { x: X(k / 20) + 0.5, y: Y(b), width: (W - L - R) / 20 - 1, height: H - B - Y(b), fill: col, "fill-opacity": 0.75 }, g), (k / 20).toFixed(2) + "–" + ((k + 1) / 20).toFixed(2) + ": " + b));
    if (n) svg("line", { x1: L, x2: W - R, y1: Y(n / 20), y2: Y(n / 20), stroke: css("--muted"), "stroke-dasharray": "3 3" }, g);
    if (n && c.pi0 != null) {
      svg("line", { x1: L, x2: W - R, y1: Y((c.pi0 * n) / 20), y2: Y((c.pi0 * n) / 20), stroke: css("--c1"), "stroke-width": 1.5 }, g);
      text(g, W - R - 4, Y((c.pi0 * n) / 20) - 5, "π0 = " + fmt(c.pi0), { "text-anchor": "end", fill: css("--c1"), "font-size": 11.5 });
    }
    svgTools(host, root, "pvalues_" + c.slug);
    const info = $("#pinfo");
    if (info) {
      const shape = { signal: "Looks healthy: a peak at 0 over a flat floor.", flat: "Flat: little or no signal in this comparison.",
        conservative: "Piles up near 1: often ties from imputation, or a model that overestimates the variance.",
        hump: "A bulge in the middle: the model may not fit (an outlier sample or a hidden batch?)." }[c.pshape] || "";
      info.innerHTML = (c.pi0 != null ? "π0 ≈ " + fmt(c.pi0) + ": about " + pct(1 - c.pi0) + " of tested " + esc(D.levelWord) + "s differ (Storey). " : "") + esc(shape);
    }
  }

  // ------------------------------------------------------------ compare
  let cmpA = 0, cmpB = 1;
  function renderCompare() {
    const host = $("#comparebody");
    if (!host) return;
    const two = D.comps.length >= 2, sec = $("#compare"), nav = $("#navcompare");
    if (sec) sec.hidden = !two;
    if (nav) nav.hidden = !two;
    if (!two) { host.innerHTML = ""; return; }
    cmpA = Math.min(cmpA, D.comps.length - 1); cmpB = Math.min(cmpB, D.comps.length - 1);
    if (cmpA === cmpB) cmpB = (cmpA + 1) % D.comps.length;
    const opt = (sel) => D.comps.map((c, k) => "<option value='" + k + "'" + (k === sel ? " selected" : "") + ">" + esc(c.name) + "</option>").join("");
    host.innerHTML = "<div class='row'><label class='ctl'>x <select id='cmpa'>" + opt(cmpA) + "</select></label> <label class='ctl'>y <select id='cmpb'>" + opt(cmpB) + "</select></label></div>" +
      "<div class='grid2 wide'><div><div class='card chart' id='quad'></div><div id='quadinfo' class='meta'></div></div><div><div class='card chart' id='upset'></div><div class='meta' id='upinfo'></div></div></div>";
    $("#cmpa").onchange = (e) => { cmpA = +e.target.value; renderCompare(); };
    $("#cmpb").onchange = (e) => { cmpB = +e.target.value; renderCompare(); };
    safe(renderQuadrant, "#quad");
    safe(renderUpset, "#upset");
  }
  function renderQuadrant() {
    const host = $("#quad"), A = D.comps[cmpA], Bc = D.comps[cmpB];
    const W = widthOf(host, 480), H = heightOf(Math.min(480, Math.max(340, W * 0.85))), L = 52, R = 14, T = 34, B = 44;
    const pts = [];
    let lim = 1;
    for (let i = 0; i < nF; i++) if (A.fc[i] != null && Bc.fc[i] != null) { pts.push(i); lim = Math.max(lim, Math.abs(A.fc[i]), Math.abs(Bc.fc[i])); }
    lim *= 1.06;
    const X = (v) => L + ((v + lim) / (2 * lim)) * (W - L - R), Y = (v) => H - B - ((v + lim) / (2 * lim)) * (H - T - B);
    const root = frame(host, W, H), g = svg("g", {}, root);
    axes(g, X, Y, niceTicks(-lim, lim, 6), niceTicks(-lim, lim, 6), L, R, T, B, W, H, "log2FC " + A.name, "log2FC " + Bc.name);
    const mu = css("--muted");
    svg("line", { x1: X(-lim), y1: Y(-lim), x2: X(lim), y2: Y(lim), stroke: mu, "stroke-dasharray": "2 4" }, g);
    [-ST.lfc, ST.lfc].forEach((v) => { if (ST.lfc > 0) { svg("line", { x1: X(v), x2: X(v), y1: T, y2: H - B, stroke: mu, "stroke-dasharray": "4 4" }, g); svg("line", { x1: L, x2: W - R, y1: Y(v), y2: Y(v), stroke: mu, "stroke-dasharray": "4 4" }, g); } });
    const cls = { both: css("--c6"), a: css("--c1"), b: css("--c2"), opp: css("--c4"), ns: css("--ns") };
    const cnt = { both: 0, a: 0, b: 0, opp: 0 };
    const kind = (i) => {
      const sa = sigOf(A, i), sb = sigOf(Bc, i);
      if (sa && sb) return sa === sb ? "both" : "opp";
      return sa ? "a" : sb ? "b" : "ns";
    };
    const byK = pts.map((i) => [i, kind(i)]).sort((x, y) => (x[1] === "ns" ? 0 : 1) - (y[1] === "ns" ? 0 : 1));
    const screen = [];
    byK.forEach(([i, k]) => {
      if (k !== "ns") cnt[k]++;
      const cx = X(A.fc[i]), cy = Y(Bc.fc[i]);
      screen.push([i, cx, cy]);
      svg("circle", { cx: cx.toFixed(1), cy: cy.toFixed(1), r: k === "ns" ? 2.3 : 3.3, fill: cls[k], "fill-opacity": k === "ns" ? 0.6 : 0.95 }, g);
    });
    if (anyMark()) screen.forEach((p) => { if (matches(p[0])) svg("circle", { cx: p[1], cy: p[2], r: 5.5, fill: "none", stroke: css("--sel"), "stroke-width": 1.3 }, g); });
    ST.groups.forEach((gr, gi) => { if (gr.on) screen.forEach((p) => { if (gr.idx.has(p[0])) svg("circle", { cx: p[1], cy: p[2], r: 4.6, fill: "none", stroke: groupColor(gi), "stroke-width": 1.6 }, g); }); });
    // name the strongest shared and specific ones
    const lab = [];
    ["both", "opp", "a", "b"].forEach((k) => byK.filter((x) => x[1] === k).sort((x, y) => (Math.abs(A.fc[y[0]]) + Math.abs(Bc.fc[y[0]])) - (Math.abs(A.fc[x[0]]) + Math.abs(Bc.fc[x[0]]))).slice(0, 4).forEach((x) => lab.push(x[0])));
    const marked = anyMark() ? screen.filter((p) => matches(p[0])).slice(0, 20).map((p) => p[0]) : [];
    const pos = new Map(screen.map((p) => [p[0], [p[1], p[2]]]));
    placeLabels(g, marked.concat(lab.filter((i) => !marked.includes(i))).map((i) => [i].concat(pos.get(i))), [L, W - R, T, H - B], 11, (i) => marked.includes(i));
    const hit = svg("rect", { x: L, y: T, width: W - L - R, height: H - T - B, fill: "transparent" }, root);
    const near = (e) => { const r = root.getBoundingClientRect(), mx = (e.clientX - r.left) * (W / r.width), my = (e.clientY - r.top) * (H / r.height); let best = null, bd = 64; screen.forEach((p) => { const d = (p[1] - mx) ** 2 + (p[2] - my) ** 2; if (d < bd) { bd = d; best = p[0]; } }); return best; };
    hit.addEventListener("mousemove", (e) => { const i = near(e); if (i == null) hideTip(); else showTip(e, "<b>" + esc(nameOf(i)) + "</b><br>" + esc(A.name) + ": " + fmt(A.fc[i]) + " (adj. p " + fmtP(A.q[i]) + ")<br>" + esc(Bc.name) + ": " + fmt(Bc.fc[i]) + " (adj. p " + fmtP(Bc.q[i]) + ")"); });
    hit.addEventListener("mouseleave", hideTip);
    hit.addEventListener("click", (e) => { const i = near(e); if (i != null) { ST.ci = cmpA; syncControls(); renderDiff(); setFocus(i); goTo("differential"); } });
    svgTools(host, root, "compare_" + A.slug + "_" + Bc.slug);
    const r = pearson(pts.map((i) => A.fc[i]), pts.map((i) => Bc.fc[i]));
    $("#quadinfo").innerHTML = "<div class='legend'><span><span class='sw' style='background:" + cls.both + "'></span>same direction in both (" + cnt.both + ")</span><span><span class='sw' style='background:" + cls.opp + "'></span>opposite (" + cnt.opp + ")</span><span><span class='sw' style='background:" + cls.a + "'></span>only " + esc(A.name) + " (" + cnt.a + ")</span><span><span class='sw' style='background:" + cls.b + "'></span>only " + esc(Bc.name) + " (" + cnt.b + ")</span></div>" +
      "Fold changes correlate r = " + fmt(r, 2) + " over " + fmtInt(pts.length) + " " + esc(D.levelWord) + "s. Points off the diagonal are specific to one comparison; opposite-direction hits are worth a look. Click a point to open it.";
  }
  function renderUpset() {
    const host = $("#upset"), comps = D.comps.slice(0, 8), K = comps.length;
    const sets = comps.map((c) => { const s = new Set(); for (let i = 0; i < nF; i++) if (sigOf(c, i)) s.add(i); return s; });
    const inter = new Map();
    for (let i = 0; i < nF; i++) {
      let key = 0;
      for (let k = 0; k < K; k++) if (sets[k].has(i)) key |= 1 << k;
      if (key) { let a = inter.get(key); if (!a) inter.set(key, (a = [])); a.push(i); }
    }
    const bars = Array.from(inter.entries()).sort((a, b) => b[1].length - a[1].length).slice(0, 16);
    if (!bars.length) { host.innerHTML = "<div class='empty'>No hits at these cut-offs.</div>"; $("#upinfo").textContent = ""; return; }
    const labW = 140, W = Math.max(widthOf(host, 420), labW + bars.length * 22 + 20), topH = 150, rowH = 18, H = topH + K * rowH + 26;
    const root = frame(host, W, H), g = svg("g", {}, root);
    const colW = Math.min(44, (W - labW - 10) / bars.length), ymax = Math.max(...bars.map((b) => b[1].length));
    const acc = css("--text2"), dim = css("--line");
    bars.forEach(([key, idx], b) => {
      const x = labW + b * colW + colW / 2, h = (idx.length / ymax) * (topH - 30);
      const r = svg("rect", { x: x - colW * 0.35, y: topH - 8 - h, width: colW * 0.7, height: h, fill: acc, rx: 1.5, style: "cursor:pointer" }, g);
      const names = comps.filter((_, k) => key & (1 << k)).map((c) => c.name);
      title(r, idx.length + " hit(s) in " + names.join(" and ") + " only — click to mark them");
      r.addEventListener("click", () => { setHighlight(new Set(idx), "hits in " + names.join(" + ") + " only"); goTo("differential"); });
      text(g, x, topH - 12 - h, String(idx.length), { "text-anchor": "middle", "font-size": 10.5 });
      let first = -1, last = -1;
      for (let k = 0; k < K; k++) {
        const on = key & (1 << k), cy = topH + k * rowH + rowH / 2;
        if (on) { if (first < 0) first = k; last = k; }
        svg("circle", { cx: x, cy: cy, r: 5, fill: on ? acc : dim }, g);
      }
      if (last > first) svg("line", { x1: x, x2: x, y1: topH + first * rowH + rowH / 2, y2: topH + last * rowH + rowH / 2, stroke: acc, "stroke-width": 2 }, g);
    });
    comps.forEach((c, k) => text(g, labW - 8, topH + k * rowH + rowH / 2 + 4, (c.name.length > 20 ? c.name.slice(0, 19) + "…" : c.name) + " (" + sets[k].size + ")", { "text-anchor": "end", "font-size": 11, fill: css("--text2") }));
    svgTools(host, root, "upset_hits");
    $("#upinfo").textContent = "Each column is a set of hits found in exactly the comparisons with a dark dot (UpSet plot). Click a bar to mark those " + D.levelWord + "s on the volcano.";
  }

  // ------------------------------------------------------------- on / off
  let onoffCi = 0;
  function renderOnOff() {
    const host = $("#onoffbody");
    if (!host) return;
    const withOO = D.comps.filter((c) => c.t2);
    if (!withOO.length) { host.innerHTML = "<div class='empty'>Needs a comparison between two groups.</div>"; return; }
    onoffCi = Math.min(onoffCi, D.comps.length - 1);
    const c = D.comps[onoffCi];
    const items = c.onoff || [];
    let h = "<div class='row'><label class='ctl'>Comparison <select id='oocomp'>" + D.comps.map((x, k) => "<option value='" + k + "'" + (k === onoffCi ? " selected" : "") + ">" + esc(x.name) + " (" + (x.onoff || []).length + ")</option>").join("") + "</select></label>" +
      (items.length ? " <button id='oomark'>Mark on the volcano</button> <button id='oocopy'>Copy names</button>" : "") + "</div>";
    if (!items.length) h += "<div class='empty'>" + (c.t2 ? "No " + esc(D.levelWord) + " is measured in one group and missing from the other." : "Not for ratio-vs-0 comparisons.") + "</div>";
    else {
      h += "<div class='tablewrap' style='max-height:440px'><table><thead><tr><th>Gene</th><th>ID</th><th>only in</th><th>measured</th><th>mean log2</th>" + (D.f.pep ? "<th>" + esc(D.evidence) + "</th>" : "") + "<th>volcano</th><th>Description</th></tr></thead><tbody>";
      items.forEach((x) => {
        const i = x[0], grp = x[1] === "t" ? c.t1 : c.t2 === "others" ? "others" : c.t2;
        const vals = D.samples.map((_, j) => (!isImputed(i, j) && D.v[i][j] != null && (x[1] === "t" ? D.cond[j] === c.t1 : c.t2 === "others" ? D.cond[j] !== c.t1 : D.cond[j] === c.t2) ? D.v[i][j] : null)).filter((v) => v != null);
        const mean = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
        const s = sigOf(c, i);
        h += "<tr data-i='" + i + "'><td>" + esc(D.f.label[i] || "") + "</td><td>" + esc((D.f.id[i] || "").slice(0, 30)) + "</td><td><span class='badge oo'>" + esc(grp) + "</span></td><td class='n'>" + x[2] + " / " + x[3] + "</td><td class='n'>" + fmt(mean) + "</td>" +
          (D.f.pep ? "<td class='n'>" + (D.f.pep[i] == null ? "" : D.f.pep[i]) + "</td>" : "") + "<td>" + (c.fc[i] == null ? "<span class='muted'>not tested</span>" : (s ? '<span class="dot ' + s + '"></span> ' : "") + fmt(c.fc[i]) + (impDriven(c)[i] ? " <span class='muted'>(imputed)</span>" : "")) + "</td><td class='desc'>" + esc((D.f.desc[i] || "").slice(0, 100)) + "</td></tr>";
      });
      h += "</tbody></table></div><p class='muted'>Sorted by how complete the group is, then by abundance: an abundant protein that is never seen in the other group is the strongest case.</p>";
    }
    host.innerHTML = h;
    $("#oocomp").onchange = (e) => { onoffCi = +e.target.value; renderOnOff(); };
    const mk = $("#oomark");
    if (mk) mk.onclick = () => { ST.ci = onoffCi; syncControls(); renderDiff(); setHighlight(new Set(items.map((x) => x[0])), "only in one condition"); goTo("differential"); };
    const cp = $("#oocopy");
    if (cp) cp.onclick = () => copyText(items.map((x) => nameOf(x[0])).join("\n"));
    $$("tbody tr[data-i]", host).forEach((tr) => (tr.onclick = () => { ST.ci = onoffCi; syncControls(); renderDiff(); setFocus(+tr.dataset.i); goTo("differential"); }));
  }

  // ------------------------------------------------------------- heatmap
  function divColor(v, lim) {
    const t = Math.max(-1, Math.min(1, v / lim));
    const a = t >= 0 ? hex(css("--up")) : hex(css("--down")), s = hex(css("--surface"));
    const k = Math.abs(t);
    return "rgb(" + Math.round(s[0] + (a[0] - s[0]) * k) + "," + Math.round(s[1] + (a[1] - s[1]) * k) + "," + Math.round(s[2] + (a[2] - s[2]) * k) + ")";
  }
  function hex(c) {
    c = (c || "#888888").replace("#", "");
    if (c.length === 3) c = c.split("").map((x) => x + x).join("");
    return [parseInt(c.slice(0, 2), 16), parseInt(c.slice(2, 4), 16), parseInt(c.slice(4, 6), 16)];
  }
  function renderHeatmap() {
    const host = $("#heatmap");
    if (!host) return;
    const hm = D.qc.heatmap;
    if (!hm || !hm.rows.length) { host.innerHTML = '<div class="empty">No significant features to cluster at the report\'s cut-offs.</div>'; return; }
    const cols = hm.cols, rows = hm.rows, vals = hm.values;
    const showNames = rows.length <= 80;
    const labW = showNames ? 110 : 8, topH = 90, cellH = Math.max(3, Math.min(14, Math.floor(620 / rows.length)));
    const W = widthOf(host), cellW = Math.max(8, Math.floor((W - labW - 20) / cols.length));
    const H = topH + rows.length * cellH + 8;
    host.querySelectorAll("canvas,svg").forEach((x) => x.remove());
    const cv = document.createElement("canvas"), dpr = window.devicePixelRatio || 1;
    cv.width = (labW + cols.length * cellW + 10) * dpr; cv.height = H * dpr;
    cv.style.width = labW + cols.length * cellW + 10 + "px"; cv.style.height = H + "px";
    host.insertBefore(cv, host.firstChild);
    const ctx = cv.getContext("2d");
    ctx.scale(dpr, dpr);
    const all = [];
    vals.forEach((r) => r.forEach((v) => v != null && all.push(Math.abs(v))));
    all.sort((a, b) => a - b);
    const lim = Math.max(0.5, all[Math.floor(all.length * 0.95)] || 1);
    ctx.font = "11px system-ui, sans-serif";
    ctx.fillStyle = css("--text2");
    cols.forEach((j, k) => {
      ctx.save();
      ctx.translate(labW + k * cellW + cellW / 2 + 3, topH - 14);
      ctx.rotate(-Math.PI / 3);
      ctx.fillText(D.samples[j].slice(0, 16), 0, 0);
      ctx.restore();
      ctx.fillStyle = condColor(D.cond[j]);
      ctx.fillRect(labW + k * cellW, topH - 10, cellW - 1, 7);
      ctx.fillStyle = css("--text2");
    });
    const sel = css("--sel");
    rows.forEach((i, r) => {
      vals[r].forEach((v, k) => {
        ctx.fillStyle = v == null ? css("--sunk") : divColor(v, lim);
        ctx.fillRect(labW + k * cellW, topH + r * cellH, cellW - (cellW > 10 ? 1 : 0), cellH - (cellH > 6 ? 1 : 0));
      });
      if (anyMark() && matches(i)) { ctx.fillStyle = sel; ctx.fillRect(labW - 4, topH + r * cellH, 3, cellH - 1); }
      if (showNames) {
        ctx.fillStyle = css("--text2");
        ctx.textAlign = "right";
        ctx.fillText(nameOf(i).slice(0, 16), labW - 6, topH + r * cellH + cellH - 2);
        ctx.textAlign = "left";
      }
    });
    cv.onmousemove = (e) => {
      const rc = cv.getBoundingClientRect(), x = e.clientX - rc.left, y = e.clientY - rc.top;
      const k = Math.floor((x - labW) / cellW), r = Math.floor((y - topH) / cellH);
      if (k < 0 || k >= cols.length || r < 0 || r >= rows.length) { hideTip(); return; }
      const i = rows[r], j = cols[k];
      showTip(e, "<b>" + esc(nameOf(i)) + "</b><br>" + esc(D.samples[j]) + " (" + esc(D.cond[j]) + ")<br>centred log2 " + fmt(vals[r][k]) + (isImputed(i, j) ? " · imputed" : ""));
    };
    cv.onmouseleave = hideTip;
    cv.onclick = (e) => {
      const rc = cv.getBoundingClientRect(), r = Math.floor((e.clientY - rc.top - topH) / cellH);
      if (r >= 0 && r < rows.length) { setFocus(rows[r]); goTo("differential"); }
    };
    $("#heatlegend").innerHTML = "<span><span class='sw' style='background:" + css("--down") + "'></span>below the protein's mean</span><span><span class='sw' style='background:" +
      css("--up") + "'></span>above</span><span class='muted'>colour saturates at ±" + fmt(lim, 1) + " log2 · " + rows.length + " of " + fmtInt(hm.total_significant) +
      " significant features, clustered (euclidean, complete linkage) · click a row to open it · search matches are marked at the left</span>";
    svgTools(host, null, "heatmap", heatmapSvg);
  }

  // ----------------------------------------------------------- enrichment
  let enrMode = null;
  function renderEnrichment() {
    const host = $("#enrich");
    if (!host) return;
    if (!D.enr.length && !D.gsea.length) { host.innerHTML = '<div class="empty">' + esc(D.enrNote || "Enrichment was not run.") + "</div>"; return; }
    if (!enrMode) enrMode = D.enr.length ? "ora" : "rank";
    host.innerHTML = "<div class='tabs'>" + (D.enr.length ? "<button data-m='ora'" + (enrMode === "ora" ? " class='on'" : "") + ">Among the hits (over-representation)</button>" : "") +
      (D.gsea.length ? "<button data-m='rank'" + (enrMode === "rank" ? " class='on'" : "") + ">All " + esc(D.levelWord) + "s ranked (no cut-off)</button>" : "") + "</div><div id='enrbody'></div>";
    $$(".tabs button", host).forEach((b) => (b.onclick = () => { enrMode = b.dataset.m; renderEnrichment(); }));
    const body = $("#enrbody");
    if (enrMode === "rank" && D.gsea.length) renderRank(body); else renderORA(body);
  }
  function renderORA(host) {
    if (!D.enr.length) { host.innerHTML = '<div class="empty">' + esc(D.enrNote || "Over-representation was not run.") + "</div>"; return; }
    const comps = [...new Set(D.enr.map((b) => b.comparison))], libs = [...new Set(D.enr.map((b) => b.library))];
    const st = host._st || (host._st = renderORA._st || (renderORA._st = { comp: comps[0], dir: "up", lib: libs[0] }));
    let h = "<div class='bar' style='position:static'><label class='ctl'>Comparison <select id='ecomp'>" + comps.map((c) => "<option" + (c === st.comp ? " selected" : "") + ">" + esc(c) + "</option>").join("") + "</select></label>" +
      "<label class='ctl'>Hits <select id='edir'><option value='up'" + (st.dir === "up" ? " selected" : "") + ">up</option><option value='down'" + (st.dir === "down" ? " selected" : "") + ">down</option></select></label>" +
      "<label class='ctl'>Gene sets <select id='elib'>" + libs.map((c) => "<option" + (c === st.lib ? " selected" : "") + ">" + esc(c) + "</option>").join("") + "</select></label></div>";
    const b = D.enr.find((x) => x.comparison === st.comp && x.direction === st.dir && x.library === st.lib);
    h += "<p class='sub'>" + (b ? fmtInt(b.hits) + " " + st.dir + " hits tested against " + fmtInt(b.background) + " quantified genes (one-sided Fisher test, BH-adjusted), at the report's saved cut-offs." : "") + "</p>";
    h += "<div id='ebars' class='chart card'></div><div id='etable'></div>";
    host.innerHTML = h;
    $("#ecomp").onchange = (e) => { st.comp = e.target.value; renderEnrichment(); };
    $("#edir").onchange = (e) => { st.dir = e.target.value; renderEnrichment(); };
    $("#elib").onchange = (e) => { st.lib = e.target.value; renderEnrichment(); };
    const terms = b ? b.terms : [];
    if (!terms.length) { $("#ebars").innerHTML = '<div class="empty">No terms (no hits in this direction, or none overlap these gene sets).</div>'; return; }
    const top = terms.slice(0, 15), host2 = $("#ebars");
    const W = widthOf(host2), L = Math.min(360, W * 0.45), R = 60, T = 30, rowH = 22, H = T + top.length * rowH + 34;
    const root = frame(host2, W, H), g = svg("g", {}, root);
    const xmax = Math.max(2, ...top.map((t) => -Math.log10(Math.max(t.q, 1e-300))));
    const X = (v) => L + (v / xmax) * (W - L - R);
    niceTicks(0, xmax, 5).forEach((v) => { svg("line", { x1: X(v), x2: X(v), y1: T, y2: H - 30, stroke: css("--grid") }, g); text(g, X(v), H - 16, fmtTick(v), { "text-anchor": "middle" }); });
    text(g, (L + W - R) / 2, H - 2, "−log10 adjusted p", { "text-anchor": "middle", fill: css("--text2") });
    svg("line", { x1: X(-Math.log10(0.05)), x2: X(-Math.log10(0.05)), y1: T, y2: H - 30, stroke: css("--muted"), "stroke-dasharray": "4 4" }, g);
    const col = st.dir === "up" ? css("--up") : css("--down");
    top.forEach((t, k) => {
      const y = T + k * rowH, v = -Math.log10(Math.max(t.q, 1e-300));
      title(svg("rect", { x: L, y: y + 3, width: Math.max(1, X(v) - L), height: rowH - 7, fill: col, "fill-opacity": t.q <= 0.05 ? 0.9 : 0.35, rx: 2 }, g), t.term + "\n" + t.k + " of " + t.K + " genes · adj. p " + fmtP(t.q) + "\n" + t.genes.join(", "));
      text(g, L - 8, y + rowH / 2 + 4, t.term.length > 52 ? t.term.slice(0, 50) + "…" : t.term, { "text-anchor": "end", fill: css("--text2") });
      text(g, X(v) + 5, y + rowH / 2 + 4, t.k + "/" + t.K, { "font-size": 11 });
    });
    svgTools(host2, root, "enrichment_" + st.comp + "_" + st.dir + "_" + st.lib);
    let tb = "<div class='tablewrap' style='max-height:380px'><table><thead><tr><th>Term</th><th>overlap</th><th>p</th><th>adj. p</th><th>log2 odds</th><th>genes</th></tr></thead><tbody>";
    terms.forEach((t, k) => {
      tb += "<tr data-k='" + k + "' title='Show these genes on the volcano'><td>" + esc(t.term) + "</td><td class='n'>" + t.k + "/" + t.K + "</td><td class='n'>" + fmtP(t.p) + "</td><td class='n'>" + fmtP(t.q) +
        "</td><td class='n'>" + (isFinite(t.log2_odds) ? fmt(t.log2_odds) : "∞") + "</td><td class='desc'>" + esc(t.genes.join(", ")) + "</td></tr>";
    });
    $("#etable").innerHTML = tb + "</tbody></table></div><p class='muted'>Click a term to mark its genes on the volcano.</p>";
    $$("#etable tbody tr").forEach((tr) => (tr.onclick = () => {
      const t = terms[+tr.dataset.k];
      const k = D.comps.findIndex((c) => c.name === st.comp);
      if (k >= 0) ST.ci = k;
      syncControls(); renderDiff();
      setHighlight(indicesOfGenes(t.genes), t.term);
      goTo("differential");
    }));
  }
  function renderRank(host) {
    const comps = [...new Set(D.gsea.map((b) => b.comparison))], libs = [...new Set(D.gsea.map((b) => b.library))];
    const st = renderRank._st || (renderRank._st = { comp: comps[0], lib: libs[0], dir: "both", sel: null });
    if (!comps.includes(st.comp)) st.comp = comps[0];
    if (!libs.includes(st.lib)) st.lib = libs[0];
    const b = D.gsea.find((x) => x.comparison === st.comp && x.library === st.lib);
    let h = "<div class='bar' style='position:static'><label class='ctl'>Comparison <select id='rcomp'>" + comps.map((c) => "<option" + (c === st.comp ? " selected" : "") + ">" + esc(c) + "</option>").join("") + "</select></label>" +
      "<label class='ctl'>Gene sets <select id='rlib'>" + libs.map((c) => "<option" + (c === st.lib ? " selected" : "") + ">" + esc(c) + "</option>").join("") + "</select></label>" +
      "<label class='ctl'>Direction <select id='rdir'>" + ["both", "up", "down"].map((d) => "<option" + (d === st.dir ? " selected" : "") + ">" + d + "</option>").join("") + "</select></label></div>" +
      "<p class='sub'>Every tested gene ranked by its statistic" + (b ? " (" + fmtInt(b.tested) + " genes)" : "") + "; a set scores when its members sit high (up) or low (down) in the ranking, even if none passes the cut-offs. Rank-sum test" + (b && b.adjusted ? " with the variance inflated by the set's inter-gene correlation (as limma's camera)" : "") + ", BH-adjusted.</p>" +
      "<div id='rbars' class='chart card'></div><div id='rcode' class='chart card' hidden></div><div id='rtable'></div>";
    host.innerHTML = h;
    $("#rcomp").onchange = (e) => { st.comp = e.target.value; st.sel = null; renderEnrichment(); };
    $("#rlib").onchange = (e) => { st.lib = e.target.value; st.sel = null; renderEnrichment(); };
    $("#rdir").onchange = (e) => { st.dir = e.target.value; renderEnrichment(); };
    const terms = (b ? b.terms : []).filter((t) => st.dir === "both" || t.dir === st.dir);
    if (!terms.length) { $("#rbars").innerHTML = "<div class='empty'>No gene set has enough measured members here.</div>"; return; }
    const top = terms.slice(0, 18), h2 = $("#rbars");
    const W = widthOf(h2), L = Math.min(360, W * 0.45), R = 40, T = 26, rowH = 22, H = T + top.length * rowH + 34;
    const root = frame(h2, W, H), g = svg("g", {}, root);
    const zmax = Math.max(2, ...top.map((t) => Math.abs(t.z)));
    const mid = L + (W - L - R) / 2, X = (v) => mid + (v / zmax) * ((W - L - R) / 2);
    niceTicks(-zmax, zmax, 6).forEach((v) => { svg("line", { x1: X(v), x2: X(v), y1: T, y2: H - 30, stroke: css("--grid") }, g); text(g, X(v), H - 16, fmtTick(v), { "text-anchor": "middle" }); });
    svg("line", { x1: mid, x2: mid, y1: T, y2: H - 30, stroke: css("--axis") }, g);
    text(g, mid, H - 2, "z (← down · up →)", { "text-anchor": "middle", fill: css("--text2") });
    top.forEach((t, k) => {
      const y = T + k * rowH, col = t.dir === "up" ? css("--up") : css("--down");
      const r = svg("rect", { x: Math.min(mid, X(t.z)), y: y + 3, width: Math.max(1, Math.abs(X(t.z) - mid)), height: rowH - 7, fill: col, "fill-opacity": t.q <= 0.05 ? 0.9 : 0.35, rx: 2, style: "cursor:pointer" }, g);
      title(r, t.term + "\n" + t.n + " genes measured · z " + fmt(t.z) + " · adj. p " + fmtP(t.q) + "\nleading: " + t.leading.join(", "));
      r.addEventListener("click", () => { st.sel = t.term; renderEnrichment(); });
      text(g, L - 8, y + rowH / 2 + 4, t.term.length > 52 ? t.term.slice(0, 50) + "…" : t.term, { "text-anchor": "end", fill: t.term === st.sel ? css("--text") : css("--text2"), "font-weight": t.term === st.sel ? 650 : 400 });
    });
    svgTools(h2, root, "gene_set_ranks_" + st.comp + "_" + st.lib);
    let tb = "<div class='tablewrap' style='max-height:380px'><table><thead><tr><th>Term</th><th></th><th>genes</th><th>z</th><th>p</th><th>adj. p</th><th>median</th><th>corr.</th><th>leading genes</th></tr></thead><tbody>";
    terms.forEach((t, k) => {
      tb += "<tr data-k='" + k + "'" + (t.term === st.sel ? " class='focus'" : "") + "><td>" + esc(t.term) + "</td><td><span class='dot " + t.dir + "'></span></td><td class='n'>" + t.n + "</td><td class='n'>" + fmt(t.z) + "</td><td class='n'>" + fmtP(t.p) + "</td><td class='n'>" + fmtP(t.q) +
        "</td><td class='n'>" + fmt(t.median) + "</td><td class='n'>" + fmt(t.corr) + "</td><td class='desc'>" + esc(t.leading.join(", ")) + "</td></tr>";
    });
    $("#rtable").innerHTML = tb + "</tbody></table></div><p class='muted'>Click a term for its barcode plot (where its genes sit in the ranking); from there, mark them on the volcano.</p>";
    $$("#rtable tbody tr").forEach((tr) => (tr.onclick = () => { st.sel = terms[+tr.dataset.k].term; renderEnrichment(); }));
    const sel = terms.find((t) => t.term === st.sel);
    if (sel) renderBarcode($("#rcode"), sel, st.comp);
  }
  /** limma-style barcode plot: every tested feature ranked by signed -log10 p; the set's members as ticks. */
  function renderBarcode(host, t, compName) {
    host.hidden = false;
    const c = D.comps.find((x) => x.name === compName) || C();
    const order = [];
    for (let i = 0; i < nF; i++) if (c.p[i] != null && c.fc[i] != null) order.push(i);
    const sc = (i) => Math.sign(c.fc[i]) * -Math.log10(Math.max(c.p[i], 1e-300));
    order.sort((a, b) => sc(b) - sc(a));
    const members = indicesOfGenes(t.genes);
    const W = widthOf(host), H = heightOf(176), L = 20, R = 20, T = 70, B = 30;
    const root = frame(host, W, H), g = svg("g", {}, root);
    const X = (k) => L + (k / Math.max(1, order.length - 1)) * (W - L - R);
    const grad = svg("linearGradient", { id: "bcg", x1: 0, x2: 1, y1: 0, y2: 0 }, svg("defs", {}, root));
    svg("stop", { offset: "0", "stop-color": css("--up"), "stop-opacity": 0.35 }, grad);
    svg("stop", { offset: "0.5", "stop-color": css("--surface"), "stop-opacity": 0 }, grad);
    svg("stop", { offset: "1", "stop-color": css("--down"), "stop-opacity": 0.35 }, grad);
    svg("rect", { x: L, y: H - B - 10, width: W - L - R, height: 10, fill: "url(#bcg)" }, g);
    const hits = [];
    order.forEach((i, k) => { if (members.has(i)) hits.push([i, k]); });
    hits.forEach(([i, k]) => title(svg("line", { x1: X(k), x2: X(k), y1: T, y2: H - B - 12, stroke: css("--text"), "stroke-width": 1.2, "stroke-opacity": 0.8 }, g), nameOf(i) + " · rank " + (k + 1) + " · log2FC " + fmt(c.fc[i])));
    // running density of members (window of 10% of the ranking), so a skew is visible at a glance
    const win = Math.max(10, Math.round(order.length / 10)), pts = [];
    const expect = hits.length / Math.max(1, order.length);
    for (let k = 0; k < order.length; k += Math.max(1, Math.floor(order.length / 200))) {
      let n = 0;
      hits.forEach((h) => { if (Math.abs(h[1] - k) <= win / 2) n++; });
      pts.push([X(k), n / win / Math.max(expect, 1e-9)]);
    }
    const ymax = Math.max(2, ...pts.map((p) => p[1]));
    const dY = (v) => T - 8 - (v / ymax) * 40;  // 1 = the set's average density
    svg("line", { x1: L, x2: W - R, y1: dY(1), y2: dY(1), stroke: css("--grid"), "stroke-dasharray": "3 3" }, g);
    text(g, W - R, dY(1) - 3, "average", { "text-anchor": "end", "font-size": 10 });
    svg("polyline", { points: pts.map((p) => p[0].toFixed(1) + "," + dY(p[1]).toFixed(1)).join(" "), fill: "none", stroke: t.dir === "up" ? css("--up") : css("--down"), "stroke-width": 1.8 }, g);
    text(g, L, 14, t.term.slice(0, 80) + " — " + hits.length + " genes", { fill: css("--text"), "font-size": 12.5, "font-weight": 600 });
    text(g, L, H - 8, "← up in " + c.name, { "font-size": 11 });
    text(g, W - R, H - 8, "down →", { "font-size": 11, "text-anchor": "end" });
    svgTools(host, root, "barcode_" + t.term.slice(0, 40));
    let btn = $("#rmark");
    if (!btn) { btn = document.createElement("button"); btn.id = "rmark"; host.appendChild(btn); }
    btn.textContent = "Mark these " + members.size + " on the volcano";
    btn.onclick = () => {
      const k = D.comps.findIndex((x) => x.name === compName);
      if (k >= 0) ST.ci = k;
      syncControls(); renderDiff();
      setSearch("term:" + t.term);
      goTo("differential");
    };
  }

  // ------------------------------------------------------------------- QC
  const QC_TABS = [["card", "Sample scorecard"], ["pca", "PCA"], ["corr", "Correlation"], ["missing", "Missing values"], ["mnar", "Missing vs intensity"], ["dist", "Distributions"], ["cv", "CV"],
    ["mv", "Mean–variance"], ["rank", "Abundance rank"], ["ids", "Identifications"], ["imp", "Imputation"], ["power", "Power"], ["psm", "Search quality"]];
  const QC_TIPS = { card: "Each sample against the others: which ones stand out", pca: "Do the replicates of a condition sit together?", corr: "How alike the samples are, pair by pair",
    missing: "How many values are missing, and where", mnar: "Are values missing because they are low?", dist: "The spread of values in each sample, before and after normalisation",
    cv: "How reproducible each condition's replicates are", mv: "Does the spread depend on the abundance?", rank: "The abundance of every feature, from most to least",
    ids: "How many features each sample has", imp: "The imputed values against the measured ones", power: "The smallest fold change this design can detect", psm: "What the search made of each raw file" };
  let qcTab = null;
  function renderQC() {
    const host = $("#qc");
    if (!host) return;
    const tabs = QC_TABS.filter(([k]) => (k !== "imp" || D.imp) && (k !== "card" || (D.qc.scorecard && D.qc.scorecard.length)) && (k !== "mnar" || D.qc.mnar) && (k !== "power" || D.qc.power) && (k !== "mv" || D.kind !== "ratio" || nS > 2) && (k !== "psm" || D.qc.psm));
    if (!qcTab || !tabs.some(([k]) => k === qcTab)) qcTab = tabs.some(([k]) => k === "card") ? "card" : "pca";
    host.innerHTML = "<div class='tabs'>" + tabs.map(([k, t]) => "<button data-k='" + k + "' title='" + esc(QC_TIPS[k] || "") + "'" + (k === qcTab ? " class='on'" : "") + ">" + t + "</button>").join("") + "</div><div id='qcbody'></div>";
    $$(".tabs button", host).forEach((b) => (b.onclick = () => { qcTab = b.dataset.k; renderQC(); }));
    const body = $("#qcbody");
    safe(() => ({ card: qcCard, pca: qcPCA, corr: qcCorr, missing: qcMissing, mnar: qcMNAR, dist: qcDist, cv: qcCV, mv: qcMV, rank: qcRank, ids: qcIds, imp: qcImp, power: qcPower, psm: qcPsm })[qcTab](body), body);
    addHelp($(":scope > .sub", body), "qc." + qcTab);
  }
  function legend() { return "<div class='legend'>" + D.conditions.map((c) => "<span><span class='sw' style='background:" + condColor(c) + "'></span>" + esc(c) + "</span>").join("") + "</div>"; }
  function qcCard(host) {
    const card = D.qc.scorecard || [];
    const zc = (z, bad) => (z == null ? "" : (bad(z) ? " class='n zbad'" : Math.abs(z) > 2.5 ? " class='n zwarn'" : " class='n'"));
    let h = "<p class='sub'>Each sample against the others. Robust z-scores (median / MAD) flag what stands out; a flagged sample is worth a look in the PCA and correlation before trusting the comparisons. Also in <a href='sample_qc.tsv'>sample_qc.tsv</a>.</p>" +
      "<div class='tablewrap'><table><thead><tr><th>Sample</th><th>Condition</th><th>Status</th><th>IDs</th><th>missing</th><th title='Median log2 before normalisation, minus the median over samples'>loading</th><th title='Median Pearson r with its own replicates'>r with replicates</th><th title='Median |value − mean of its replicates|'>spread</th><th title='Change of the group median CV when this sample is left out (groups of 4+)'>CV without it</th><th>Flags</th></tr></thead><tbody>";
    card.forEach((r) => {
      h += "<tr><td><span class='sw' style='background:" + condColor(r.condition) + "'></span> " + esc(r.sample) + "</td><td>" + esc(r.condition) + "</td><td><span class='pill " + r.status + "'>" + r.status + "</span></td>" +
        "<td" + zc(r.z_ids, (z) => z < -3) + ">" + fmtInt(r.ids) + "</td><td class='n'>" + fmt(r.missing_pct, 1) + "%</td><td class='n'>" + (r.shift == null ? "–" : (r.shift >= 0 ? "+" : "") + fmt(r.shift)) + "</td>" +
        "<td" + zc(r.z_corr, (z) => z < -3.5) + ">" + fmt(r.corr_group, 3) + "</td><td" + zc(r.z_spread, (z) => z > 3.5) + ">" + fmt(r.spread) + "</td><td class='n'>" + (r.loo_cv == null ? "–" : (r.loo_cv >= 0 ? "+" : "") + pct(r.loo_cv)) + "</td>" +
        "<td class='desc'>" + esc(r.flags.join("; ")) + "</td></tr>";
    });
    host.innerHTML = h + "</tbody></table></div>";
  }
  function qcPCA(host) {
    const st = host._st || (qcPCA._st = qcPCA._st || { x: 0, y: 1, names: nS <= 24, by: "condition", when: "after" });
    host._st = st;
    const PB = D.qc.pcaBefore; // TMT plexes: the same PCA before they were put on one scale (plex.py)
    const P = st.when === "before" && PB ? PB : D.qc.pca;
    if (!P || !P.scores.length) { host.innerHTML = "<div class='empty'>Not enough complete features for a PCA.</div>"; return; }
    const hasRep = D.rep && D.rep.some((r) => r != null);
    const hasPlex = D.plex && D.plex.some((p) => p != null);
    const opts = (sel) => P.percent.map((p, k) => "<option value='" + k + "'" + (k === sel ? " selected" : "") + ">PC" + (k + 1) + " (" + p.toFixed(1) + "%)</option>").join("");
    const reps = hasRep ? [...new Set(D.rep.filter((r) => r != null))].sort((a, b) => a - b) : [];
    const plexes = hasPlex ? [...new Set(D.plex.filter((p) => p != null))] : [];
    const colorOf = (j) => (st.by === "replicate" && hasRep ? css("--c" + (reps.indexOf(D.rep[j]) % 8)) : st.by === "plex" && hasPlex ? css("--c" + (plexes.indexOf(D.plex[j]) % 8)) : condColor(D.cond[j]));
    const sel = (v, label) => "<option value='" + v + "'" + (st.by === v ? " selected" : "") + ">" + label + "</option>";
    const byCtl = hasRep || hasPlex ? " <label class='ctl'>colour by <select id='pcby'>" + sel("condition", "condition") + (hasRep ? sel("replicate", "replicate number") : "") + (hasPlex ? sel("plex", "plex") : "") + "</select></label>" : "";
    const whenCtl = PB ? " <label class='ctl'>values <select id='pcw'><option value='after'" + (st.when !== "before" ? " selected" : "") + ">after " + esc(PB.label) + "</option><option value='before'" + (st.when === "before" ? " selected" : "") + ">before " + esc(PB.label) + "</option></select></label>" : "";
    const keyOf = st.by === "replicate" && hasRep ? "<div class='legend'>" + reps.map((r, k) => "<span><span class='sw' style='background:" + css("--c" + (k % 8)) + "'></span>replicate " + r + "</span>").join("") + "</div>"
      : st.by === "plex" && hasPlex ? "<div class='legend'>" + plexes.map((p, k) => "<span><span class='sw' style='background:" + css("--c" + (k % 8)) + "'></span>" + esc(p) + "</span>").join("") + "</div>" : legend();
    let assoc = "";
    const pcs = D.qc.pcs;
    if (pcs && pcs.pcs && pcs.pcs.length) {
      assoc = "<table class='mini'><thead><tr><th></th>" + pcs.pcs.map((p) => "<th>PC" + p.pc + " (" + fmt(p.percent, 0) + "%)</th>").join("") + "</tr></thead><tbody><tr><td>explained by condition</td>" +
        pcs.pcs.map((p) => "<td class='n'>" + (p.r2_condition == null ? "–" : pct(p.r2_condition)) + "</td>").join("") + "</tr>" +
        (pcs.pcs.some((p) => p.r2_replicate != null) ? "<tr><td>explained by replicate number</td>" + pcs.pcs.map((p) => "<td class='n" + (p.r2_replicate != null && p.r2_replicate >= 0.5 && p.r2_replicate > (p.r2_condition || 0) ? " zbad" : "") + "'>" + (p.r2_replicate == null ? "–" : pct(p.r2_replicate)) + "</td>").join("") + "</tr>" : "") +
        "</tbody></table><p class='muted'>One-way ANOVA R² of each component's scores. Condition should explain the top components; replicate number explaining one hints at a batch (samples of the same replicate number prepared or run together).</p>";
    }
    host.innerHTML = "<p class='sub'>Top " + fmtInt(P.n) + " most variable features with no missing values (FragPipe-Analyst plot_pca). Replicates should sit together." +
      (PB ? " Several TMT plexes: colour by plex and compare the values before and after " + esc(PB.label) + "; before it the samples usually group by plex." : "") + "</p>" +
      "<div class='row'><label class='ctl'>x <select id='pcx'>" + opts(st.x) + "</select></label> <label class='ctl'>y <select id='pcy'>" + opts(st.y) + "</select></label> <label class='ctl'><input type='checkbox' id='pcn'" + (st.names ? " checked" : "") + "> names</label>" +
      byCtl + whenCtl + "</div>" + keyOf +
      "<div class='chart card' id='pcachart'></div>" + (P === D.qc.pca ? assoc : "");
    $("#pcx").onchange = (e) => { st.x = +e.target.value; qcPCA(host); };
    $("#pcy").onchange = (e) => { st.y = +e.target.value; qcPCA(host); };
    $("#pcn").onchange = (e) => { st.names = e.target.checked; qcPCA(host); };
    const by = $("#pcby");
    if (by) by.onchange = (e) => { st.by = e.target.value; qcPCA(host); };
    const when = $("#pcw");
    if (when) when.onchange = (e) => { st.when = e.target.value; qcPCA(host); };
    const ch = $("#pcachart"), W = widthOf(ch), H = heightOf(Math.min(520, Math.round(W * 0.6))), L = 56, R = 20, T = 16, B = 44;
    const xs = P.scores.map((s) => s[st.x]), ys = P.scores.map((s) => s[st.y]);
    const pad = (a) => { const lo = Math.min(...a), hi = Math.max(...a), p = (hi - lo) * 0.12 || 1; return [lo - p, hi + p]; };
    const [x0, x1] = pad(xs), [y0, y1] = pad(ys);
    const X = (v) => L + ((v - x0) / (x1 - x0)) * (W - L - R), Y = (v) => H - B - ((v - y0) / (y1 - y0)) * (H - T - B);
    const root = frame(ch, W, H), g = svg("g", {}, root);
    axes(g, X, Y, niceTicks(x0, x1, 6), niceTicks(y0, y1, 5), L, R, T, B, W, H, "PC" + (st.x + 1) + " (" + P.percent[st.x].toFixed(1) + "%)", "PC" + (st.y + 1) + " (" + P.percent[st.y].toFixed(1) + "%)");
    const flagged = new Set((D.qc.scorecard || []).filter((r) => r.status !== "ok").map((r) => r.sample));
    P.scores.forEach((s, j) => {
      const c = svg("circle", { cx: X(s[st.x]), cy: Y(s[st.y]), r: 7, fill: colorOf(j), stroke: flagged.has(D.samples[j]) ? css("--text") : css("--surface"), "stroke-width": flagged.has(D.samples[j]) ? 2.5 : 1.5 }, g);
      c.addEventListener("mousemove", (e) => showTip(e, "<b>" + esc(D.samples[j]) + "</b><br>" + esc(D.cond[j]) + (D.rep && D.rep[j] != null ? " · replicate " + D.rep[j] : "") + (hasPlex && D.plex[j] != null ? " · plex " + esc(D.plex[j]) : "") + (flagged.has(D.samples[j]) ? "<br>flagged in the scorecard" : "")));
      c.addEventListener("mouseleave", hideTip);
      if (st.names) text(g, X(s[st.x]) + 9, Y(s[st.y]) + 4, D.samples[j], { "font-size": 11, fill: css("--text2") });
    });
    svgTools(ch, root, "PCA");
  }
  function seqColor(t) {
    const a = hex(css("--c0")), s = hex(css("--surface"));
    t = Math.max(0, Math.min(1, t));
    return "rgb(" + Math.round(s[0] + (a[0] - s[0]) * t) + "," + Math.round(s[1] + (a[1] - s[1]) * t) + "," + Math.round(s[2] + (a[2] - s[2]) * t) + ")";
  }
  function qcCorr(host) {
    const Cc = D.qc.correlation;
    if (!Cc || !Cc.matrix.length) { host.innerHTML = "<div class='empty'>No correlation.</div>"; return; }
    host.innerHTML = "<p class='sub'>Pearson correlation between samples over " + fmtInt(Cc.complete_rows) + " complete features, clustered. Replicates of a condition should form blocks.</p>" + legend() + "<div class='chart card' id='corrchart'></div>";
    const ch = $("#corrchart"), o = Cc.order, n = o.length;
    const W = widthOf(ch), lab = 130, cell = Math.max(10, Math.min(36, Math.floor((Math.min(W, heightOf(W)) - lab - 20) / n))), H = lab + n * cell + 10;
    const root = frame(ch, lab + n * cell + 12, H), g = svg("g", {}, root);
    let lo = 1, hi = -1;  // off-diagonal range, so r = 1 on the diagonal doesn't flatten the scale
    Cc.matrix.forEach((r, a) => r.forEach((v, b) => { if (v != null && a !== b) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }));
    if (hi <= lo) { lo = Math.min(lo, 0.9); hi = 1; }
    o.forEach((a, r) => {
      text(g, lab - 6, lab + r * cell + cell / 2 + 4, D.samples[a].slice(0, 18), { "text-anchor": "end", "font-size": 11, fill: condColor(D.cond[a]) });
      const t = text(g, 0, 0, D.samples[a].slice(0, 18), { "font-size": 11, fill: condColor(D.cond[a]), transform: "translate(" + (lab + r * cell + cell / 2 + 4) + "," + (lab - 6) + ") rotate(-60)" });
      t.setAttribute("x", 0);
      o.forEach((b, k) => {
        const v = Cc.matrix[a][b];
        const rc = svg("rect", { x: lab + k * cell, y: lab + r * cell, width: cell - 1, height: cell - 1, fill: v == null ? css("--sunk") : seqColor(hi > lo ? (v - lo) / (hi - lo) : 1) }, g);
        rc.addEventListener("mousemove", (e) => showTip(e, esc(D.samples[a]) + " × " + esc(D.samples[b]) + "<br>r = " + fmt(v, 3)));
        rc.addEventListener("mouseleave", hideTip);
      });
    });
    svgTools(ch, root, "correlation");
    ch.insertAdjacentHTML("beforeend", "<div class='legend'><span class='muted'>light = r " + fmt(lo, 3) + " · dark = r " + fmt(hi, 3) + "</span></div>");
  }
  function qcMissing(host) {
    const M = D.qc.missing;
    if (!M) { host.innerHTML = "<div class='empty'>No missing-value summary.</div>"; return; }
    host.innerHTML = "<p class='sub'>" + fmtInt(M.all) + " features after filtering: " + fmtInt(M.complete) + " complete, " + fmtInt(M.under_half) +
      " with at most 50% missing. The pattern shows every feature with a gap (blue = measured, blank = missing).</p><div class='grid2'><div class='chart card' id='misschart'></div><div class='chart card' id='cumchart'></div></div>";
    const ch = $("#misschart");
    if (!M.rows.length) ch.innerHTML = "<div class='empty'>No missing values.</div>";
    else {
      const W = widthOf(ch, 400) - 10, top = 70, cellW = Math.max(4, Math.floor((W - 10) / nS)), rows = M.rows, rh = Math.max(1, Math.min(4, Math.floor(420 / rows.length)));
      const cv = document.createElement("canvas"), dpr = window.devicePixelRatio || 1, Wc = nS * cellW + 10, Hc = top + rows.length * rh + 4;
      cv.width = Wc * dpr; cv.height = Hc * dpr; cv.style.width = Wc + "px"; cv.style.height = Hc + "px";
      ch.appendChild(cv);
      const ctx = cv.getContext("2d");
      ctx.scale(dpr, dpr);
      ctx.font = "10px system-ui, sans-serif";
      D.samples.forEach((s, j) => {
        ctx.fillStyle = condColor(D.cond[j]);
        ctx.fillRect(j * cellW, top - 8, cellW - 1, 6);
        ctx.save(); ctx.translate(j * cellW + cellW / 2 + 3, top - 11); ctx.rotate(-Math.PI / 3); ctx.fillStyle = css("--text2"); ctx.fillText(s.slice(0, 12), 0, 0); ctx.restore();
      });
      const on = css("--c0"), off = css("--sunk");
      rows.forEach((p, r) => { for (let j = 0; j < nS; j++) { ctx.fillStyle = p[j] === "1" ? on : off; ctx.fillRect(j * cellW, top + r * rh, cellW - (cellW > 5 ? 1 : 0), rh); } });
      cv.onmousemove = (e) => {
        const rc = cv.getBoundingClientRect(), j = Math.floor((e.clientX - rc.left) / cellW), r = Math.floor((e.clientY - rc.top - top) / rh);
        if (j < 0 || j >= nS || r < 0 || r >= rows.length) { hideTip(); return; }
        const i = M.row_ids[r];
        showTip(e, "<b>" + esc(nameOf(i)) + "</b><br>" + esc(D.samples[j]) + ": " + (rows[r][j] === "1" ? "measured" : "missing"));
      };
      cv.onmouseleave = hideTip;
      if (M.total > rows.length) ch.insertAdjacentHTML("beforeend", "<div class='muted'>every " + Math.ceil(M.total / rows.length) + "th of " + fmtInt(M.total) + " rows shown</div>");
    }
    const cc = $("#cumchart"), W = widthOf(cc, 360), H = heightOf(300), L = 56, R = 14, T = 14, B = 44;
    const root = frame(cc, W, H), g = svg("g", {}, root), curve = M.curve;
    const X = (v) => L + v * (W - L - R), Y = (v) => H - B - (v / Math.max(1, M.all)) * (H - T - B);
    axes(g, X, Y, [0, 0.25, 0.5, 0.75, 1], niceTicks(0, M.all, 5), L, R, T, B, W, H, "missing fraction (at most)", "features");
    svg("polyline", { points: curve.map(([x, y]) => X(x) + "," + Y(y)).join(" "), fill: "none", stroke: css("--c1"), "stroke-width": 2.5 }, g);
    svg("line", { x1: X(0.5), x2: X(0.5), y1: T, y2: H - B, stroke: css("--muted"), "stroke-dasharray": "4 4" }, g);
    svgTools(cc, root, "cumulative_missing");
  }
  function qcMNAR(host) {
    const M = D.qc.mnar;
    const verdict = { intensity: "Missing values are mostly low-abundance dropouts (missing not at random). Imputing low values (Perseus-type, MinProb) is appropriate.",
      random: "Features go missing at every abundance (missing at random). Low-value imputation would invent fold changes; kNN or no imputation fits better.",
      mixed: "Partly intensity-dependent: some dropouts are low-abundance, some are not. Check hits that rest on imputed values.", few: "Almost nothing is missing, so imputation hardly matters." }[M.verdict] || "";
    host.innerHTML = "<p class='sub'>Share of samples in which a " + esc(D.levelWord) + " is measured, against its mean measured log2 value (equal-sized bins). " +
      (M.rho != null ? "Spearman ρ = " + fmt(M.rho) + "; complete " + esc(D.levelWord) + "s are " + fmt(M.gap) + " log2 more abundant than incomplete ones. " : "") + "</p>" +
      (verdict ? "<div class='card verdict'>" + esc(verdict) + "</div>" : "") + "<div class='chart card' id='mnarchart' style='max-width:640px'></div>";
    const ch = $("#mnarchart"), W = widthOf(ch, 600), H = heightOf(280), L = 56, R = 14, T = 14, B = 44;
    const bins = M.bins, lo = Math.min(...bins.map((b) => b.mean)), hi = Math.max(...bins.map((b) => b.mean));
    const X = (v) => L + ((v - lo) / (hi - lo || 1)) * (W - L - R), Y = (v) => H - B - v * (H - T - B);
    const root = frame(ch, W, H), g = svg("g", {}, root);
    axes(g, X, Y, niceTicks(lo, hi, 6), [0, 0.25, 0.5, 0.75, 1], L, R, T, B, W, H, "mean measured log2 value", "share of samples measured");
    svg("polyline", { points: bins.map((b) => X(b.mean) + "," + Y(b.detected)).join(" "), fill: "none", stroke: css("--c0"), "stroke-width": 2.2 }, g);
    bins.forEach((b) => title(svg("circle", { cx: X(b.mean), cy: Y(b.detected), r: 4.5, fill: css("--c0") }, g), fmtInt(b.n) + " features around log2 " + fmt(b.mean) + ": measured in " + pct(b.detected) + " of samples"));
    svgTools(ch, root, "missingness_vs_intensity");
  }
  function boxes(host, stats, ttl) {
    const W = widthOf(host), H = heightOf(300), L = 50, R = 10, T = 14, B = 90;
    const root = frame(host, W, H), g = svg("g", {}, root);
    let lo = Infinity, hi = -Infinity;
    stats.forEach((s) => { if (s) { lo = Math.min(lo, s.lo); hi = Math.max(hi, s.hi); } });
    if (!isFinite(lo)) return;
    const pad = (hi - lo) * 0.06 || 1;
    lo -= pad; hi += pad;
    const bw = (W - L - R) / nS, Y = (v) => H - B - ((v - lo) / (hi - lo)) * (H - T - B);
    axes(g, () => 0, Y, [], niceTicks(lo, hi, 5), L, R, T, B, W, H, "", ttl);
    stats.forEach((s, j) => {
      const cx = L + bw * (j + 0.5), w = Math.min(28, bw * 0.6), col = condColor(D.cond[j]);
      const t = text(g, 0, 0, D.samples[j].slice(0, 16), { "font-size": 10.5, transform: "translate(" + (cx + 3) + "," + (H - B + 10) + ") rotate(60)" });
      t.setAttribute("x", 0);
      if (!s) return;
      svg("line", { x1: cx, x2: cx, y1: Y(s.lo), y2: Y(s.hi), stroke: css("--muted") }, g);
      title(svg("rect", { x: cx - w / 2, y: Y(s.q3), width: w, height: Math.max(1, Y(s.q1) - Y(s.q3)), fill: col, "fill-opacity": 0.35, stroke: col }, g), D.samples[j] + "\nmedian " + fmt(s.median) + " · IQR " + fmt(s.q1) + "–" + fmt(s.q3) + " · n " + s.n);
      svg("line", { x1: cx - w / 2, x2: cx + w / 2, y1: Y(s.median), y2: Y(s.median), stroke: css("--text"), "stroke-width": 2 }, g);
    });
    svgTools(host, root, "distributions");
  }
  function qcDist(host) {
    const st = host._st || (host._st = { after: true });
    const hasNorm = D.settings.normalize !== "none";
    host.innerHTML = "<p class='sub'>Measured log2 values per sample" + (hasNorm ? " — toggle to compare before/after " + esc(D.settings.normalize) + " normalisation" : "") +
      ". Medians should line up.</p>" + (hasNorm ? "<div class='row'><button id='dbefore'" + (!st.after ? " class='on'" : "") + ">before</button> <button id='dafter'" + (st.after ? " class='on'" : "") + ">after</button></div>" : "") + legend() + "<div class='chart card' id='distchart'></div>";
    if (hasNorm) { $("#dbefore").onclick = () => { st.after = false; qcDist(host); }; $("#dafter").onclick = () => { st.after = true; qcDist(host); }; }
    boxes($("#distchart"), (st.after || !hasNorm ? D.qc.box_after : D.qc.box_before) || [], D.kind === "ratio" ? "log2 H/L" : "log2 value");
  }
  function qcCV(host) {
    const cv = D.qc.cv || {};
    const conds = Object.keys(cv);
    if (D.kind === "ratio") { host.innerHTML = "<div class='empty'>CVs are for intensities; this data is ratios.</div>"; return; }
    host.innerHTML = "<p class='sub'>Coefficient of variation (sd / mean of un-logged values) per feature within each condition (FragPipe-Analyst plot_cvs). Lower is more reproducible; the dashed line is the median.</p><div class='grid2' id='cvgrid'></div>";
    const grid = $("#cvgrid");
    conds.forEach((c) => {
      if (!cv[c] || !cv[c].hist) return;
      const d = document.createElement("div");
      d.className = "chart card";
      grid.appendChild(d);
      const bins = cv[c].hist, W = widthOf(d, 300), H = heightOf(200), L = 44, R = 8, T = 30, B = 34, ymax = Math.max(1, ...bins);
      const root = frame(d, W, H), g = svg("g", {}, root);
      const X = (v) => L + v * (W - L - R), Y = (v) => H - B - (v / ymax) * (H - T - B);
      axes(g, X, Y, [0, 0.25, 0.5, 0.75, 1], niceTicks(0, ymax, 3), L, R, T, B, W, H, "CV", "");
      bins.forEach((b, k) => svg("rect", { x: X(k / bins.length) + 0.5, y: Y(b), width: (W - L - R) / bins.length - 1, height: H - B - Y(b), fill: condColor(c), "fill-opacity": 0.75 }, g));
      if (cv[c].median != null) svg("line", { x1: X(Math.min(1, cv[c].median)), x2: X(Math.min(1, cv[c].median)), y1: T, y2: H - B, stroke: css("--text"), "stroke-dasharray": "4 3" }, g);
      text(g, 4, 16, c + " · median " + (cv[c].median == null ? "–" : (cv[c].median * 100).toFixed(1) + "%"), { fill: css("--text"), "font-size": 12.5, "font-weight": 600 });
      svgTools(d, root, "cv_" + c);
    });
  }
  /** Per-feature mean and pooled within-condition SD: shows the noise floor and whether variance depends on abundance. */
  function qcMV(host) {
    const groups = {};
    D.cond.forEach((c, j) => (groups[c] = groups[c] || []).push(j));
    const pts = [];
    for (let i = 0; i < nF; i++) {
      let ss = 0, df = 0, sum = 0, n = 0;
      Object.values(groups).forEach((idx) => {
        const obs = idx.map((j) => (isImputed(i, j) ? null : D.v[i][j])).filter((v) => v != null);
        if (obs.length >= 2) { const m = obs.reduce((a, b) => a + b, 0) / obs.length; obs.forEach((v) => (ss += (v - m) ** 2)); df += obs.length - 1; }
        obs.forEach((v) => { sum += v; n++; });
      });
      if (df && n) pts.push([sum / n, Math.sqrt(ss / df), i]);
    }
    if (pts.length < 10) { host.innerHTML = "<div class='empty'>Needs replicates with measured values.</div>"; return; }
    pts.sort((a, b) => a[0] - b[0]);
    host.innerHTML = "<p class='sub'>Pooled within-condition SD of measured values against the mean, for every " + esc(D.levelWord) + ", with a running median. Noise usually rises at low abundance; hits there need bigger fold changes to be real. Median SD " + fmt(median(pts.map((p) => p[1])), 3) + " log2.</p><div class='chart card' id='mvchart'></div>";
    const ch = $("#mvchart"), W = widthOf(ch), H = heightOf(320), L = 56, R = 14, T = 14, B = 44;
    const x0 = pts[0][0], x1 = pts[pts.length - 1][0], sds = pts.map((p) => p[1]).sort((a, b) => a - b), y1 = sds[Math.floor(sds.length * 0.995)] * 1.1 || 1;
    const X = (v) => L + ((v - x0) / (x1 - x0 || 1)) * (W - L - R), Y = (v) => H - B - (Math.min(v, y1) / y1) * (H - T - B);
    const root = frame(ch, W, H), g = svg("g", {}, root);
    axes(g, X, Y, niceTicks(x0, x1, 7), niceTicks(0, y1, 5), L, R, T, B, W, H, "mean log2", "SD (log2)");
    const step = Math.max(1, Math.floor(pts.length / 5000)), c = C(), ns = css("--ns");
    for (let k = 0; k < pts.length; k += step) {
      const [m, s, i] = pts[k], sg = c ? sigOf(c, i) : "";
      svg("circle", { cx: X(m).toFixed(1), cy: Y(s).toFixed(1), r: sg ? 2.8 : 2, fill: sg === "up" ? css("--up") : sg === "down" ? css("--down") : ns, "fill-opacity": sg ? 0.9 : 0.55 }, g);
    }
    const win = Math.max(15, Math.floor(pts.length / 25)), line = [];
    for (let k = 0; k < pts.length; k += Math.max(1, Math.floor(win / 3))) {
      const seg = pts.slice(Math.max(0, k - win), k + win);
      line.push([X(median(seg.map((p) => p[0]))), Y(median(seg.map((p) => p[1])))]);
    }
    svg("polyline", { points: line.map((p) => p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" "), fill: "none", stroke: css("--c1"), "stroke-width": 2.5 }, g);
    svgTools(ch, root, "mean_variance");
  }
  function qcRank(host) {
    const pts = [];
    for (let i = 0; i < nF; i++) {
      const obs = D.v[i].filter((v, j) => v != null && !isImputed(i, j));
      if (obs.length) pts.push([obs.reduce((a, b) => a + b, 0) / obs.length, i]);
    }
    if (!pts.length) { host.innerHTML = "<div class='empty'>No measured values.</div>"; return; }
    pts.sort((a, b) => b[0] - a[0]);
    const c = C();
    const hits = c ? pts.filter((p) => sigOf(c, p[1])).map((p, k) => k) : [];
    host.innerHTML = "<p class='sub'>Every " + esc(D.levelWord) + " ranked by its mean measured log2 value: the dynamic range of the experiment (" + fmt(pts[0][0] - pts[pts.length - 1][0], 1) + " log2 ≈ " + fmt((pts[0][0] - pts[pts.length - 1][0]) * Math.log10(2), 1) + " orders of magnitude). " +
      (c ? "Hits of " + esc(c.name) + " are coloured; search matches ringed. Hits piled at the low end are the ones to double-check." : "") + "</p><div class='chart card' id='rankchart'></div>";
    const ch = $("#rankchart"), W = widthOf(ch), H = heightOf(320), L = 56, R = 14, T = 14, B = 44;
    const y0 = pts[pts.length - 1][0] - 0.5, y1 = pts[0][0] + 0.5;
    const X = (k) => L + (k / Math.max(1, pts.length - 1)) * (W - L - R), Y = (v) => H - B - ((v - y0) / (y1 - y0)) * (H - T - B);
    const root = frame(ch, W, H), g = svg("g", {}, root);
    axes(g, X, Y, niceTicks(0, pts.length, 6), niceTicks(y0, y1, 6), L, R, T, B, W, H, "rank", "mean log2");
    const step = Math.max(1, Math.floor(pts.length / 1500));
    svg("polyline", { points: pts.filter((_, k) => k % step === 0 || k === pts.length - 1).map((p) => X(pts.indexOf(p)).toFixed(1) + "," + Y(p[0]).toFixed(1)).join(" "), fill: "none", stroke: css("--ns"), "stroke-width": 2.5 }, g);
    const screen = [];
    pts.forEach((p, k) => {
      const s = c ? sigOf(c, p[1]) : "", m = anyMark() && matches(p[1]);
      if (!s && !m) return;
      const cx = X(k), cy = Y(p[0]);
      screen.push([p[1], cx, cy]);
      if (s) svg("circle", { cx: cx.toFixed(1), cy: cy.toFixed(1), r: 3, fill: s === "up" ? css("--up") : css("--down") }, g);
      if (m) svg("circle", { cx: cx.toFixed(1), cy: cy.toFixed(1), r: 5.5, fill: "none", stroke: css("--sel"), "stroke-width": 1.3 }, g);
    });
    if (anyMark()) screen.filter((p) => matches(p[0])).slice(0, 25).forEach((p) => text(g, p[1] + 6, p[2] - 5, nameOf(p[0]).slice(0, 16), { "font-size": 11, fill: css("--text") }));
    const hit = svg("rect", { x: L, y: T, width: W - L - R, height: H - T - B, fill: "transparent" }, root);
    hit.addEventListener("mousemove", (e) => {
      const r = root.getBoundingClientRect(), mx = (e.clientX - r.left) * (W / r.width);
      const k = Math.max(0, Math.min(pts.length - 1, Math.round(((mx - L) / (W - L - R)) * (pts.length - 1))));
      showTip(e, "#" + (k + 1) + " <b>" + esc(nameOf(pts[k][1])) + "</b><br>mean log2 " + fmt(pts[k][0]));
    });
    hit.addEventListener("mouseleave", hideTip);
    hit.addEventListener("click", (e) => {
      const r = root.getBoundingClientRect(), mx = (e.clientX - r.left) * (W / r.width);
      const k = Math.max(0, Math.min(pts.length - 1, Math.round(((mx - L) / (W - L - R)) * (pts.length - 1))));
      setFocus(pts[k][1]); goTo("differential");
    });
    svgTools(ch, root, "abundance_rank");
    if (hits.length) {
      const q = median(hits) / pts.length;
      ch.insertAdjacentHTML("beforeend", "<div class='muted'>The median hit sits at " + pct(q) + " of the ranking (50% = no abundance bias).</div>");
    }
  }
  function qcIds(host) {
    const a = D.qc.features_per_sample || [], b = D.qc.features_per_sample_after || [];
    host.innerHTML = "<p class='sub'>" + esc(D.levelTitle) + " measured per sample (FragPipe-Analyst plot_feature_numbers): before filtering (light) and in the analysis (solid).</p>" + legend() + "<div class='chart card' id='idchart'></div>";
    const ch = $("#idchart"), W = widthOf(ch), H = heightOf(300), L = 60, R = 10, T = 14, B = 90;
    const root = frame(ch, W, H), g = svg("g", {}, root), ymax = Math.max(1, ...a);
    const bw = (W - L - R) / nS, Y = (v) => H - B - (v / ymax) * (H - T - B);
    axes(g, () => 0, Y, [], niceTicks(0, ymax, 5), L, R, T, B, W, H, "", "features");
    a.forEach((v, j) => {
      const x = L + bw * j + bw * 0.15, w = bw * 0.7, col = condColor(D.cond[j]);
      svg("rect", { x: x, y: Y(v), width: w, height: H - B - Y(v), fill: col, "fill-opacity": 0.3 }, g);
      title(svg("rect", { x: x, y: Y(b[j] || 0), width: w, height: H - B - Y(b[j] || 0), fill: col }, g), D.samples[j] + ": " + v + " measured, " + b[j] + " in the analysis");
      const t = text(g, 0, 0, D.samples[j].slice(0, 16), { "font-size": 10.5, transform: "translate(" + (x + w / 2 + 3) + "," + (H - B + 10) + ") rotate(60)" });
      t.setAttribute("x", 0);
    });
    const med = median(a);
    if (med) svg("line", { x1: L, x2: W - R, y1: Y(med), y2: Y(med), stroke: css("--muted"), "stroke-dasharray": "4 4" }, g);
    svgTools(ch, root, "identifications");
  }
  function qcImp(host) {
    host.innerHTML = "<p class='sub'>Where the imputed values landed: " + esc(D.imputationLabel) + ". Imputed values should sit at the low end of the measured distribution.</p><div class='chart card' id='impchart'></div>";
    const meas = [], imp = [];
    for (let i = 0; i < nF; i++) for (let j = 0; j < nS; j++) { const v = D.v[i][j]; if (v == null) continue; (isImputed(i, j) ? imp : meas).push(v); }
    let lo = Infinity, hi = -Infinity;
    meas.concat(imp).forEach((v) => { if (v < lo) lo = v; if (v > hi) hi = v; });
    const nb = 40;
    const hist = (xs) => { const b = new Array(nb).fill(0); xs.forEach((v) => b[Math.min(nb - 1, Math.floor(((v - lo) / (hi - lo || 1)) * nb))]++); return b; };
    const hm = hist(meas), hi_ = hist(imp), ymax = Math.max(1, ...hm, ...hi_);
    const ch = $("#impchart"), W = widthOf(ch), H = heightOf(280), L = 56, R = 10, T = 14, B = 44;
    const root = frame(ch, W, H), g = svg("g", {}, root);
    const X = (v) => L + ((v - lo) / (hi - lo || 1)) * (W - L - R), Y = (v) => H - B - (v / ymax) * (H - T - B);
    axes(g, X, Y, niceTicks(lo, hi, 7), niceTicks(0, ymax, 4), L, R, T, B, W, H, "log2 value", "values");
    const bw = (W - L - R) / nb;
    hm.forEach((v, k) => svg("rect", { x: L + k * bw, y: Y(v), width: bw - 1, height: H - B - Y(v), fill: css("--c0"), "fill-opacity": 0.55 }, g));
    hi_.forEach((v, k) => svg("rect", { x: L + k * bw, y: Y(v), width: bw - 1, height: H - B - Y(v), fill: css("--c1"), "fill-opacity": 0.75 }, g));
    ch.insertAdjacentHTML("beforeend", "<div class='legend'><span><span class='sw' style='background:" + css("--c0") + "'></span>measured (" + fmtInt(meas.length) + ")</span><span><span class='sw' style='background:" + css("--c1") + "'></span>imputed (" + fmtInt(imp.length) + ")</span></div>");
    svgTools(ch, root, "imputation");
  }
  function qcPower(host) {
    const P = D.qc.power;
    const a05 = P.curves["0.05"] || [], a001 = P.curves["0.001"] || [];
    const need = (curve) => { const k = curve.findIndex((r) => r && r.q50 <= ST.lfc); return k < 0 ? null : P.n[k]; };
    const n05 = need(a05), n001 = need(a001);
    host.innerHTML = "<p class='sub'>The smallest |log2 fold change| a " + (D.kind === "ratio" ? "one-sample" : "two-group") + " test detects with " + pct(1 - P.beta) + " power, against replicates per group, from this experiment's own noise (SD of a typical " + esc(D.levelWord) + ": " + fmt(P.sd.q50, 3) + " log2" + (P.moderated ? ", moderated with limma's prior" : "") + "). Band: the quieter and the noisier half of the " + esc(D.levelWord) + "s (25th–75th percentile SD).</p>" +
      "<div class='card verdict'>With " + P.current + " per group now, a typical " + esc(D.levelWord) + " needs |log2FC| ≥ " + fmt(((a05[P.n.indexOf(P.current)] || {}).q50), 2) + " (p 0.05) or ≥ " + fmt(((a001[P.n.indexOf(P.current)] || {}).q50), 2) + " (p 0.001, closer to what survives the FDR). " +
      "For the current cut-off (|log2FC| ≥ " + fmt(ST.lfc, 2) + "): " + (n05 ? n05 + " replicates at p 0.05" : "more than " + P.n[P.n.length - 1] + " replicates at p 0.05") + ", " + (n001 ? n001 + " at p 0.001." : "more than " + P.n[P.n.length - 1] + " at p 0.001.") + "</div>" +
      "<div class='chart card' id='powchart' style='max-width:700px'></div>";
    const ch = $("#powchart"), W = widthOf(ch, 640), H = heightOf(300), L = 56, R = 16, T = 16, B = 44;
    let ymax = ST.lfc * 1.2;
    [a05, a001].forEach((cv) => cv.forEach((r) => { if (r) ymax = Math.max(ymax, Math.min(r.q75, 6)); }));
    const X = (n) => L + ((n - P.n[0]) / (P.n[P.n.length - 1] - P.n[0] || 1)) * (W - L - R), Y = (v) => H - B - (Math.min(v, ymax) / ymax) * (H - T - B);
    const root = frame(ch, W, H), g = svg("g", {}, root);
    axes(g, X, Y, P.n, niceTicks(0, ymax, 5), L, R, T, B, W, H, "replicates per group", "detectable |log2FC|");
    [[a001, css("--c1"), "p 0.001"], [a05, css("--c0"), "p 0.05"]].forEach(([cv, col, lab]) => {
      const ok = cv.map((r, k) => [r, P.n[k]]).filter((x) => x[0]);
      if (!ok.length) return;
      svg("polygon", { points: ok.map(([r, n]) => X(n) + "," + Y(r.q25)).concat(ok.slice().reverse().map(([r, n]) => X(n) + "," + Y(r.q75))).join(" "), fill: col, "fill-opacity": 0.14 }, g);
      svg("polyline", { points: ok.map(([r, n]) => X(n) + "," + Y(r.q50)).join(" "), fill: "none", stroke: col, "stroke-width": 2.4 }, g);
      const last = ok[ok.length - 1];
      text(g, X(last[1]) - 4, Y(last[0].q50) - 6, lab, { "text-anchor": "end", fill: col, "font-size": 11.5 });
    });
    svg("line", { x1: L, x2: W - R, y1: Y(ST.lfc), y2: Y(ST.lfc), stroke: css("--muted"), "stroke-dasharray": "4 4" }, g);
    text(g, L + 4, Y(ST.lfc) + 14, "your cut-off (|log2FC| " + fmt(ST.lfc, 2) + ")", { "font-size": 11 });
    if (P.n.includes(P.current)) svg("line", { x1: X(P.current), x2: X(P.current), y1: T, y2: H - B, stroke: css("--accent"), "stroke-dasharray": "2 3" }, g);
    svgTools(ch, root, "power");
  }
  /** Search quality per raw file (psmqc.py): FragPipe's psm.tsv per run, and DIA-NN's own per-run summary as it is. */
  function qcPsm(host) {
    const P = D.qc.psm, runs = P.runs || [], dn = P.diann || [], lim = P.limits || {}, zs = P.z || [];
    const st = qcPsm._st || (qcPsm._st = { chart: "ppm" });
    const has = (r, f) => (r.flags || []).includes(f);
    const cell = (s, bad) => "<td class='n" + (bad ? " zbad" : "") + "'>" + s + "</td>";
    const charts = [["ppm", "Mass error"], ["mc", "Missed cleavages"], ["z", "Charge states"], ["len", "Peptide length"]];
    let h = "<p class='sub'>What the search made of each raw file: the spectra it identified (PSMs), how far the measured precursor masses are from the calculated ones, how complete the digestion was, and the charge states. " +
      "Runs of one experiment should look alike." + (runs.length ? " A run is flagged when its median mass error is " + fmt(lim.ppm, 0) + " ppm or more from 0, or " + pct(lim.missed) + " or more of its PSMs have a missed cleavage (wide limits, not yet the lab's own)." : "") +
      " Also in <a href='psm_qc.tsv'>psm_qc.tsv</a>.</p>" + ((P.notes || []).length ? "<p class='muted'>" + P.notes.map(esc).join("<br>") + "</p>" : "");
    if (runs.length) {
      const samples = runs.some((r) => r.sample && r.sample !== r.run);
      h += "<div class='row'><label class='ctl'>Chart <select id='psmsel'>" + charts.map(([k, t]) => "<option value='" + k + "'" + (k === st.chart ? " selected" : "") + ">" + t + "</option>").join("") + "</select></label></div><div class='chart card' id='psmchart'></div>" +
        "<div class='tablewrap'><table id='psmtable'><thead><tr><th>Run</th>" + (samples ? "<th title='The folder the psm.tsv is in (FragPipe&#39;s experiment)'>Sample</th>" : "") + "<th>PSMs</th><th>Peptides</th><th>Proteins</th>" +
        "<th title='Median precursor mass error: measured minus calculated mass, isotope-corrected'>mass error (ppm)</th><th title='25th to 75th percentile of the mass error'>middle half</th><th title='PSMs with at least one missed cleavage'>missed cleavage</th>" +
        zs.map((z) => "<th title='Share of PSMs with charge " + esc(z) + "'>" + esc(z) + "+</th>").join("") + "<th title='Median peptide length, amino acids'>length</th><th>Flags</th></tr></thead><tbody>" +
        runs.map((r) => "<tr><td>" + esc(r.run) + "</td>" + (samples ? "<td>" + esc(r.sample) + "</td>" : "") + cell(fmtInt(r.psms)) + cell(fmtInt(r.pep)) + cell(fmtInt(r.prot)) +
          cell(r.ppm ? (r.ppm[2] >= 0 ? "+" : "") + fmt(r.ppm[2]) : "–", has(r, "mass error")) + cell(r.ppm ? fmt(r.ppm[1]) + " to " + fmt(r.ppm[3]) : "–") + cell(pct(r.mcRate, 1), has(r, "missed cleavages")) +
          zs.map((_z, k) => cell((r.z || [])[k] == null ? "–" : fmt(r.z[k], 1) + "%")).join("") + cell(fmt(r.len, 0)) + "<td class='desc'>" + esc((r.flags || []).join("; ")) + "</td></tr>").join("") + "</tbody></table></div>";
    }
    if (dn.length) {
      h += "<h3>DIA-NN's own summary per run</h3><p class='muted'>From DIA-NN's stats.tsv, as DIA-NN reports it. Its mass accuracy is DIA-NN's own number (Median.Mass.Acc), not the signed error FragPipe's PSMs give; no run is flagged on it.</p>" +
        "<div class='tablewrap'><table id='psmdiann'><thead><tr><th>Run</th><th>Precursors</th><th>Proteins</th><th>MS1 mass accuracy (ppm)</th><th>MS2 mass accuracy (ppm)</th><th>mean missed cleavages</th><th>mean charge</th><th>peak width (FWHM, min)</th></tr></thead><tbody>" +
        dn.map((r) => "<tr><td>" + esc(r.run) + "</td>" + cell(fmtInt(r.precursors)) + cell(fmtInt(r.proteins)) + cell(fmt(r.ms1_ppm)) + cell(fmt(r.ms2_ppm)) + cell(fmt(r.missed, 3)) + cell(fmt(r.charge)) + cell(fmt(r.fwhm, 3)) + "</tr>").join("") + "</tbody></table></div>";
    }
    host.innerHTML = h;
    if (!runs.length) return;
    $("#psmsel").onchange = (e) => { st.chart = e.target.value; qcPsm(host); };
    const ch = $("#psmchart"), n = runs.length, W = widthOf(ch), H = heightOf(300), L = 56, R = 10, T = 14, B = st.chart === "len" ? 44 : n <= 60 ? 90 : 24;
    const root = frame(ch, W, H), g = svg("g", {}, root), bw = (W - L - R) / n;
    const name = (j) => {  // the run under its bar; too many runs to read: the bar's tooltip names it
      if (n > 60) return;
      const t = text(g, 0, 0, runs[j].run.slice(0, 16), { "font-size": 10.5, transform: "translate(" + (L + bw * (j + 0.5) + 3) + "," + (H - B + 10) + ") rotate(60)" });
      t.setAttribute("x", 0);
    };
    if (st.chart === "ppm") {  // per run: 5th to 95th percentile (line), middle half (box), median
      const ok = runs.filter((r) => r.ppm);
      let lo = Math.min(0, ...ok.map((r) => r.ppm[0])), hi = Math.max(0, ...ok.map((r) => r.ppm[4]));
      const pad = (hi - lo) * 0.08 || 1;
      lo -= pad; hi += pad;
      const Y = (v) => H - B - ((v - lo) / (hi - lo)) * (H - T - B);
      axes(g, () => 0, Y, [], niceTicks(lo, hi, 5), L, R, T, B, W, H, "", "precursor mass error (ppm)");
      svg("line", { x1: L, x2: W - R, y1: Y(0), y2: Y(0), stroke: css("--muted"), "stroke-dasharray": "4 4" }, g);
      runs.forEach((r, j) => {
        name(j);
        if (!r.ppm) return;
        const cx = L + bw * (j + 0.5), w = Math.min(28, bw * 0.6), col = css(has(r, "mass error") ? "--up" : "--c0"), q = r.ppm;
        svg("line", { x1: cx, x2: cx, y1: Y(q[0]), y2: Y(q[4]), stroke: css("--muted") }, g);
        title(svg("rect", { x: cx - w / 2, y: Y(q[3]), width: w, height: Math.max(1, Y(q[1]) - Y(q[3])), fill: col, "fill-opacity": 0.35, stroke: col, "data-run": j }, g),
          r.run + "\nmedian " + fmt(q[2]) + " ppm · middle half " + fmt(q[1]) + " to " + fmt(q[3]) + " · 5th to 95th percentile " + fmt(q[0]) + " to " + fmt(q[4]) + " · " + fmtInt(r.ppmN) + " PSMs");
        svg("line", { x1: cx - w / 2, x2: cx + w / 2, y1: Y(q[2]), y2: Y(q[2]), stroke: css("--text"), "stroke-width": 2 }, g);
      });
    } else if (st.chart === "len") {  // every run together
      const bins = (P.len && P.len.n) || [], lo = (P.len && P.len.lo) || 0, m = Math.max(1, bins.length), ymax = Math.max(1, ...bins), w = (W - L - R) / m;
      const X = (v) => L + (v - lo + 0.5) * w, Y = (v) => H - B - (v / ymax) * (H - T - B);
      axes(g, X, Y, niceTicks(lo, lo + m - 1, 8).filter((v) => v >= lo && v < lo + m && v === Math.round(v)), niceTicks(0, ymax, 4), L, R, T, B, W, H, "peptide length (amino acids), all runs", "PSMs");
      bins.forEach((v, k) => title(svg("rect", { x: X(lo + k) - w / 2 + 0.5, y: Y(v), width: Math.max(1, w - 1), height: H - B - Y(v), fill: css("--c0"), "fill-opacity": 0.75 }, g), (lo + k) + " amino acids: " + fmtInt(v) + " PSMs"));
    } else {  // stacked shares of each run's PSMs
      const mc = st.chart === "mc", names = mc ? ["no missed cleavage", "1", "2 or more"] : zs.map((z) => z + "+");
      const Y = (v) => H - B - v * (H - T - B);
      axes(g, () => 0, Y, [], [0, 0.25, 0.5, 0.75, 1], L, R, T, B, W, H, "", "share of PSMs");
      runs.forEach((r, j) => {
        name(j);
        const tot = mc && r.mc ? r.mc[0] + r.mc[1] + r.mc[2] : 0;
        const share = mc ? (tot ? r.mc.map((v) => v / tot) : []) : (r.z || []).map((v) => (v || 0) / 100);
        let at = 0;
        share.forEach((v, k) => {
          title(svg("rect", { x: L + bw * j + bw * 0.15, y: Y(at + v), width: bw * 0.7, height: Math.max(0, Y(at) - Y(at + v)), fill: css("--c" + (k % 8)), "data-k": k }, g), r.run + ": " + names[k] + " · " + pct(v, 1));
          at += v;
        });
      });
      ch.insertAdjacentHTML("beforeend", "<div class='legend'>" + names.map((s, k) => "<span><span class='sw' style='background:" + css("--c" + (k % 8)) + "'></span>" + esc(s) + "</span>").join("") + "</div>");
    }
    svgTools(ch, root, "search_quality_" + st.chart);
  }

  // -------------------------------------------------------- dose-response
  // D.dose (doseresponse.py report_payload): per series the curves as columns (i = feature, cls, pec50, ciL/ciR,
  // ec50, slope, front, back, fc, p, q, rel, r2, n) and y = each curve's log2 ratios to the control per series
  // sample (samples, sdose: molar, 0 = control). The fit is CurveCurator's; the page only draws it.
  const DS = { s: 0, cls: "all", q: "", sortKey: "rel", sortDir: -1, page: 0, focus: null };
  const DOSE_CLS = ["up", "down", "not", "unclear"];
  const MOLAR = { pM: 1e-12, nM: 1e-9, "µM": 1e-6, mM: 1e-3, M: 1 };
  const doseColor = (c) => css(c === "up" ? "--up" : c === "down" ? "--down" : c === "not" ? "--ns" : "--muted");
  function fmtDose(molar, unit) {  // the unit that keeps the number >= 1
    unit = unit || ["M", "mM", "µM", "nM"].find((u) => molar >= MOLAR[u] * (1 - 1e-9)) || "pM";
    return String(+(molar / MOLAR[unit]).toPrecision(3)) + " " + unit;
  }
  function doseSeries() { const X = D.dose; return X && X.ran && X.series && X.series.length ? X.series[Math.min(DS.s, X.series.length - 1)] : null; }
  function doseCurve(S, k, x) { return (S.front[k] - S.back[k]) / (1 + Math.pow(10, S.slope[k] * (x + S.pec50[k]))) + S.back[k]; }
  const DOSE_COLS = [
    { k: "name", t: D.kind === "ratio" ? "Site" : "Gene", f: (S, k) => esc(nameOf(S.i[k])) },
    { k: "cls", t: "class", f: (S, k) => "<span class='dot' style='background:" + doseColor(S.cls[k]) + "'></span> " + esc(S.cls[k]) },
    { k: "pec50", t: "pEC50", n: 1, f: (S, k) => fmt(S.pec50[k]) },
    { k: "ciL", t: "95% CI", n: 1, f: (S, k) => (S.ciL[k] == null ? "–" : fmt(S.ciL[k]) + " – " + fmt(S.ciR[k])) },
    { k: "ec50", t: "EC50", n: 1, f: (S, k) => (S.ec50[k] == null ? "–" : +S.ec50[k].toPrecision(3) + " " + esc(S.unit)) },
    { k: "fc", t: "log2 FC", n: 1, f: (S, k) => fmt(S.fc[k]) },
    { k: "p", t: "p", n: 1, f: (S, k) => fmtP(S.p[k]) },
    { k: "q", t: "q (BH)", n: 1, f: (S, k) => fmtP(S.q[k]) },
    { k: "rel", t: "relevance", n: 1, f: (S, k) => fmt(S.rel[k]) },
    { k: "r2", t: "R²", n: 1, f: (S, k) => fmt(S.r2[k]) },
    { k: "n", t: "points", n: 1, f: (S, k) => String(S.n[k]) },
  ];
  function doseRows(S, withText) {
    const q = withText ? DS.q.trim().toLowerCase() : "";
    const out = [];
    for (let k = 0; k < S.i.length; k++) {
      if (DS.cls !== "all" && S.cls[k] !== DS.cls) continue;
      if (q) {
        const i = S.i[k], t = ((D.f.label[i] || "") + " " + (D.f.id[i] || "") + " " + (D.f.desc[i] || "")).toLowerCase();
        if (!t.includes(q)) continue;
      }
      out.push(k);
    }
    const key = DS.sortKey, val = (k) => (key === "name" ? nameOf(S.i[k]).toLowerCase() : key === "cls" ? DOSE_CLS.indexOf(S.cls[k]) : S[key][k]);
    out.sort((a, b) => {
      const x = val(a), y = val(b);
      if (x == null || y == null) return x == null ? (y == null ? 0 : 1) : -1;
      return (x > y ? 1 : x < y ? -1 : 0) * DS.sortDir;
    });
    return out;
  }
  function renderDose() {
    const host = $("#dosebody");
    if (!host) return;
    const X = D.dose || {};
    const S = doseSeries();
    if (X.ran || X.found) ["#dose", "#navdose"].forEach((s) => { const e = $(s); if (e) e.hidden = false; });
    if (!S) { host.innerHTML = "<div class='empty'>" + esc(X.reason || "No dose-response curves were fitted.") + "</div>"; return; }
    DS.s = X.series.indexOf(S);
    const count = {};
    S.cls.forEach((c) => (count[c] = (count[c] || 0) + 1));
    let h = "<div class='row'>";
    if (X.series.length > 1) h += "<label class='ctl'>Compound <select id='dseries'>" + X.series.map((x, k) => "<option value='" + k + "'" + (k === DS.s ? " selected" : "") + ">" + esc(x.name || "dose-response") + "</option>").join("") + "</select></label>";
    h += "<label class='ctl'>Class <select id='dcls'>" + ["all"].concat(DOSE_CLS).map((c) => "<option value='" + c + "'" + (c === DS.cls ? " selected" : "") + ">" + c + " (" + (c === "all" ? S.i.length : count[c] || 0) + ")</option>").join("") + "</select></label>" +
      "<input type='search' id='dq' placeholder='Find a curve' aria-label='Find a curve' value='" + esc(DS.q) + "'> <button id='dcsv'>Download CSV</button>" +
      "<span class='muted'>" + S.doses.length + " doses, " + esc(fmtDose(S.doses[0])) + " – " + esc(fmtDose(S.doses[S.doses.length - 1])) +
      (S.controls && S.controls.length ? " · control " + esc(S.controls.join(", ")) : " · ratios to 1") + " · alpha " + X.alpha + ", |log2 FC| ≥ " + X.fcLim +
      (S.skipped ? " · " + fmtInt(S.skipped) + " not fitted (too few doses measured)" : "") + "</span></div>" +
      "<div id='dosehl' class='muted'></div><div class='split'><div><div class='card chart' id='dosescatter'></div><div class='legend'>" + DOSE_CLS.map((c) => "<span><span class='sw' style='background:" + doseColor(c) + "'></span>" + c + "</span>").join("") + "</div></div>" +
      "<div class='card detail'><div id='dosehead'></div><div class='chart' id='dosecurve'></div></div></div><div id='dosetable'></div>";
    host.innerHTML = h;
    const ser = $("#dseries");
    if (ser) ser.onchange = (e) => { DS.s = +e.target.value; DS.focus = null; DS.page = 0; renderDose(); };
    $("#dcls").onchange = (e) => { DS.cls = e.target.value; DS.page = 0; safe(renderDoseScatter, "#dosescatter"); safe(renderDoseTable, "#dosetable"); };
    $("#dq").oninput = (e) => { DS.q = e.target.value; DS.page = 0; safe(renderDoseTable, "#dosetable"); };
    $("#dcsv").onclick = () => exportDoseCSV(S);
    if (DS.focus == null || DS.focus >= S.i.length) DS.focus = S.i.length ? 0 : null;
    const marked = anyMark() ? S.i.filter((i) => matches(i)).length : 0;
    $("#dosehl").textContent = anyMark() ? marked + " of these curves match the search (ringed)" : "";
    safe(renderDoseScatter, "#dosescatter");
    safe(renderDoseCurve, "#dosecurve");
    safe(renderDoseTable, "#dosetable");
  }
  function doseFocus(k) { DS.focus = k; safe(renderDoseScatter, "#dosescatter"); safe(renderDoseCurve, "#dosecurve"); safe(renderDoseTable, "#dosetable"); }
  // the report's search marks curves too, and the first match is drawn
  function doseOnSearch() {
    const S = doseSeries();
    if (!S) return;
    if (anyMark()) { const k = S.i.findIndex((i) => matches(i)); if (k >= 0) DS.focus = k; }
    renderDose();
  }
  function renderDoseScatter() {
    const S = doseSeries(), host = $("#dosescatter");
    if (!S || !host) return;
    const ks = doseRows(S, false).filter((k) => S.pec50[k] != null && S.fc[k] != null);
    const W = widthOf(host, 700), H = heightOf(380), L = 56, R = 16, T = 16, B = 44;
    const root = frame(host, W, H);
    if (!ks.length) { text(root, W / 2, H / 2, "No curves in this class", { "text-anchor": "middle" }); return; }
    const xs = ks.map((k) => S.pec50[k]), ys = ks.map((k) => S.fc[k]).concat([D.dose.fcLim, -D.dose.fcLim]);
    let x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs), y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
    const px = (x1 - x0) * 0.05 || 0.5, py = (y1 - y0) * 0.06 || 0.5;
    x0 -= px; x1 += px; y0 -= py; y1 += py;
    const X = (v) => L + ((v - x0) / (x1 - x0)) * (W - L - R), Y = (v) => T + (1 - (v - y0) / (y1 - y0)) * (H - T - B);
    const g = svg("g", {}, root);
    axes(g, X, Y, niceTicks(x0, x1, 7), niceTicks(y0, y1, 6), L, R, T, B, W, H, "pEC50 (−log10 M; higher = more potent)", "log2 curve fold change");
    const muted = css("--muted");
    [-D.dose.fcLim, D.dose.fcLim].forEach((v) => svg("line", { x1: L, x2: W - R, y1: Y(v), y2: Y(v), stroke: muted, "stroke-dasharray": "4 4" }, g));
    const order = ks.slice().sort((a, b) => (S.cls[a] === "up" || S.cls[a] === "down" ? 1 : 0) - (S.cls[b] === "up" || S.cls[b] === "down" ? 1 : 0));
    const mark = anyMark(), sel = css("--sel");
    order.forEach((k) => {
      const reg = S.cls[k] === "up" || S.cls[k] === "down", hit = mark && matches(S.i[k]);
      const c = svg("circle", { cx: X(S.pec50[k]), cy: Y(S.fc[k]), r: reg ? 3.6 : 2.6, fill: doseColor(S.cls[k]), "fill-opacity": reg ? 0.85 : 0.45,
        stroke: hit ? sel : null, "stroke-width": hit ? 1.6 : null, "data-k": k, style: "cursor:pointer" }, g);
      c.onmouseenter = (e) => showTip(e, "<b>" + esc(nameOf(S.i[k])) + "</b> · " + esc(S.cls[k]) + "<br>pEC50 " + fmt(S.pec50[k]) + " · log2 FC " + fmt(S.fc[k]) + " · relevance " + fmt(S.rel[k]));
      c.onmouseleave = hideTip;
      c.onclick = () => doseFocus(k);
    });
    if (DS.focus != null && S.pec50[DS.focus] != null && S.fc[DS.focus] != null)
      svg("circle", { cx: X(S.pec50[DS.focus]), cy: Y(S.fc[DS.focus]), r: 6.5, fill: "none", stroke: sel, "stroke-width": 2 }, g);
    svgTools(host, root, "dose_potency_" + (S.name || "curves"));
  }
  function renderDoseCurve() {
    const S = doseSeries(), host = $("#dosecurve"), head = $("#dosehead");
    if (!S || !host) return;
    const k = DS.focus;
    if (k == null) { host.innerHTML = "<div class='empty'>Click a curve.</div>"; if (head) head.innerHTML = ""; return; }
    const i = S.i[k];
    if (head) head.innerHTML = "<h4>" + esc(nameOf(i)) + " <span class='badge' style='color:" + doseColor(S.cls[k]) + ";border-color:" + doseColor(S.cls[k]) + "'>" + esc(S.cls[k]) + "</span></h4>" +
      "<div class='id'>" + esc(D.f.id[i] || "") + "</div>" + (D.f.desc[i] ? "<div class='desc'>" + esc(D.f.desc[i].slice(0, 120)) + "</div>" : "") +
      "<div class='meta'>pEC50 " + fmt(S.pec50[k]) + (S.ciL[k] != null ? " (" + fmt(S.ciL[k]) + " – " + fmt(S.ciR[k]) + ")" : "") +
      (S.ec50[k] != null ? " · EC50 " + +S.ec50[k].toPrecision(3) + " " + esc(S.unit) : "") + "<br>log2 FC " + fmt(S.fc[k]) + " · slope " + fmt(S.slope[k]) +
      " · p " + fmtP(S.p[k]) + " · relevance " + fmt(S.rel[k]) + "</div>" +
      (S.cls[k] === "up" || S.cls[k] === "down" ? "" : "<div class='muted'>Not a regulated curve: don't read its pEC50.</div>");
    const lx = S.doses.map((d) => Math.log10(d));
    const xa = Math.min.apply(null, lx) - 0.5, xb = Math.max.apply(null, lx) + 0.5, gap = Math.max(0.6, (xb - xa) * 0.12), xc = xa - gap;
    const pts = [];
    S.samples.forEach((_j, jj) => {
      const v = S.y[k][jj];
      if (v != null) pts.push([S.sdose[jj] > 0 ? Math.log10(S.sdose[jj]) : xc, Math.pow(2, v), S.sdose[jj] === 0]);
    });
    const W = widthOf(host, 320), H = heightOf(260), L = 44, R = 10, T = 12, B = 40;
    const ymax = Math.max(1.4, S.front[k], S.back[k], ...pts.map((p) => p[1])) * 1.08;
    const X = (v) => L + ((v - (xc - gap * 0.4)) / (xb - (xc - gap * 0.4))) * (W - L - R), Y = (v) => T + (1 - v / ymax) * (H - T - B);
    const root = frame(host, W, H), g = svg("g", {}, root);
    const xt = [];
    for (let v = Math.ceil(xa); v <= Math.floor(xb); v++) xt.push(v);
    axes(g, X, Y, [], niceTicks(0, ymax, 5), L, R, T, B, W, H, "dose", "ratio to control");
    xt.forEach((v) => { svg("line", { x1: X(v), x2: X(v), y1: T, y2: H - B, stroke: css("--grid") }, g); text(g, X(v), H - B + 16, fmtDose(Math.pow(10, v)).replace(/^(\S+) /, "$1 "), { "text-anchor": "middle", "font-size": 10.5 }); });
    text(g, X(xc), H - B + 16, "ctrl", { "text-anchor": "middle", "font-size": 10.5 });
    const bx = X(xc + gap / 2);
    svg("path", { d: "M" + (bx - 4) + " " + (H - B + 4) + "l4 -8m0 8l4 -8", stroke: css("--axis"), fill: "none" }, g);
    const muted = css("--muted");
    svg("line", { x1: L, x2: W - R, y1: Y(1), y2: Y(1), stroke: muted, "stroke-dasharray": "3 3" }, g);
    if (-S.pec50[k] >= xa && -S.pec50[k] <= xb) svg("line", { x1: X(-S.pec50[k]), x2: X(-S.pec50[k]), y1: T, y2: H - B, stroke: muted, "stroke-dasharray": "2 4" }, g);
    const col = doseColor(S.cls[k]);
    let d = "";
    for (let n = 0; n <= 120; n++) { const x = xa + ((xb - xa) * n) / 120; d += (n ? "L" : "M") + X(x).toFixed(1) + " " + Y(Math.max(0, Math.min(ymax, doseCurve(S, k, x)))).toFixed(1); }
    svg("path", { d: d, fill: "none", stroke: col, "stroke-width": 2 }, g);
    svg("line", { x1: X(xc) - 10, x2: X(xc) + 10, y1: Y(S.front[k]), y2: Y(S.front[k]), stroke: col, "stroke-width": 2, "stroke-opacity": 0.6 }, g);
    pts.forEach((p) => title(svg("circle", { cx: X(p[0]), cy: Y(Math.min(p[1], ymax)), r: 3.4, fill: p[2] ? "none" : css("--text2"), stroke: css("--text2"), "stroke-width": 1.2 }, g),
      (p[2] ? "control" : fmtDose(Math.pow(10, p[0]))) + ": ratio " + fmt(p[1])));
    svgTools(host, root, "dose_curve_" + nameOf(i));
  }
  function renderDoseTable() {
    const S = doseSeries(), host = $("#dosetable");
    if (!S || !host) return;
    const rows = doseRows(S, true), per = 50, pages = Math.max(1, Math.ceil(rows.length / per));
    DS.page = Math.min(DS.page, pages - 1);
    const mark = anyMark();
    let h = "<div class='tablewrap'><table><thead><tr>" + DOSE_COLS.map((c) => "<th data-k='" + c.k + "'" + (c.k === DS.sortKey ? " data-dir='" + (DS.sortDir > 0 ? "asc" : "desc") + "'" : "") + ">" + esc(c.t) + "</th>").join("") + "</tr></thead><tbody>";
    rows.slice(DS.page * per, DS.page * per + per).forEach((k) => {
      const hit = mark && matches(S.i[k]);
      h += "<tr data-k='" + k + "'" + (k === DS.focus ? " class='focus'" : "") + (hit ? " style='font-weight:650'" : "") + ">" + DOSE_COLS.map((c) => "<td" + (c.n ? " class='n'" : "") + ">" + c.f(S, k) + "</td>").join("") + "</tr>";
    });
    if (!rows.length) h += "<tr><td colspan='" + DOSE_COLS.length + "' class='muted'>No curve matches.</td></tr>";
    host.innerHTML = h + "</tbody></table></div><div class='pager'>" + fmtInt(rows.length) + " curves · page " + (DS.page + 1) + " of " + pages + " <button id='dprev'>‹</button><button id='dnext'>›</button></div>";
    $$("th", host).forEach((th) => (th.onclick = () => { if (DS.sortKey === th.dataset.k) DS.sortDir *= -1; else { DS.sortKey = th.dataset.k; DS.sortDir = th.dataset.k === "name" || th.dataset.k === "cls" || th.dataset.k === "p" || th.dataset.k === "q" ? 1 : -1; } DS.page = 0; renderDoseTable(); }));
    $$("tbody tr[data-k]", host).forEach((tr) => (tr.onclick = () => doseFocus(+tr.dataset.k)));
    $("#dprev").onclick = () => { DS.page = Math.max(0, DS.page - 1); renderDoseTable(); };
    $("#dnext").onclick = () => { DS.page = Math.min(pages - 1, DS.page + 1); renderDoseTable(); };
  }
  function exportDoseCSV(S) {
    const q = (v) => '"' + String(v == null ? "" : v).replace(/"/g, '""') + '"';
    const lines = [["series", "id", "label", "class", "pEC50", "pEC50_ci_low", "pEC50_ci_high", "EC50", "unit", "log2_curve_fold_change", "p", "q", "relevance", "R2", "points"].join(",")];
    doseRows(S, true).forEach((k) => {
      const i = S.i[k];
      lines.push([S.name, D.f.id[i], D.f.label[i], S.cls[k], S.pec50[k], S.ciL[k], S.ciR[k], S.ec50[k], S.unit, S.fc[k], S.p[k], S.q[k], S.rel[k], S.r2[k], S.n[k]].map(q).join(","));
    });
    download("dose_response_" + (S.name || "curves") + ".csv", lines.join("\n") + "\n", "text/csv");
  }

  // ----------------------------------------------------------- time course
  // D.time (timecourse.py report_payload): per series the tested features as columns (i = feature, cls, pat, F, p,
  // q, tt / tq = trend, max, peak, fc = log2 fold change against the first time point per time) and the series'
  // samples with their times. The tests are limma's, made in Python; the page draws them.
  const TS = { s: 0, show: "changing", pat: 0, q: "", sortKey: "p", sortDir: 1, page: 0, focus: null };
  const TIME_CLS = ["up", "down", "mixed", "not"];
  const timeColor = (c) => css(c === "up" ? "--up" : c === "down" ? "--down" : c === "mixed" ? "--c3" : "--ns");
  function timeSeries() { const X = D.time; return X && X.ran && X.series && X.series.length ? X.series[Math.min(TS.s, X.series.length - 1)] : null; }
  const TIME_COLS = [
    { k: "name", t: D.kind === "ratio" ? "Site" : "Gene", f: (S, k) => esc(nameOf(S.i[k])) },
    { k: "cls", t: "class", f: (S, k) => "<span class='dot' style='background:" + timeColor(S.cls[k]) + "'></span> " + esc(S.cls[k]) },
    { k: "pat", t: "pattern", n: 1, f: (S, k) => (S.pat[k] == null ? "" : String(S.pat[k])) },
    { k: "F", t: "F", n: 1, f: (S, k) => fmt(S.F[k]) },
    { k: "p", t: "p", n: 1, f: (S, k) => fmtP(S.p[k]) },
    { k: "q", t: "adj. p", n: 1, f: (S, k) => fmtP(S.q[k]) },
    { k: "tt", t: "trend t", n: 1, f: (S, k) => fmt(S.tt[k]) },
    { k: "tq", t: "trend adj. p", n: 1, f: (S, k) => fmtP(S.tq[k]) },
    { k: "max", t: "largest log2FC", n: 1, f: (S, k) => fmt(S.max[k]) },
    { k: "peak", t: "at", f: (S, k) => esc(S.labels[S.peak[k]]) },
  ];
  const timeCols = (S) => TIME_COLS.concat(S.iq ? [{ k: "iq", t: "differs from " + S.vs + " adj. p", n: 1, f: (S2, k) => fmtP(S2.iq[k]) }] : []);
  function timeRows(S, withText) {
    const X = D.time, q = withText ? TS.q.trim().toLowerCase() : "", out = [];
    for (let k = 0; k < S.i.length; k++) {
      const c = S.cls[k];
      if (TS.show === "changing" && c === "not") continue;
      if (TS.show === "differs" && !(S.iq && S.iq[k] != null && S.iq[k] <= X.alpha)) continue;
      if (TIME_CLS.includes(TS.show) && c !== TS.show) continue;
      if (TS.pat && S.pat[k] !== TS.pat) continue;
      if (q) {
        const i = S.i[k], t = ((D.f.label[i] || "") + " " + (D.f.id[i] || "") + " " + (D.f.desc[i] || "")).toLowerCase();
        if (!t.includes(q)) continue;
      }
      out.push(k);
    }
    const key = TS.sortKey, val = (k) => (key === "name" ? nameOf(S.i[k]).toLowerCase() : key === "cls" ? TIME_CLS.indexOf(S.cls[k]) : key === "max" || key === "tt" ? (S[key][k] == null ? null : Math.abs(S[key][k])) : S[key] ? S[key][k] : null);
    out.sort((a, b) => {
      const x = val(a), y = val(b);
      if (x == null || y == null) return x == null ? (y == null ? 0 : 1) : -1;
      return (x > y ? 1 : x < y ? -1 : 0) * TS.sortDir;
    });
    return out;
  }
  function renderTime() {
    const host = $("#timebody");
    if (!host) return;
    const X = D.time || {}, S = timeSeries();
    if (X.ran || X.found) ["#time", "#navtime"].forEach((s) => { const e = $(s); if (e) e.hidden = false; });
    if (!S) { host.innerHTML = "<div class='empty'>" + esc(X.reason || "No time course was found.") + "</div>"; return; }
    TS.s = X.series.indexOf(S);
    if (TS.show === "differs" && !S.iq) TS.show = "changing";
    if (TS.pat > S.patterns.length) TS.pat = 0;
    const count = { changing: 0 };
    S.cls.forEach((c) => { count[c] = (count[c] || 0) + 1; if (c !== "not") count.changing++; });
    const differ = S.iq ? S.iq.filter((v) => v != null && v <= X.alpha).length : 0;
    const opts = [["changing", "changing (" + count.changing + ")"]].concat(TIME_CLS.map((c) => [c, c + " (" + (count[c] || 0) + ")"]), [["all", "all tested (" + S.i.length + ")"]],
      S.iq ? [["differs", "differs from " + S.vs + " (" + differ + ")"]] : []);
    let h = "<div class='row'>";
    if (X.series.length > 1) h += "<label class='ctl'>Series <select id='tseries'>" + X.series.map((x, k) => "<option value='" + k + "'" + (k === TS.s ? " selected" : "") + ">" + esc(x.name || "time course") + "</option>").join("") + "</select></label>";
    h += "<label class='ctl'>Show <select id='tshow'>" + opts.map((o) => "<option value='" + o[0] + "'" + (o[0] === TS.show ? " selected" : "") + ">" + esc(o[1]) + "</option>").join("") + "</select></label>" +
      "<input type='search' id='tq' placeholder='Find a feature' aria-label='Find a feature' value='" + esc(TS.q) + "'>" +
      "<span class='muted'>" + S.labels.length + " time points: " + esc(S.labels.join(", ")) + " · changing: F adj. p ≤ " + X.alpha + " and |log2FC| ≥ " + X.lfc + " against " + esc(S.labels[0]) +
      (S.untested ? " · " + fmtInt(S.untested) + " not tested (not measured at every time point)" : "") + " · <a href='time_course.tsv'>time_course.tsv</a></span></div>" +
      "<div id='timepatterns' class='tiles'></div><div id='timehl' class='muted'></div>" +
      "<div class='split'><div id='timetable'></div><div class='card detail'><div id='timehead'></div><div class='chart' id='timeprofile'></div></div></div>";
    host.innerHTML = h;
    const ser = $("#tseries");
    if (ser) ser.onchange = (e) => { TS.s = +e.target.value; TS.focus = null; TS.pat = 0; TS.page = 0; renderTime(); };
    $("#tshow").onchange = (e) => { TS.show = e.target.value; TS.page = 0; safe(renderTimeTable, "#timetable"); };
    $("#tq").oninput = (e) => { TS.q = e.target.value; TS.page = 0; safe(renderTimeTable, "#timetable"); };
    if (TS.focus == null || TS.focus >= S.i.length) TS.focus = S.i.length ? 0 : null;
    const marked = anyMark() ? S.i.filter((i) => matches(i)).length : 0;
    $("#timehl").textContent = anyMark() ? marked + " of these features match the search (bold)" : "";
    safe(renderTimePatterns, "#timepatterns");
    safe(renderTimeTable, "#timetable");
    safe(renderTimeProfile, "#timeprofile");
  }
  function timeOnSearch() {
    const S = timeSeries();
    if (!S) return;
    if (anyMark()) { const k = S.i.findIndex((i) => matches(i)); if (k >= 0) TS.focus = k; }
    renderTime();
  }
  /** One small chart per pattern: the median log2 fold change of its features at each time point. */
  function renderTimePatterns() {
    const S = timeSeries(), host = $("#timepatterns");
    if (!S || !host) return;
    host.innerHTML = "";
    if (!S.patterns.length) { host.innerHTML = "<div class='muted'>No feature changes over time at these cut-offs.</div>"; return; }
    const lim = Math.max.apply(null, S.patterns.map((p) => Math.max.apply(null, p.profile.map((v) => Math.abs(v || 0))))) || 1;
    S.patterns.forEach((p, j) => {
      const card = document.createElement("div");
      card.className = "tile";
      card.dataset.pat = j + 1;
      card.style.cursor = "pointer";
      if (TS.pat === j + 1) card.style.borderColor = css("--accent");
      card.innerHTML = "<div class='k'>Pattern " + (j + 1) + " · " + fmtInt(p.n) + " feature" + (p.n === 1 ? "" : "s") + "</div>";
      const W = 170, H = 70, n = p.profile.length;
      const root = svg("svg", { viewBox: "0 0 " + W + " " + H, width: W, height: H, role: "img" }, card);
      title(root, "Pattern " + (j + 1) + ": median log2 fold change " + p.profile.map((v, k) => S.labels[k] + " " + fmt(v)).join(", "));
      const X = (k) => 8 + (k / Math.max(1, n - 1)) * (W - 16), Y = (v) => H / 2 - (v / lim) * (H / 2 - 8);
      svg("line", { x1: 8, x2: W - 8, y1: Y(0), y2: Y(0), stroke: css("--axis") }, root);
      const up = p.profile[p.profile.reduce((a, v, k) => (Math.abs(v) > Math.abs(p.profile[a]) ? k : a), 0)] >= 0;
      svg("path", { d: p.profile.map((v, k) => (k ? "L" : "M") + X(k).toFixed(1) + " " + Y(v).toFixed(1)).join(""), fill: "none", stroke: css(up ? "--up" : "--down"), "stroke-width": 2 }, root);
      p.profile.forEach((v, k) => svg("circle", { cx: X(k), cy: Y(v), r: 2.4, fill: css(up ? "--up" : "--down") }, root));
      card.onclick = () => { TS.pat = TS.pat === j + 1 ? 0 : j + 1; if (TS.show === "not") TS.show = "changing"; TS.page = 0; renderTime(); };
      host.appendChild(card);
    });
  }
  function timeFocus(k) { TS.focus = k; safe(renderTimeTable, "#timetable"); safe(renderTimeProfile, "#timeprofile"); }
  /** The focused feature over time: every replicate, the mean per time point, and the control series' mean. */
  function renderTimeProfile() {
    const X = D.time, S = timeSeries(), host = $("#timeprofile"), head = $("#timehead");
    if (!S || !host) return;
    const k = TS.focus;
    if (k == null) { host.innerHTML = "<div class='empty'>Click a row to draw a feature over time.</div>"; if (head) head.innerHTML = ""; return; }
    const i = S.i[k];
    if (head) head.innerHTML = "<h4>" + esc(nameOf(i)) + " <span class='badge' style='color:" + timeColor(S.cls[k]) + ";border-color:" + timeColor(S.cls[k]) + "'>" + esc(S.cls[k]) + "</span></h4>" +
      "<div class='muted'>F " + fmt(S.F[k]) + " · adj. p " + fmtP(S.q[k]) + " · largest change " + fmt(S.max[k]) + " at " + esc(S.labels[S.peak[k]]) + (S.iq && S.iq[k] != null ? " · differs from " + esc(S.vs) + ": adj. p " + fmtP(S.iq[k]) : "") + "</div>";
    const pointsOf = (Q) => Q.samples.map((j, a) => [Q.times.indexOf(Q.stime[a]), D.v[i] ? D.v[i][j] : null, j]).filter((p) => p[1] != null && p[0] >= 0);
    const meansOf = (Q, pts) => Q.times.map((_t, a) => { const v = pts.filter((p) => p[0] === a).map((p) => p[1]); return v.length ? v.reduce((x, y) => x + y, 0) / v.length : null; });
    const pts = pointsOf(S), means = meansOf(S, pts);
    const other = S.vs ? X.series.find((q) => q.name === S.vs) : null;
    // the control series on this series' time axis (the shared time points)
    const opts = other ? pointsOf(other).map((p) => [S.times.indexOf(other.times[p[0]]), p[1], p[2]]).filter((p) => p[0] >= 0) : [];
    const omeans = other ? S.times.map((_t, a) => { const v = opts.filter((p) => p[0] === a).map((p) => p[1]); return v.length ? v.reduce((x, y) => x + y, 0) / v.length : null; }) : [];
    const W = widthOf(host, 420), H = heightOf(300), L = 52, R = 14, T = 14, B = 44;
    const root = frame(host, W, H);
    const ys = pts.concat(opts).map((p) => p[1]);
    if (!ys.length) { text(root, W / 2, H / 2, "No measured values", { "text-anchor": "middle" }); return; }
    let y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
    const py = (y1 - y0) * 0.1 || 0.5;
    y0 -= py; y1 += py;
    const n = S.times.length, Xs = (a) => L + 14 + (a / Math.max(1, n - 1)) * (W - L - R - 28), Y = (v) => T + (1 - (v - y0) / (y1 - y0)) * (H - T - B);
    const g = svg("g", {}, root);
    axes(g, Xs, Y, [], niceTicks(y0, y1, 5), L, R, T, B, W, H, "time", D.kind === "ratio" ? "log2 ratio" : "log2 intensity");
    S.labels.forEach((lab, a) => text(g, Xs(a), H - B + 16, lab, { "text-anchor": "middle" }));
    const line = (m, col, dash) => {
      let d = "", pen = false;
      m.forEach((v, a) => { if (v == null) { pen = false; return; } d += (pen ? "L" : "M") + Xs(a).toFixed(1) + " " + Y(v).toFixed(1); pen = true; });
      if (d) svg("path", { d: d, fill: "none", stroke: col, "stroke-width": 2, "stroke-dasharray": dash }, g);
    };
    const muted = css("--muted"), col = timeColor(S.cls[k] === "not" ? "not" : S.cls[k]) || css("--c0");
    if (other) {
      line(omeans, muted, "5 4");
      opts.forEach((p) => title(svg("circle", { cx: Xs(p[0]) + 5, cy: Y(p[1]), r: 2.8, fill: "none", stroke: muted, "stroke-width": 1.2, "data-ctrl": 1 }, g), D.samples[p[2]] + ": " + fmt(p[1])));
    }
    line(means, S.cls[k] === "not" ? css("--text2") : col, null);
    pts.forEach((p) => title(svg("circle", { cx: Xs(p[0]) - (other ? 5 : 0), cy: Y(p[1]), r: 3.2, fill: S.cls[k] === "not" ? css("--text2") : col, "fill-opacity": isImputed(i, p[2]) ? 0.3 : 0.85, "data-j": p[2] }, g),
      D.samples[p[2]] + ": " + fmt(p[1]) + (isImputed(i, p[2]) ? " (imputed)" : "")));
    if (other) text(g, L + 8, T + 10, "dashed: " + other.name, { "text-anchor": "start" });
    svgTools(host, root, "time_course_" + nameOf(i));
  }
  function renderTimeTable() {
    const S = timeSeries(), host = $("#timetable");
    if (!S || !host) return;
    const cols = timeCols(S), rows = timeRows(S, true), per = 25, pages = Math.max(1, Math.ceil(rows.length / per));
    TS.page = Math.min(TS.page, pages - 1);
    const mark = anyMark();
    let h = "<div class='tablewrap'><table><thead><tr>" + cols.map((c) => "<th data-k='" + c.k + "'" + (c.k === TS.sortKey ? " data-dir='" + (TS.sortDir > 0 ? "asc" : "desc") + "'" : "") + ">" + esc(c.t) + "</th>").join("") + "</tr></thead><tbody>";
    rows.slice(TS.page * per, TS.page * per + per).forEach((k) => {
      const hit = mark && matches(S.i[k]);
      h += "<tr data-k='" + k + "'" + (k === TS.focus ? " class='focus'" : "") + (hit ? " style='font-weight:650'" : "") + ">" + cols.map((c) => "<td" + (c.n ? " class='n'" : "") + ">" + c.f(S, k) + "</td>").join("") + "</tr>";
    });
    if (!rows.length) h += "<tr><td colspan='" + cols.length + "' class='muted'>No feature matches. Try another choice under Show" + (TS.pat ? ", or click the pattern again to clear it" : "") + ".</td></tr>";
    host.innerHTML = h + "</tbody></table></div><div class='pager'>" + fmtInt(rows.length) + " features" + (TS.pat ? " in pattern " + TS.pat : "") + " · page " + (TS.page + 1) + " of " + pages + " <button id='tprev'>‹</button><button id='tnext'>›</button></div>";
    $$("th", host).forEach((th) => (th.onclick = () => {
      const k = th.dataset.k;
      if (TS.sortKey === k) TS.sortDir *= -1; else { TS.sortKey = k; TS.sortDir = k === "F" || k === "max" || k === "tt" ? -1 : 1; }
      TS.page = 0; renderTimeTable();
    }));
    $$("tbody tr[data-k]", host).forEach((tr) => (tr.onclick = () => timeFocus(+tr.dataset.k)));
    $("#tprev").onclick = () => { TS.page = Math.max(0, TS.page - 1); renderTimeTable(); };
    $("#tnext").onclick = () => { TS.page = Math.min(pages - 1, TS.page + 1); renderTimeTable(); };
  }

  // ------------------------------------------------------- liganded sites
  // D.cys (cys.py report_payload): site ratio data (isoDTB). Per compound, columns over the sites: r = median log2
  // competition ratio, n / over = replicates measured / at or over the threshold, cls = index into D.cys.classes.
  // The calls are made in Python; the page only shows them.
  const CS = { c: 0, show: "lig", q: "", sortKey: "r", sortDir: -1, page: 0, rows: "sites" };
  const cysColor = (k) => css(k === 0 ? "--up" : k === 1 ? "--c3" : k === 2 ? "--ns" : "--c6");
  const fmtR = (r) => (r == null ? "–" : +Math.pow(2, r).toPrecision(3) >= 100 ? Math.round(Math.pow(2, r)).toLocaleString() : String(+Math.pow(2, r).toPrecision(3)));
  function cysCompound() { const X = D.cys; return X && X.ran && X.compounds && X.compounds.length ? X.compounds[Math.min(CS.c, X.compounds.length - 1)] : null; }
  function cysRows() {
    const X = D.cys, S = cysCompound(), q = CS.q.trim().toLowerCase(), out = [];
    for (let k = 0; k < X.i.length; k++) {
      if (CS.show === "lig" && S.cls[k] !== 0) continue;
      if (CS.show === "inc" && S.cls[k] !== 1) continue;
      if (CS.show === "any" && !X.nlig[k]) continue;
      if (CS.show === "sel" && X.sel[k] !== 1) continue;
      if (CS.show === "new" && !(X.nlig[k] && X.annotation && X.annotation.status[k] === "new")) continue;
      if (CS.show === "all" && S.r[k] == null) continue;
      if (q) {
        const i = X.i[k], t = ((D.f.label[i] || "") + " " + (D.f.id[i] || "") + " " + (D.f.desc[i] || "")).toLowerCase();
        if (!t.includes(q)) continue;
      }
      out.push(k);
    }
    const key = CS.sortKey, m = /^c(\d+)$/.exec(key);
    const val = (k) => (m ? X.compounds[+m[1]].r[k] : key === "name" ? nameOf(X.i[k]).toLowerCase() : key === "cls" ? S.cls[k] : key === "over" ? S.over[k] :
      key === "nlig" ? X.nlig[k] : key === "sel" ? (X.sel[k] || 9) : key === "status" ? X.annotation.status[k] : S.r[k]);
    out.sort((a, b) => {
      const x = val(a), y = val(b);
      if (x == null || y == null) return x == null ? (y == null ? 0 : 1) : -1;
      return (x > y ? 1 : x < y ? -1 : 0) * CS.sortDir;
    });
    return out;
  }
  function renderCys() {
    const host = $("#cysbody");
    if (!host) return;
    const X = D.cys || {}, S = cysCompound();
    if (X.ran) ["#cys", "#navcys"].forEach((s) => { const e = $(s); if (e) e.hidden = false; });
    if (!S) { host.innerHTML = "<div class='empty'>" + esc(X.reason || "No liganded-site calls were made.") + "</div>"; return; }
    CS.c = X.compounds.indexOf(S);
    const many = X.compounds.length > 1, ann = X.annotation;
    if ((CS.show === "sel" && !many) || (CS.show === "new" && !ann)) CS.show = "lig";
    const pct = (c) => (c.fraction == null ? "–" : (100 * c.fraction).toFixed(1) + "%");
    let h = "<div class='tiles'>" + X.compounds.map((c) => "<div class='tile'><div class='k'>" + esc(c.name) + "</div><div class='v'>" + fmtInt(c.counts.liganded) +
      "</div><div class='d'>liganded of " + fmtInt(c.assessed) + " assessed (" + pct(c) + ")" + (c.counts.inconsistent ? " · " + fmtInt(c.counts.inconsistent) + " inconsistent" : "") +
      (c.counts["too few"] ? " · " + fmtInt(c.counts["too few"]) + " with too few replicates" : "") + "</div></div>").join("");
    if (many) h += "<div class='tile'><div class='k'>Selective sites</div><div class='v'>" + fmtInt(X.selectivity.selective) + "</div><div class='d'>one compound only · " +
      fmtInt(X.selectivity.shared) + " shared · " + fmtInt(X.selectivity.unresolved) + " unresolved</div></div>";
    if (ann) h += "<div class='tile'><div class='k'>New liganded sites</div><div class='v'>" + fmtInt(ann.liganded_new) + "</div><div class='d'>not in " + esc(ann.file) + " · " +
      fmtInt(ann.liganded_known) + " in it</div></div>";
    const opts = [["lig", "liganded by " + S.name], ["inc", "inconsistent in " + S.name]].concat(many ? [["any", "liganded by any compound"], ["sel", "selective"]] : [],
      ann ? [["new", "liganded and new"]] : [], [["all", "every site measured in " + S.name]]);
    h += "</div><div class='row'>" + (many ? "<label class='ctl'>Compound <select id='ccomp'>" + X.compounds.map((c, k) => "<option value='" + k + "'" + (k === CS.c ? " selected" : "") + ">" + esc(c.name) + "</option>").join("") + "</select></label>" : "") +
      "<label class='ctl'>Show <select id='cshow'>" + opts.map((o) => "<option value='" + o[0] + "'" + (o[0] === CS.show ? " selected" : "") + ">" + esc(o[1]) + "</option>").join("") + "</select></label>" +
      "<input type='search' id='cq' placeholder='Find a site' aria-label='Find a site' value='" + esc(CS.q) + "'>" +
      "<span class='seg'><button id='csites'" + (CS.rows === "sites" ? " class='on'" : "") + ">Sites</button><button id='cprot'" + (CS.rows === "proteins" ? " class='on'" : "") + ">Proteins</button></span>" +
      "<span class='muted'>liganded: " + esc(X.rule) + (S.minRep !== X.minRep ? " (" + esc(S.name) + ": " + S.minRep + " of " + S.reps + ")" : "") + " · every site: <a href='cysteine_sites.tsv'>cysteine_sites.tsv</a></span></div>" +
      "<div class='card chart' id='cysrank'></div><div class='legend'>" + X.classes.map((c, k) => "<span><span class='sw' style='background:" + cysColor(k) + "'></span>" + esc(c) + "</span>").join("") + "</div><div id='cystable'></div>";
    host.innerHTML = h;
    const comp = $("#ccomp");
    if (comp) comp.onchange = (e) => { CS.c = +e.target.value; CS.page = 0; renderCys(); };
    $("#cshow").onchange = (e) => { CS.show = e.target.value; CS.page = 0; safe(renderCysTable, "#cystable"); };
    $("#cq").oninput = (e) => { CS.q = e.target.value; CS.page = 0; safe(renderCysTable, "#cystable"); };
    $("#csites").onclick = () => { CS.rows = "sites"; renderCys(); };
    $("#cprot").onclick = () => { CS.rows = "proteins"; renderCys(); };
    safe(renderCysRank, "#cysrank");
    safe(renderCysTable, "#cystable");
  }
  /** Every measured site of the compound, ranked by its competition ratio, against the threshold. */
  function renderCysRank() {
    const X = D.cys, S = cysCompound(), host = $("#cysrank");
    if (!S || !host) return;
    const ks = [];
    for (let k = 0; k < X.i.length; k++) if (S.r[k] != null) ks.push(k);
    ks.sort((a, b) => S.r[b] - S.r[a]);
    const W = widthOf(host, 900), H = heightOf(300), L = 56, R = 16, T = 16, B = 44;
    const root = frame(host, W, H);
    if (!ks.length) { text(root, W / 2, H / 2, "No site of " + S.name + " has a ratio", { "text-anchor": "middle" }); return; }
    const thr = Math.log2(X.ratio);
    let y0 = Math.min(S.r[ks[ks.length - 1]], -thr), y1 = Math.max(S.r[ks[0]], thr);
    const py = (y1 - y0) * 0.06 || 0.5;
    y0 -= py; y1 += py;
    const n = ks.length, Xs = (v) => L + (n > 1 ? v / (n - 1) : 0.5) * (W - L - R), Y = (v) => T + (1 - (v - y0) / (y1 - y0)) * (H - T - B);
    const g = svg("g", {}, root);
    axes(g, (v) => Xs(v - 1), Y, niceTicks(1, n, 6).filter((v) => v >= 1 && v <= n), niceTicks(y0, y1, 6), L, R, T, B, W, H, "sites, ranked by competition ratio", "log2 R (" + (X.dir === "high" ? "heavy / light" : "light / heavy") + ")");
    const muted = css("--muted"), mark = anyMark(), sel = css("--sel");
    svg("line", { x1: L, x2: W - R, y1: Y(thr), y2: Y(thr), stroke: muted, "stroke-dasharray": "4 4" }, g);
    text(g, W - R - 4, Y(thr) - 5, "R = " + X.ratio, { "text-anchor": "end" });
    svg("line", { x1: L, x2: W - R, y1: Y(0), y2: Y(0), stroke: css("--axis") }, g);
    ks.forEach((k, pos) => {
      const hit = mark && matches(X.i[k]), lig = S.cls[k] === 0;
      const c = svg("circle", { cx: Xs(pos), cy: Y(S.r[k]), r: lig ? 3.2 : 2.2, fill: cysColor(S.cls[k]), "fill-opacity": lig ? 0.9 : 0.55, stroke: hit ? sel : null, "stroke-width": hit ? 1.6 : null, "data-k": k, style: "cursor:pointer" }, g);
      c.onmouseenter = (e) => showTip(e, "<b>" + esc(nameOf(X.i[k])) + "</b> · " + esc(X.classes[S.cls[k]]) + "<br>R " + fmtR(S.r[k]) + " · " + S.over[k] + " of " + S.n[k] + " replicates at R ≥ " + X.ratio);
      c.onmouseleave = hideTip;
      c.onclick = () => setFocus(X.i[k]);
    });
    svgTools(host, root, "liganded_rank_" + S.name);
  }
  function renderCysTable() {
    const X = D.cys, S = cysCompound(), host = $("#cystable");
    if (!S || !host) return;
    if (CS.rows === "proteins") { renderCysProteins(host); return; }
    const many = X.compounds.length > 1, ann = X.annotation, lim = 2 * Math.log2(X.ratio);
    const cols = [{ k: "name", t: "Site" }].concat(X.compounds.map((c, j) => ({ k: "c" + j, t: "R " + c.name, n: 1, comp: j })),
      [{ k: "over", t: "replicates ≥ R", n: 1 }, { k: "cls", t: "call" }], many ? [{ k: "nlig", t: "liganded by", n: 1 }, { k: "sel", t: "selectivity" }] : [],
      ann ? [{ k: "status", t: "annotation" }] : [], [{ k: "desc", t: "Description" }]);
    const rows = cysRows(), per = 50, pages = Math.max(1, Math.ceil(rows.length / per));
    CS.page = Math.min(CS.page, pages - 1);
    const mark = anyMark();
    const sortKey = CS.sortKey === "r" ? "c" + CS.c : CS.sortKey;
    let h = "<div class='tablewrap'><table><thead><tr>" + cols.map((c) => "<th data-k='" + c.k + "'" + (c.k === sortKey ? " data-dir='" + (CS.sortDir > 0 ? "asc" : "desc") + "'" : "") + ">" + esc(c.t) + "</th>").join("") + "</tr></thead><tbody>";
    rows.slice(CS.page * per, CS.page * per + per).forEach((k) => {
      const i = X.i[k], hit = mark && matches(i);
      h += "<tr data-k='" + k + "'" + (i === ST.focus ? " class='focus'" : "") + (hit ? " style='font-weight:650'" : "") + ">" + cols.map((c) => {
        if (c.comp != null) {
          const P = X.compounds[c.comp], r = P.r[k];
          return "<td class='n' title='" + esc(P.name + ": " + X.classes[P.cls[k]] + ", " + P.over[k] + " of " + P.n[k] + " replicates") + "'" +
            (r == null ? "" : " style='background:" + seqColor(Math.max(0, r) / lim) + (P.cls[k] === 0 ? ";font-weight:650" : "") + "'") + ">" + fmtR(r) + "</td>";
        }
        if (c.k === "name") return "<td>" + esc(nameOf(i)) + "</td>";
        if (c.k === "over") return "<td class='n'>" + S.over[k] + " / " + S.n[k] + "</td>";
        if (c.k === "cls") return "<td><span class='dot' style='background:" + cysColor(S.cls[k]) + "'></span> " + esc(X.classes[S.cls[k]]) + "</td>";
        if (c.k === "nlig") return "<td class='n'>" + X.nlig[k] + "</td>";
        if (c.k === "sel") return "<td>" + esc(X.selNames[X.sel[k]]) + "</td>";
        if (c.k === "status") return "<td>" + esc(ann.status[k]) + "</td>";
        return "<td class='desc'>" + esc((D.f.desc[i] || "").slice(0, 120)) + "</td>";
      }).join("") + "</tr>";
    });
    if (!rows.length) h += "<tr><td colspan='" + cols.length + "' class='muted'>No site matches. Try another choice under Show.</td></tr>";
    host.innerHTML = h + "</tbody></table></div><div class='pager'>" + fmtInt(rows.length) + " sites · page " + (CS.page + 1) + " of " + pages + " <button id='cprev'>‹</button><button id='cnext'>›</button></div>";
    $$("th", host).forEach((th) => (th.onclick = () => {
      const k = th.dataset.k;
      if (k === "desc") return;
      if (sortKey === k) CS.sortDir *= -1; else { CS.sortKey = k; CS.sortDir = k === "name" || k === "cls" || k === "sel" || k === "status" ? 1 : -1; }
      CS.page = 0; renderCysTable();
    }));
    $$("tbody tr[data-k]", host).forEach((tr) => (tr.onclick = () => setFocus(X.i[+tr.dataset.k])));
    $("#cprev").onclick = () => { CS.page = Math.max(0, CS.page - 1); renderCysTable(); };
    $("#cnext").onclick = () => { CS.page = Math.min(pages - 1, CS.page + 1); renderCysTable(); };
  }
  /** Proteins with a liganded cysteine: most of a protein's sites moving together hints at its amount, not a site. */
  function renderCysProteins(host) {
    const X = D.cys, q = CS.q.trim().toLowerCase();
    const rows = (X.proteins || []).filter((e) => !q || (e.g + " " + e.p).toLowerCase().includes(q));
    let h = "<div class='tablewrap'><table><thead><tr><th>Gene</th><th>Protein</th><th>sites</th>" + X.compounds.map((c) => "<th>" + esc(c.name) + " liganded / assessed</th>").join("") + "</tr></thead><tbody>";
    rows.slice(0, 500).forEach((e, k) => {
      h += "<tr data-k='" + k + "'><td>" + esc(e.g) + "</td><td>" + esc(String(e.p).slice(0, 40)) + "</td><td class='n'>" + e.s + "</td>" + e.per.map((v) => "<td class='n'>" + v[1] + " / " + v[0] +
        (v[2] ? " <span class='badge imp' title='At least half of this protein&#39;s assessed cysteines are liganded: possibly the protein amount, not one site'>most sites</span>" : "") + "</td>").join("") + "</tr>";
    });
    if (!rows.length) h += "<tr><td colspan='" + (3 + X.compounds.length) + "' class='muted'>No protein has a liganded cysteine.</td></tr>";
    host.innerHTML = h + "</tbody></table></div><div class='pager'>" + fmtInt(rows.length) + " proteins" + (rows.length > 500 ? " (first 500 shown)" : "") + " · click one to mark its sites</div>";
    $$("tbody tr[data-k]", host).forEach((tr) => (tr.onclick = () => { const e = rows[+tr.dataset.k]; setHighlight(new Set(e.x), e.g + " sites"); }));
  }

  // ----------------------------------------------------------------- help
  // Plain-language help from ionomos/help/*.md, rendered to safe HTML in Python (report.py _help_payload):
  // a "?" beside each section title, QC tab and issue opens its entry inline, and the Help section at the
  // end lists every entry, the glossary and the issues of this report. Entry HTML is ours; anything that
  // comes from the data (issue titles) goes through esc().
  const H = Object.assign({ entries: {}, report: [], glossary: [], more: [], issues: [] }, D.help || {});
  const HELP_AT = [["#differential > h2", "report.differential"], ["#differential > p.sub", "report.search"],
    ["#differential-body > .bar", "report.cutoffs"], ["#differential-body > h3", "report.phist"],
    ["#differential-body > .tablebar", "report.table"], ["#compare > h2", "report.compare"], ["#onoff > h2", "report.onoff"],
    ["#heat > h2", "report.heatmap"], ["#enrichment > h2", "report.enrichment"], ["#dose > h2", "report.dose"], ["#time > h2", "report.time"], ["#cys > h2", "report.cys"], ["#quality > h2", "report.quality"],
    ["#methods > h2", "report.methods"], ["#files > h2", "report.files"]];
  const HELP_H = { "Data source": "report.source", "Settings used": "report.methods", "Sample metadata": "report.sdrf",
    "Cross-check": "report.fpa", "Highlight groups": "report.groups", "Hits": "report.hitfilters", "Plot": "report.plotoptions", "Figures for slides": "report.export" };
  /** A "?" button in `host` that opens entry `id` in a panel placed after `after` (default: host). */
  function addHelp(host, id, after) {
    const e = H.entries[id];
    if (!host || !e || $(":scope > .qhelp", host)) return;
    const b = document.createElement("button");
    b.type = "button"; b.className = "qhelp"; b.textContent = "?";
    b.title = "What is this?"; b.setAttribute("aria-label", "Help: " + e.t); b.setAttribute("aria-expanded", "false");
    b.dataset.help = id;
    b.onclick = (ev) => { ev.stopPropagation(); toggleHelp(b, id, after || host); };
    host.appendChild(b);
  }
  function toggleHelp(b, id, after) {
    const old = b._panel;
    if (old && old.isConnected) { old.remove(); b._panel = null; b.setAttribute("aria-expanded", "false"); return; }
    const e = H.entries[id], p = document.createElement("div");
    p.className = "helppanel"; p.setAttribute("role", "note");
    p.innerHTML = "<div class='hh'><b>" + esc(e.t) + "</b> <button type='button' class='hclose' aria-label='Close help'>×</button></div>" +
      e.h + "<p class='muted'><a href='#help-" + esc(id) + "' data-help='" + esc(id) + "'>More in the Help section</a></p>";
    $(".hclose", p).onclick = () => toggleHelp(b, id, after);
    after.insertAdjacentElement("afterend", p);
    b._panel = p; b.setAttribute("aria-expanded", "true");
    wireHelpLinks(p);
  }
  function openHelp(id) {
    const el = document.getElementById("help-" + id);
    if (!el) return;
    el.open = true;
    goTo(el.id);
  }
  function wireHelpLinks(root) {
    $$("a[data-help]", root).forEach((a) => (a.onclick = (ev) => { ev.preventDefault(); openHelp(a.dataset.help); }));
  }
  function helpItem(id, extra) {
    const e = H.entries[id];
    return e ? "<details class='helpitem' id='help-" + esc(id) + "'><summary>" + esc(e.t) + "</summary><div class='hbody'>" + (extra || "") + e.h + "</div></details>" : "";
  }
  function renderHelp() {
    const host = $("#helpbody");
    if (!host) return;
    HELP_AT.forEach(([sel, id]) => addHelp($(sel), id));
    $$("#methods > h3, .optpanel h4").forEach((h) => { const k = Object.keys(HELP_H).find((t) => h.textContent.indexOf(t) === 0); if (k) addHelp(h, HELP_H[k]); });
    $$(".issues > .issue").forEach((box) => {
      const t = ($(":scope > b", box) || {}).textContent, x = H.issues.find((i) => i.title === t && i.id);
      if (x) addHelp($(".sev", box), x.id, box.lastElementChild);
    });
    if (!Object.keys(H.entries).length) { host.innerHTML = "<div class='empty'>The help could not be included in this report.</div>"; return; }
    let h = "";
    const byId = new Map();
    H.issues.forEach((x) => { const k = x.id || ""; if (!byId.has(k)) byId.set(k, []); byId.get(k).push(x); });
    if (byId.size) {
      h += "<h3>The issues in this report</h3>";
      byId.forEach((xs, id) => {
        const list = "<ul class='hin'>" + xs.map((x) => "<li>" + esc(x.title) + " <span class='muted'>" + esc(x.code) + "</span></li>").join("") + "</ul>";
        h += id ? helpItem(id, "<p class='muted'>In this report:</p>" + list) : "<div class='helpitem'>" + list + "<p class='muted'>No help for this one yet.</p></div>";
      });
    }
    const group = (ttl, ids) => (ids.length ? "<h3>" + ttl + "</h3>" + ids.map((i) => helpItem(i)).join("") : "");
    h += group("Reading this report", H.report) + group("Glossary", H.glossary) + group("More answers", H.more.filter((i) => !byId.has(i)));
    h += "<p class='muted'>Everything else (naming folders, the inbox, failed searches, what Ionomos never does to your data) is in the full help: the Help button in the Ionomos app, or <code>ionomos help --open</code>.</p>";
    host.innerHTML = h;
    wireHelpLinks(host);
  }

  // -------------------------------------------------------- figure export
  // One style (XS) for every exported figure: size, text, marks, colours, what is drawn. It starts from the
  // lab's defaults (config.yaml analysis.export, sent as D.exportDefaults), is kept in this browser, and can be
  // saved and loaded as a small JSON file (a house style). `ionomos export` reads the same keys
  // (downstream/charts.py STYLE_DEFAULTS): keep the two in step.
  // A figure is exported by drawing its part of the report again with EX set (css(), widthOf() and heightOf()
  // then answer from the style), copying the chart svgTools() is handed, and putting a title, a legend and the
  // cut-offs around it. Text stays text, colours are written out, nothing in the file refers to the page.
  const STYLE_KEY = "ionomos.export.v1";
  // name, width, height, unit, text size (pt) that suits it. A 16:9 PowerPoint slide is 1280 x 720 px at 96 px / inch.
  const SIZES = { slide169: ["16:9 slide", 1280, 720, "px", 14], slide43: ["4:3 slide", 960, 720, "px", 14], half: ["Half a slide", 640, 600, "px", 12],
    col1: ["Journal figure, one column (85 mm)", 85, 70, "mm", 7], col2: ["Journal figure, two columns (180 mm)", 180, 110, "mm", 7], custom: ["Custom size", 0, 0, "", 0] };
  const STATIC_FIGS = ["volcano", "pca", "heatmap", "correlation"];  // what `figures:` may list (the watcher's static files)
  const STYLE_DEFAULTS = { size: "slide169", width: 1280, height: 720, unit: "px", font_pt: 14, font_family: "Arial", line_scale: 1, point_scale: 1,
    palette: "default", up: "#e34948", down: "#2a78d6", neutral: "#c3c2b7", background: "light", title: true, subtitle: true, legend: true, note: true,
    labels: "screen", label_count: null, png_scale: 2, png_dpi: 0, zip_format: "both", figures: [] };
  const STYLE_ENUMS = { size: Object.keys(SIZES), unit: ["px", "mm"], palette: ["default", "colorblind", "grey", "custom"], background: ["light", "dark", "transparent"],
    labels: ["screen", "top", "marked", "none"], zip_format: ["svg", "png", "both"] };
  const STYLE_RANGES = { width: [20, 8000], height: [20, 8000], font_pt: [4, 48], line_scale: [0.25, 4], point_scale: [0.25, 4], png_scale: [1, 4] };
  const FONTS = ["Arial", "Helvetica", "Calibri", "Segoe UI", "Verdana", "Times New Roman", "Georgia", "Courier New"];
  // [light, dark] marks per palette: up, down, not significant, and the eight categories (conditions, groups).
  // colorblind is Okabe and Ito's set; grey is for print without colour.
  const PALETTES = {
    default: [{ up: "#e34948", down: "#2a78d6", ns: "#c3c2b7", c: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"] },
      { up: "#e66767", down: "#3987e5", ns: "#52514e", c: ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"] }],
    colorblind: [{ up: "#d55e00", down: "#0072b2", ns: "#b3b3b3", c: ["#0072b2", "#e69f00", "#009e73", "#cc79a7", "#56b4e9", "#d55e00", "#f0e442", "#000000"] },
      { up: "#e69f00", down: "#56b4e9", ns: "#5a5a5a", c: ["#56b4e9", "#e69f00", "#009e73", "#cc79a7", "#0072b2", "#d55e00", "#f0e442", "#ffffff"] }],
    grey: [{ up: "#111111", down: "#6b6b6b", ns: "#cfcfcf", c: ["#111111", "#5c5c5c", "#8c8c8c", "#b0b0b0", "#333333", "#747474", "#9e9e9e", "#c4c4c4"] },
      { up: "#f2f2f2", down: "#9a9a9a", ns: "#4d4d4d", c: ["#f2f2f2", "#b0b0b0", "#8c8c8c", "#6b6b6b", "#d9d9d9", "#9e9e9e", "#7a7a7a", "#5c5c5c"] }],
  };
  const INKS = {
    light: { "--page": "#ffffff", "--surface": "#ffffff", "--sunk": "#f1f0ec", "--text": "#0b0b0b", "--text2": "#3f3e3c", "--muted": "#6f6d68", "--line": "#d5d4cc", "--grid": "#e1e0d9", "--axis": "#a8a79c", "--sel": "#0b0b0b", "--accent": "#2a78d6", "--warnink": "#8a5300" },
    dark: { "--page": "#0d0d0d", "--surface": "#1a1a19", "--sunk": "#141413", "--text": "#ffffff", "--text2": "#c3c2b7", "--muted": "#9a9891", "--line": "#383835", "--grid": "#2c2c2a", "--axis": "#4a4a46", "--sel": "#ffffff", "--accent": "#3987e5", "--warnink": "#f2b64a" },
  };
  /** A complete, valid style out of anything: the lab's defaults, this browser's storage, a loaded file (all
   * untrusted). An unknown key is dropped and a bad value keeps the base value; `bad` collects their names. */
  function normStyle(raw, base, bad) {
    const out = Object.assign({}, base || STYLE_DEFAULTS);
    out.figures = (out.figures || []).slice();
    bad = bad || [];
    if (raw == null) return out;
    if (typeof raw !== "object" || Array.isArray(raw)) { bad.push("(not a style)"); return out; }
    const has = (k) => Object.prototype.hasOwnProperty.call(raw, k);
    Object.keys(raw).forEach((k) => { if (!Object.prototype.hasOwnProperty.call(STYLE_DEFAULTS, k) && k !== "ionomos_export_style") bad.push(k); });
    Object.keys(STYLE_DEFAULTS).forEach((k) => {
      if (!has(k)) return;
      const v = raw[k], num = typeof v === "number" && isFinite(v);
      let ok = false;
      if (STYLE_ENUMS[k]) ok = typeof v === "string" && STYLE_ENUMS[k].includes(v);
      else if (STYLE_RANGES[k]) ok = num && v >= STYLE_RANGES[k][0] && v <= STYLE_RANGES[k][1];
      else if (typeof STYLE_DEFAULTS[k] === "boolean") ok = typeof v === "boolean";
      else if (k === "up" || k === "down" || k === "neutral") ok = typeof v === "string" && /^#[0-9a-fA-F]{6}$/.test(v);
      else if (k === "font_family") ok = typeof v === "string" && /^[A-Za-z0-9][A-Za-z0-9 _-]{0,39}$/.test(v);
      else if (k === "label_count") ok = v === null || (num && v === Math.floor(v) && v >= 0 && v <= 200);
      else if (k === "png_dpi") ok = v === 0 || (num && v >= 72 && v <= 1200);
      else if (k === "figures") ok = Array.isArray(v) && v.length <= STATIC_FIGS.length && v.every((x) => STATIC_FIGS.includes(x));
      if (ok) out[k] = k === "figures" ? v.slice() : typeof v === "string" && v[0] === "#" ? v.toLowerCase() : v;
      else bad.push(k);
    });
    // a size named without a text size takes the size that suits it (7 pt for a journal column, 14 pt for a slide)
    if (has("size") && !has("font_pt") && out.size === raw.size && out.size !== "custom") out.font_pt = SIZES[out.size][4];
    return out;
  }
  const LAB = normStyle(D.exportDefaults);  // the lab's house style, or the built-in defaults
  let XS = normStyle(store.get(STYLE_KEY, null), LAB);
  const FIG_TEXT = {};  // figure name -> { title, subtitle } typed in the dialog (this session only)
  function saveStyle() { store.set(STYLE_KEY, XS); }
  /** [width, height] of the whole figure in px (96 per inch), and whether the size is given in mm. */
  function sizePx(st) {
    const z = SIZES[st.size], own = st.size === "custom", mm = (own ? st.unit : z[3]) === "mm", k = mm ? 96 / 25.4 : 1;
    const cl = (v) => Math.max(120, Math.min(8000, v * k));
    return [cl(own ? st.width : z[1]), cl(own ? st.height : z[2]), mm];
  }
  /** The family with fallbacks every program knows, e.g. "'Segoe UI', Arial, Helvetica, sans-serif". */
  function fontStack(name) {
    const tail = /times|georgia|garamond|cambria|palatino|serif|book/i.test(name) && !/sans/i.test(name) ? ["'Times New Roman'", "Times", "serif"]
      : /courier|mono|consolas|menlo/i.test(name) ? ["'Courier New'", "Courier", "monospace"] : ["Arial", "Helvetica", "sans-serif"];
    return [/[ _-]/.test(name) ? "'" + name + "'" : name].concat(tail.filter((x) => x.replace(/'/g, "").toLowerCase() !== name.toLowerCase())).join(", ");
  }
  function tokensOf(st) {
    const dark = st.background === "dark", t = Object.assign({}, INKS[dark ? "dark" : "light"]), m = (PALETTES[st.palette] || PALETTES.default)[dark ? 1 : 0];
    t["--up"] = st.palette === "custom" ? st.up : m.up;
    t["--down"] = st.palette === "custom" ? st.down : m.down;
    t["--ns"] = st.palette === "custom" ? st.neutral : m.ns;
    m.c.forEach((c, k) => (t["--c" + k] = c));
    if (st.palette === "grey") Object.keys(t).forEach((k) => (t[k] = toGrey(t[k])));  // the inks too: no tint left
    return t;
  }
  const clean = (s) => String(s == null ? "" : s).replace(/[\u0000-\u001f\u007f￾￿]+/g, " ").trim();  // no control characters: they break XML and text files
  function toGrey(c) {
    let m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(c || ""), rgb = null;
    if (m) rgb = hex(c);
    else if ((m = /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)/i.exec(c || ""))) rgb = [+m[1], +m[2], +m[3]];
    if (!rgb) return c;
    const y = Math.max(0, Math.min(255, Math.round(0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]))).toString(16).padStart(2, "0");
    return "#" + y + y + y;
  }

  // what each chart is, by the start of its svgTools() name, and which cut-offs it rests on: "view" = the ones
  // set here, "saved" = the report's own (analysis settings), "" = none
  const FIG_KINDS = [["volcano_", "Volcano plot", "view"], ["pvalues_", "p-value distribution", ""], ["compare_", "Fold change against fold change", "view"], ["upset_", "Overlap of the hit lists", "view"],
    ["heatmap", "Heatmap of significant features", "saved"], ["enrichment_", "Gene sets over-represented among the hits", "saved"], ["gene_set_ranks_", "Gene sets by rank", ""], ["barcode_", "Barcode plot of a gene set", ""],
    ["values_", "Values per condition", ""], ["PCA", "PCA of the samples", ""], ["correlation", "Sample correlation", ""], ["cumulative_missing", "Missing values", ""], ["missingness_vs_intensity", "Missing values against intensity", ""],
    ["distributions", "Value distribution per sample", ""], ["cv_", "Coefficient of variation", ""], ["mean_variance", "Mean against variance", ""], ["abundance_rank", "Abundance rank", ""],
    ["identifications", "Identifications per sample", ""], ["imputation", "Measured and imputed values", ""], ["power", "Power", ""], ["search_quality_", "Search quality per run", ""],
    ["dose_potency_", "Dose-response: potency against effect", ""], ["dose_curve_", "Dose-response curve", ""], ["time_course_", "Time course", ""], ["liganded_rank_", "Liganded sites", ""]];
  const TEST_NAMES = { limma: "limma moderated t-test", welch: "Welch t-test", student: "Student t-test" };
  /** The cut-offs in plain words: shown beside the plot and written into every exported figure. */
  function cutText(c) {
    if (c && c.conf === "none") return "|log2FC| ≥ " + (ST.lfc || 1) + " (fold change only: no replicates, no p-values)";
    return "|log2FC| ≥ " + ST.lfc + " and " + (ST.adj ? "adjusted p" : "p") + " ≤ " + ST.alpha;
  }
  const savedCutText = () => "|log2FC| ≥ " + D.settings.log2fc + " and " + (D.settings.use_adjusted ? "adjusted p" : "p") + " ≤ " + D.settings.alpha;
  const filterText = () => [ST.opt.hideImp ? "imputation-driven hits ignored" : "", ST.opt.minPep ? "hits need ≥ " + ST.opt.minPep + " " + (D.evidence || "peptides") : ""].filter(Boolean).join(", ");
  function analysisText() {
    return [TEST_NAMES[D.settings.test] || D.settings.test, D.kind === "ratio" ? "" : "normalisation: " + D.settings.normalize, D.imputationLabel ? "imputation: " + D.imputationLabel : ""].filter(Boolean).join("; ");
  }
  /** What a chart is and which comparison and cut-offs it shows, read from the report's state as it was drawn. */
  function figInfo(name) {
    const k = FIG_KINDS.find((x) => name.indexOf(x[0]) === 0), what = k ? k[1] : name.replace(/_/g, " "), cut = k ? k[2] : "view";
    const c = /^(volcano_|pvalues_)/.test(name) ? C() : null;
    let detail = "";
    if (/^compare_/.test(name) && D.comps[cmpA] && D.comps[cmpB]) detail = D.comps[cmpA].name + " (x) against " + D.comps[cmpB].name + " (y)";
    else if (/^enrichment_/.test(name) && renderORA._st) detail = renderORA._st.comp + ", " + renderORA._st.dir + " hits, " + renderORA._st.lib;
    else if (/^(gene_set_ranks_|barcode_)/.test(name) && renderRank._st) detail = renderRank._st.comp + ", " + renderRank._st.lib + (/^barcode_/.test(name) && renderRank._st.sel ? ", " + renderRank._st.sel : "");
    else if (/^(values_|dose_curve_|time_course_|cv_|liganded_rank_|dose_potency_)/.test(name)) detail = name.replace(/^(values|dose_curve|time_course|cv|liganded_rank|dose_potency)_/, "");
    const cuts = cut === "view" ? cutText(c) + (filterText() ? "; " + filterText() : "") : cut === "saved" ? savedCutText() + " (the report's saved cut-offs)" : "";
    return { what: what, cut: cut, cuts: cuts, c: c, detail: clean(detail), title: clean(c ? c.name : what), subtitle: clean((c ? what + " · " : detail ? detail + " · " : "") + D.title) };
  }
  /** The legend of a chart, read from the page beside it: [colour or null, text]. */
  function legendItems(cap, tk) {
    const out = [], host = cap.host, els = [];
    if (host.id === "volcano" && C()) {
      const c = C(), n = counts(c);
      out.push([tk["--up"], "Up " + fmtInt(n.up)], [tk["--down"], "Down " + fmtInt(n.down)], [tk["--ns"], c.conf === "none" ? "Below the cut-off" : "Not significant"]);
      if ($("#glegend")) els.push($("#glegend"));
    }
    const isLegend = (e) => e && e.classList && e.classList.contains("legend");
    Array.from(host.children).concat([host.previousElementSibling]).forEach((e) => { if (isLegend(e)) els.push(e); });
    const next = host.nextElementSibling;
    if (isLegend(next)) els.push(next);
    else if (next && next.classList && next.classList.contains("meta")) $$(".legend", next).forEach((e) => els.push(e));
    els.forEach((el) => Array.from(el.children).forEach((sp) => {
      const sw = $(".sw", sp), t = clean(sp.textContent).slice(0, 70);
      if (t) out.push([sw ? sw.style.backgroundColor || sw.style.background || null : null, t]);
    }));
    return out;
  }
  /** Ready the copied chart for a file: nothing that only served the page, the style's line and point sizes. */
  function tidyChart(root, st) {
    const grey = st.palette === "grey";
    Array.from(root.querySelectorAll("*")).forEach((e) => {
      if (e.getAttribute("fill") === "transparent" || e.getAttribute("visibility") === "hidden" || e.tagName.toLowerCase() === "foreignobject") { e.remove(); return; }
      e.removeAttribute("style");
      const sk = e.getAttribute("stroke");
      if (st.line_scale !== 1 && sk && sk !== "none") e.setAttribute("stroke-width", +((parseFloat(e.getAttribute("stroke-width")) || 1) * st.line_scale).toFixed(2));
      if (st.point_scale !== 1 && e.tagName === "circle") e.setAttribute("r", +((parseFloat(e.getAttribute("r")) || 0) * st.point_scale).toFixed(2));
      if (grey) ["fill", "stroke", "stop-color"].forEach((a) => { if (e.hasAttribute(a)) e.setAttribute(a, toGrey(e.getAttribute(a))); });
      if (!e.children.length && e.textContent && clean(e.textContent) !== e.textContent.trim()) e.textContent = clean(e.textContent);
    });
  }
  /** The finished figure around one captured chart: { name, svg, w, h (px), fit, info, desc }. */
  function composeFigure(cap, st, box, tk) {
    const info = figInfo(cap.name), [PW, PH, mm] = sizePx(st), s = st.font_pt / 9, LW = PW / s, LH = PH / s, pad = 12;
    const body = cap.root.cloneNode(true);
    tidyChart(body, st);
    const w = +cap.root.getAttribute("width") || box.w, h = +cap.root.getAttribute("height") || box.h;
    const own = FIG_TEXT[cap.name] || {}, grey = st.palette === "grey";
    const tw = (t, fs) => t.length * fs * 0.56;  // no layout to ask off the page: an estimate of a text's width
    const cut = (t, fs) => { const n = Math.floor((LW - 2 * pad) / (fs * 0.56)); return t.length > n ? t.slice(0, Math.max(1, n - 1)) + "…" : t; };
    const ttl = st.title ? cut(clean(own.title || info.title), 16) : "", sub = st.subtitle ? cut(clean(own.subtitle || info.subtitle), 12) : "";
    // a low-confidence or fold-change-only comparison says so in the figure, whatever is switched off
    const warn = info.c && info.c.conf ? (info.c.conf === "none" ? "FOLD CHANGE ONLY: no replicates, no p-values" : "LOW CONFIDENCE: a group has one sample; p-values borrowed") : "";
    const note = st.note ? cut(clean(D.title + (info.cuts ? " · hits: " + info.cuts : "") + " · " + analysisText()), 10) : "";
    // legend rows, flowed into the full width
    const rows = [[]];
    let lx = 0;
    (st.legend ? legendItems(cap, tk) : []).forEach((it) => {
      const iw = (it[0] ? 15 : 0) + tw(it[1], 12) + 16;
      if (lx && lx + iw > LW - 2 * pad) { rows.push([]); lx = 0; }
      rows[rows.length - 1].push([lx, it]);
      lx += iw;
    });
    const nrows = rows[0].length ? rows.length : 0;
    const top = pad + (ttl ? 24 : 0) + (sub ? 18 : 0) + (warn ? 18 : 0) + nrows * 18 + (nrows ? 4 : 0), bottom = (note ? 20 : 0) + pad * 0.6;
    let fit = Math.min(1, box.w / w, (LH - top - bottom) / h);
    fit = Math.max(0.05, fit);
    const widest = Math.max(ttl ? tw(ttl, 16) : 0, sub ? tw(sub, 12) : 0, warn ? tw(warn, 11.5) : 0, note ? tw(note, 10) : 0, ...rows.map((r) => (r.length ? r[r.length - 1][0] + (r[r.length - 1][1][0] ? 15 : 0) + tw(r[r.length - 1][1][1], 12) : 0)));
    let OW = Math.min(LW, Math.max(w * fit, widest) + 2 * pad), OH = top + h * fit + bottom;
    if (LW - OW < 3) OW = LW;  // a chart that fills the size gives exactly the size
    if (Math.abs(LH - OH) < 3) OH = LH;
    const out = document.createElementNS(SVGNS, "svg"), r2 = (v) => +v.toFixed(2), unit = mm ? "mm" : "", per = mm ? (25.4 / 96) * s : s;
    out.setAttribute("width", r2(OW * per) + unit);
    out.setAttribute("height", r2(OH * per) + unit);
    out.setAttribute("viewBox", "0 0 " + r2(OW) + " " + r2(OH));
    out.setAttribute("font-family", fontStack(st.font_family));
    const desc = ["Figure: " + info.what + (info.detail ? " (" + info.detail + ")" : ""), "Experiment: " + clean(D.title), info.c ? "Comparison: " + clean(info.c.name) : "",
      info.cuts ? "Cut-offs: " + info.cuts : "", warn, "Analysis: " + analysisText(), D.sourceName ? "Source table: " + clean(D.sourceName) : "",
      "Export style: " + styleText(st), "Made by the Ionomos report, " + stamp()].filter(Boolean).join("\n");
    svg("title", {}, out).textContent = info.what + (info.c ? ": " + clean(info.c.name) : "");
    svg("desc", {}, out).textContent = desc;
    if (st.background !== "transparent") svg("rect", { x: 0, y: 0, width: r2(OW), height: r2(OH), fill: tk["--surface"] }, out);
    const put = (x, y, t, attrs) => { const e = svg("text", Object.assign({ x: r2(x), y: r2(y) }, attrs), out); e.textContent = t; return e; };
    let y = pad;
    if (ttl) { put(pad, y + 16, ttl, { "font-size": 16, "font-weight": 600, fill: tk["--text"] }); y += 24; }
    if (sub) { put(pad, y + 12, sub, { "font-size": 12, fill: tk["--text2"] }); y += 18; }
    if (warn) { put(pad, y + 12, warn, { "font-size": 11.5, "font-weight": 600, fill: tk["--warnink"] }); y += 18; }
    if (nrows) rows.forEach((row) => {
      row.forEach(([x, it]) => {
        if (it[0]) svg("circle", { cx: r2(pad + x + 5), cy: r2(y + 8), r: 5, fill: grey ? toGrey(it[0]) : it[0] }, out);
        put(pad + x + (it[0] ? 15 : 0), y + 12, it[1], { "font-size": 12, fill: tk["--text2"] });
      });
      y += 18;
    });
    const g = svg("g", { transform: "translate(" + r2((OW - w * fit) / 2) + " " + r2(top) + ")" + (fit < 0.999 ? " scale(" + +fit.toFixed(4) + ")" : "") }, out);
    while (body.firstChild) g.appendChild(body.firstChild);
    if (note) put(pad, top + h * fit + 14, note, { "font-size": 10, fill: tk["--muted"] });
    return { name: cap.name, svg: '<?xml version="1.0" encoding="UTF-8"?>\n' + new XMLSerializer().serializeToString(out), w: OW * s, h: OH * s, mm: mm, fit: fit, info: info, desc: desc,
      room: h === box.h ? Math.floor(LH - top - bottom) : 0 };  // a chart that took the height it was given: the height it can have
  }
  function stamp() { const d = new Date(), p = (n) => String(n).padStart(2, "0"); return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes()); }
  function styleText(st) {
    const [w, h, mm] = sizePx(st), k = mm ? 25.4 / 96 : 1;
    return SIZES[st.size][0] + " (" + +(w * k).toFixed(1) + " × " + +(h * k).toFixed(1) + (mm ? " mm" : " px") + "), text " + st.font_pt + " pt " + st.font_family + ", palette " + st.palette + ", " + st.background + " background";
  }
  // the report's parts as redraw() draws them: a chart's own part is drawn again for its export
  const STEPS = [["#volcano", renderVolcano], ["#phist", renderPHist], ["#detail", renderDetail], ["#comparebody", renderCompare], ["#enrich", renderEnrichment],
    ["#dosebody", renderDose], ["#cysbody", renderCys], ["#timebody", renderTime], ["#qc", renderQC]];
  function holdState() {
    return { ci: ST.ci, zoom: ST.zoom, mode: ST.mode, labels: ST.labels, lm: ST.opt.labelMatches, pinned: ST.pinned, focus: ST.focus, qc: qcTab, enr: enrMode, ds: DS.s, df: DS.focus,
      ora: renderORA._st && Object.assign({}, renderORA._st), rank: renderRank._st && Object.assign({}, renderRank._st), psm: qcPsm._st && Object.assign({}, qcPsm._st), pcn: qcPCA._st && qcPCA._st.names };
  }
  function restoreState(h) {
    ST.ci = h.ci; ST.zoom = h.zoom; ST.mode = h.mode; ST.labels = h.labels; ST.opt.labelMatches = h.lm; ST.pinned = h.pinned; ST.focus = h.focus; qcTab = h.qc; enrMode = h.enr; DS.s = h.ds; DS.focus = h.df;
    [[renderORA, h.ora], [renderRank, h.rank], [qcPsm, h.psm]].forEach(([fn, was]) => { if (was && fn._st) Object.assign(fn._st, was); else if (!was) delete fn._st; });
    if (qcPCA._st && h.pcn != null) qcPCA._st.names = h.pcn;
  }
  /** Draw `fig` with the style and return its finished figures ([] when it draws nothing here). */
  function figuresOf(fig, st, keep) {
    const [PW, PH] = sizePx(st), s = st.font_pt / 9, pad = 12, tk = tokensOf(st), held = holdState(), out = [];
    const box = { w: Math.max(200, Math.floor(PW / s - 2 * pad)), h: Math.max(120, Math.floor(PH / s - 1.6 * pad - (st.title ? 24 : 0) - (st.subtitle ? 18 : 0) - (st.legend ? 22 : 0) - (st.note ? 20 : 0))) };
    // which names are written on the plot
    if (st.labels !== "screen") { ST.labels = st.labels === "top" ? (st.label_count == null ? ST.labels : st.label_count) : 0; }
    if (st.labels === "top" || st.labels === "none") { ST.opt.labelMatches = false; ST.pinned = []; ST.focus = null; }
    if (st.labels === "none" && qcPCA._st) qcPCA._st.names = false;
    const pass = (bx) => {
      EX = { w: bx.w, h: bx.h, tokens: tk, grey: st.palette === "grey", cap: [] };
      fig.draw();
      let caps = EX.cap;
      if (fig.name) caps = caps.filter((c) => c.name === fig.name);
      else if (fig.pick) caps = caps.filter((c) => c.name.indexOf(fig.pick) === 0);
      return caps.map((c) => composeFigure(c, st, bx, tk));
    };
    try {
      pass(box).forEach((f) => out.push(f));
      // the space kept for a legend or a title was a guess: a chart that can take any height is drawn once more at the height that is left
      const loose = out.find((f) => f.room && Math.abs(f.room - box.h) > 2);
      if (loose) pass({ w: box.w, h: Math.max(120, loose.room) }).forEach((f) => { const k = out.findIndex((o) => o.name === f.name); if (k >= 0 && out[k].room) out[k] = f; });
      if (!out.length && fig.root) out.push(composeFigure({ host: fig.host, root: fig.root, name: fig.name }, st, box, tk));  // not drawn again: the chart as it was
    } catch (e) {
      if (window.console && console.warn) console.warn("Ionomos export:", e);
    } finally {
      EX = null;
      restoreState(held);
    }
    if (!keep) (fig.restore || redrawAll)();
    return out;
  }
  function redrawAll() { syncControls(); redraw(); }
  /** The figure behind a chart's own buttons: its part of the report (or the whole report) is drawn again. */
  function figOfChart(t) {
    const step = t.again ? null : STEPS.find(([sel]) => { const e = $(sel); return e && e.contains(t.host); });
    const fn = t.again || (step ? step[1] : redraw);
    return { name: t.name, host: t.host, root: t.root, label: figInfo(t.name).what, draw: fn, restore: t.root ? fn : () => {} };
  }
  /** Every figure the report can draw: each comparison, each QC tab, each enrichment with a result. */
  function allFigs() {
    const out = [], add = (label, draw, pick) => out.push({ label: label, draw: draw, pick: pick });
    D.comps.forEach((c, k) => {
      add("Volcano: " + c.name, () => { ST.ci = k; ST.zoom = null; ST.mode = "volcano"; renderVolcano(); });
      if (c.conf !== "none") add("p-values: " + c.name, () => { ST.ci = k; renderPHist(); });
    });
    if (D.comps.length >= 2) add("Compare comparisons", renderCompare);
    if (D.qc.heatmap && D.qc.heatmap.rows.length) add("Heatmap of significant features", heatmapSvg);
    D.enr.filter((b) => b.terms.some((t) => t.q <= 0.05)).forEach((b) => add("Enrichment: " + b.comparison + ", " + b.direction + " hits, " + b.library,
      () => { enrMode = "ora"; renderORA._st = Object.assign(renderORA._st || {}, { comp: b.comparison, dir: b.direction, lib: b.library }); renderEnrichment(); }, "enrichment_"));
    D.gsea.filter((b) => b.terms.some((t) => t.q <= 0.05)).forEach((b) => add("Gene sets by rank: " + b.comparison + ", " + b.library,
      () => { enrMode = "rank"; renderRank._st = Object.assign(renderRank._st || { dir: "both" }, { comp: b.comparison, lib: b.library, sel: null }); renderEnrichment(); }, "gene_set_ranks_"));
    $$("#qc > .tabs button").forEach((b) => {
      const k = b.dataset.k;
      if (k === "psm") [["ppm", "mass error"], ["mc", "missed cleavages"], ["z", "charge states"], ["len", "peptide length"]].forEach(([ch, t]) => add("QC: Search quality, " + t, () => { qcTab = "psm"; qcPsm._st = Object.assign(qcPsm._st || {}, { chart: ch }); renderQC(); }));
      else if (k !== "card") add("QC: " + b.textContent, () => { qcTab = k; renderQC(); });
    });
    ((D.dose && D.dose.ran && D.dose.series) || []).forEach((S, k) => add("Dose-response: " + (S.name || "curves"), () => { DS.s = k; DS.focus = null; renderDose(); }, "dose_potency_"));
    return out;
  }
  /** Every figure, finished: the list above, then whatever else the report shows now (an open protein, a
   * time course, liganded sites, a section added later). One of each name. */
  function collectAll(st) {
    const out = [], seen = new Set();
    allFigs().concat([{ draw: redraw }]).forEach((f) => figuresOf(f, st, true).forEach((x) => { if (!seen.has(x.name)) { seen.add(x.name); out.push(x); } }));
    redrawAll();
    return out;
  }
  /** The heatmap as SVG (on screen it is a canvas), drawn off the page and handed to the export. */
  function heatmapSvg() {
    const hm = D.qc.heatmap;
    if (!hm || !hm.rows.length) return;
    const host = document.createElement("div"), cols = hm.cols, rows = hm.rows, vals = hm.values, topH = 90, W = widthOf(host);
    const cellH = Math.max(2, Math.min(16, (heightOf(topH + 8 + rows.length * 10) - topH - 8) / rows.length)), names = rows.length <= 80 && cellH >= 9, labW = names ? 110 : 8;
    const cellW = Math.max(6, Math.min(64, (W - labW - 14) / cols.length));
    const root = frame(host, Math.round(labW + cols.length * cellW + 10), Math.round(topH + rows.length * cellH + 8)), g = svg("g", {}, root);
    const all = [];
    vals.forEach((r) => r.forEach((v) => v != null && all.push(Math.abs(v))));
    all.sort((a, b) => a - b);
    const lim = Math.max(0.5, all[Math.floor(all.length * 0.95)] || 1), grey = EX && EX.grey, t2 = css("--text2"), sunk = css("--sunk");
    // without colour, one ramp from light (low) to dark (high); otherwise the report's blue-to-red
    const shade = (v) => { const k = Math.round(245 - ((Math.max(-1, Math.min(1, v / lim)) + 1) / 2) * 225).toString(16).padStart(2, "0"); return "#" + k + k + k; };
    cols.forEach((j, k) => {
      const t = text(g, 0, 0, D.samples[j].slice(0, 16), { "font-size": 11, fill: t2, transform: "translate(" + +(labW + k * cellW + cellW / 2 + 3).toFixed(1) + "," + (topH - 14) + ") rotate(-60)" });
      t.setAttribute("x", 0);
      svg("rect", { x: +(labW + k * cellW).toFixed(1), y: topH - 10, width: +(cellW - 1).toFixed(1), height: 7, fill: condColor(D.cond[j]) }, g);
    });
    rows.forEach((i, r) => {
      const y = +(topH + r * cellH).toFixed(2);
      vals[r].forEach((v, k) => svg("rect", { x: +(labW + k * cellW).toFixed(1), y: y, width: +(cellW - (cellW > 10 ? 1 : 0)).toFixed(1), height: +(cellH - (cellH > 6 ? 1 : 0)).toFixed(2), fill: v == null ? sunk : grey ? shade(v) : divColor(v, lim) }, g));
      if (anyMark() && matches(i)) svg("rect", { x: labW - 4, y: y, width: 3, height: +(cellH - 1).toFixed(2), fill: css("--sel") }, g);
      if (names) text(g, labW - 6, y + cellH - 2, nameOf(i).slice(0, 16), { "text-anchor": "end", "font-size": 11, fill: t2 });
    });
    host.insertAdjacentHTML("beforeend", "<div class='legend'><span><span class='sw' style='background:" + (grey ? shade(-lim) : css("--down")) + "'></span>below the " + esc(D.levelWord) + "'s mean</span><span><span class='sw' style='background:" +
      (grey ? shade(lim) : css("--up")) + "'></span>above</span><span>full colour at ±" + fmt(lim, 1) + " log2 · " + rows.length + " of " + fmtInt(hm.total_significant) + " significant, clustered</span></div>");
    D.conditions.forEach((c) => $(".legend", host).insertAdjacentHTML("beforeend", "<span><span class='sw' style='background:" + condColor(c) + "'></span>" + esc(c) + "</span>"));
    svgTools(host, root, "heatmap");
  }

  // ---- files: PNG, zip, tables
  function utf8(s) {
    let b;
    try { b = unescape(encodeURIComponent(s)); } catch (e) { b = unescape(encodeURIComponent(String(s).replace(/[\ud800-\udfff]/g, "?"))); }  // a lone surrogate
    const u = new Uint8Array(b.length);
    for (let i = 0; i < b.length; i++) u[i] = b.charCodeAt(i);
    return u;
  }
  let CRCT = null;
  function crc32(u8) {
    if (!CRCT) { CRCT = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; CRCT[n] = c >>> 0; } }
    let c = 0xffffffff;
    for (let i = 0; i < u8.length; i++) c = CRCT[(c ^ u8[i]) & 0xff] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  }
  /** A .zip of [{ name, data: Uint8Array }], stored without compression (so it needs no library): a local header
   * and the bytes per file, then the central directory. Names are UTF-8 (here always ASCII: safeName). */
  function zipStore(files, when) {
    const d = when || new Date(), time = (d.getHours() << 11) | (d.getMinutes() << 5) | (d.getSeconds() >> 1), date = ((Math.max(1980, d.getFullYear()) - 1980) << 9) | ((d.getMonth() + 1) << 5) | d.getDate();
    const parts = [], central = [];
    let off = 0;
    files.forEach((f) => {
      const name = utf8(f.name), crc = crc32(f.data), n = f.data.length;
      const lh = new Uint8Array(30 + name.length), lv = new DataView(lh.buffer);
      lv.setUint32(0, 0x04034b50, true); lv.setUint16(4, 20, true); lv.setUint16(6, 0x0800, true); lv.setUint16(10, time, true); lv.setUint16(12, date, true);
      lv.setUint32(14, crc, true); lv.setUint32(18, n, true); lv.setUint32(22, n, true); lv.setUint16(26, name.length, true);
      lh.set(name, 30);
      const ch = new Uint8Array(46 + name.length), cv = new DataView(ch.buffer);
      cv.setUint32(0, 0x02014b50, true); cv.setUint16(4, 20, true); cv.setUint16(6, 20, true); cv.setUint16(8, 0x0800, true); cv.setUint16(12, time, true); cv.setUint16(14, date, true);
      cv.setUint32(16, crc, true); cv.setUint32(20, n, true); cv.setUint32(24, n, true); cv.setUint16(28, name.length, true); cv.setUint32(42, off, true);
      ch.set(name, 46);
      parts.push(lh, f.data);
      central.push(ch);
      off += lh.length + n;
    });
    const end = new Uint8Array(22), ev = new DataView(end.buffer);
    ev.setUint32(0, 0x06054b50, true); ev.setUint16(8, files.length, true); ev.setUint16(10, files.length, true);
    ev.setUint32(12, central.reduce((a, b) => a + b.length, 0), true); ev.setUint32(16, off, true);
    return new Blob(parts.concat(central, [end]), { type: "application/zip" });
  }
  function bytesOf(blob, cb) {
    try {
      const fr = new FileReader();
      fr.onload = () => cb(new Uint8Array(fr.result));
      fr.onerror = () => cb(null);
      fr.readAsArrayBuffer(blob);
    } catch (e) { cb(null); }
  }
  /** The PNG with its print size (pHYs, so 300 dpi means 300 dpi in Word or InDesign) and the figure's
   * description (iTXt "Description": experiment, comparison, cut-offs), put in after the header chunk. */
  function pngMeta(u8, dpi, desc) {
    if (u8.length < 45 || u8[0] !== 0x89 || u8[1] !== 0x50 || u8[2] !== 0x4e || u8[3] !== 0x47) return u8;  // not a PNG: as it is
    const chunk = (type, data) => {
      const o = new Uint8Array(12 + data.length), v = new DataView(o.buffer);
      v.setUint32(0, data.length);
      for (let i = 0; i < 4; i++) o[4 + i] = type.charCodeAt(i);
      o.set(data, 8);
      v.setUint32(8 + data.length, crc32(o.subarray(4, 8 + data.length)));
      return o;
    };
    const phys = new Uint8Array(9), pv = new DataView(phys.buffer), ppm = Math.round(dpi / 0.0254);
    pv.setUint32(0, ppm); pv.setUint32(4, ppm); phys[8] = 1;
    const key = "Description", body = utf8(desc), itxt = new Uint8Array(key.length + 5 + body.length);
    for (let i = 0; i < key.length; i++) itxt[i] = key.charCodeAt(i);
    itxt.set(body, key.length + 5);  // keyword, 0, not compressed (0, 0), no language (0), no translated keyword (0), the text
    const parts = [u8.subarray(0, 33), chunk("pHYs", phys), chunk("iTXt", itxt)], dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
    for (let p = 33; p + 12 <= u8.length;) {  // the rest, without a pHYs the browser may have written
      const n = dv.getUint32(p), type = String.fromCharCode(u8[p + 4], u8[p + 5], u8[p + 6], u8[p + 7]);
      if (type !== "pHYs") parts.push(u8.subarray(p, p + 12 + n));
      p += 12 + n;
    }
    const out = new Uint8Array(parts.reduce((a, b) => a + b.length, 0));
    let at = 0;
    parts.forEach((x) => { out.set(x, at); at += x.length; });
    return out;
  }
  /** [scale, dots per inch] of the PNG for a figure of w x h px. */
  function pngScale(st, w, h) {
    const k = Math.min(st.png_dpi ? st.png_dpi / 96 : st.png_scale, 16000 / Math.max(w, h));  // a canvas is at most ~16,000 px a side
    return [k, 96 * k];
  }
  /** cb(Blob or null): the figure as a PNG, drawn by the browser from its SVG. */
  function pngOf(f, st, done) {
    let over = false;
    const timer = setTimeout(() => cb(null), 20000);  // a browser that neither draws the picture nor says why
    const cb = (b) => { if (!over) { over = true; clearTimeout(timer); done(b); } };
    try {
      const [k, dpi] = pngScale(st, f.w, f.h), img = new Image();
      img.onload = () => {
        try {
          const cv = document.createElement("canvas");
          cv.width = Math.round(f.w * k); cv.height = Math.round(f.h * k);
          cv.getContext("2d").drawImage(img, 0, 0, cv.width, cv.height);
          if (!cv.toBlob) { cb(null); return; }
          cv.toBlob((b) => { if (!b) cb(null); else bytesOf(b, (u8) => cb(u8 ? new Blob([pngMeta(u8, dpi, f.desc)], { type: "image/png" }) : b)); }, "image/png");
        } catch (e) { cb(null); }
      };
      img.onerror = () => cb(null);
      img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(f.svg);
    } catch (e) { cb(null); }  // PNG is a convenience; SVG always works
  }
  // a text cell: quoted, and one that a spreadsheet would run as a formula (= + - @) starts with an apostrophe
  const csvCell = (v) => { const s = String(v == null ? "" : v).replace(/"/g, '""'); return '"' + (/^[=+\-@\t\r]/.test(s) ? "'" : "") + s + '"'; };
  function resultsCsv(c, rows) {
    const head = ["id", "label", "description", "log2fc", "ci_low", "ci_high", "p", "adj_p", "significant", "imputation_driven"].concat(D.F ? ["any_change_F_adj_p"] : [], D.f.pep ? [D.evidence || "peptides"] : [], D.samples);
    const imp = impDriven(c);
    return [head.map(csvCell).join(",")].concat(rows.map((i) => [csvCell(D.f.id[i]), csvCell(D.f.label[i]), csvCell(D.f.desc[i]), c.fc[i], c.ciL[i], c.ciR[i], c.p[i], c.q[i], sigOf(c, i), imp[i] ? "yes" : ""]
      .concat(D.F ? [D.F.q[i]] : [], D.f.pep ? [D.f.pep[i]] : [], D.v[i]).map((v) => (v == null ? "" : v)).join(","))).join("\n");
  }
  /** The tables that go with the figures: [file name, what it is, text]. */
  function tablesOf() {
    const out = [];
    D.comps.forEach((c) => {
      const rows = [];
      for (let i = 0; i < nF; i++) if (c.p[i] != null || c.fc[i] != null) rows.push(i);
      rows.sort((a, b) => (c.p[a] == null ? 2 : c.p[a]) - (c.p[b] == null ? 2 : c.p[b]));
      out.push(["results_" + c.slug + ".csv", "Every tested " + D.levelWord + " of " + clean(c.name) + ": fold change, p, adjusted p, hit or not at " + cutText(c) + ", and its values per sample", resultsCsv(c, rows)]);
    });
    const card = new Map((D.qc.scorecard || []).map((r) => [r.sample, r]));
    out.push(["samples.csv", "The samples: condition, replicate" + (card.size ? ", and the quality scorecard" : ""),
      [["sample", "condition", "replicate"].concat(card.size ? ["status", "identifications", "missing_pct", "r_with_replicates", "flags"] : []).map(csvCell).join(",")].concat(D.samples.map((s, j) => {
        const r = card.get(s);
        return [csvCell(s), csvCell(D.cond[j]), D.rep && D.rep[j] != null ? D.rep[j] : ""].concat(card.size ? (r ? [csvCell(r.status), r.ids, r.missing_pct, r.corr_group, csvCell((r.flags || []).join("; "))] : ["", "", "", "", ""]) : []).map((v) => (v == null ? "" : v)).join(",");
      })).join("\n")]);
    if (D.enr.some((b) => b.terms.length)) out.push(["enrichment.csv", "Gene sets over-represented among the hits (the report's saved cut-offs)",
      [["comparison", "direction", "library", "term", "overlap", "set_size", "p", "adj_p", "genes"].map(csvCell).join(",")].concat(D.enr.flatMap((b) => b.terms.map((t) =>
        [csvCell(b.comparison), csvCell(b.direction), csvCell(b.library), csvCell(t.term), t.k, t.K, t.p, t.q, csvCell(t.genes.join(" "))].join(",")))).join("\n")]);
    if (D.gsea.some((b) => b.terms.length)) out.push(["gene_set_ranks.csv", "Gene sets tested on every ranked " + D.levelWord + " (no cut-off)",
      [["comparison", "library", "term", "genes_measured", "z", "p", "adj_p", "direction", "leading_genes"].map(csvCell).join(",")].concat(D.gsea.flatMap((b) => b.terms.map((t) =>
        [csvCell(b.comparison), csvCell(b.library), csvCell(t.term), t.n, t.z, t.p, t.q, csvCell(t.dir), csvCell(t.leading.join(" "))].join(",")))).join("\n")]);
    return out;
  }
  /** cb(Blob, number of figures): every figure at the style, the tables, the style and a README, in one .zip. */
  function exportAll(st, cb) {
    const figs = collectAll(st), base = safeName(D.title || "experiment").slice(0, 60) + "_figures/", used = new Set(), files = [], listed = [];
    const uniq = (n) => { let x = n, k = 2; while (used.has(x.toLowerCase())) x = n.replace(/(\.[A-Za-z0-9]+)?$/, "_" + k++ + "$1"); used.add(x.toLowerCase()); return x; };
    const wantSvg = st.zip_format !== "png", wantPng = st.zip_format !== "svg";
    const finish = () => {
      tablesOf().forEach(([n, what, txt]) => { const name = uniq("tables/" + safeName(n)); files.push({ name: base + name, data: utf8("﻿" + txt + "\n") }); listed.push([name, what]); });
      files.push({ name: base + "export_style.json", data: utf8(styleJson(st)) });
      listed.push(["export_style.json", "The export style these figures were made with; load it in another report (Export, Load style) or pass it to: ionomos export --style"]);
      files.push({ name: base + "README.txt", data: utf8("﻿" + readmeText(st, listed)) });
      cb(zipStore(files), figs.length);
    };
    const step = (k) => {
      if (k >= figs.length) { finish(); return; }
      const f = figs[k], stem = "figures/" + String(k + 1).padStart(2, "0") + "_" + safeName(f.name).slice(0, 80);
      const what = f.info.what + (f.info.c ? ", " + clean(f.info.c.name) : f.info.detail ? ", " + f.info.detail : "") + (f.info.cuts ? ". Cut-offs: " + f.info.cuts : "") + (f.fit < 0.97 ? ". Scaled to " + Math.round(f.fit * 100) + "% to fit the size" : "");
      if (wantSvg) { const n = uniq(stem + ".svg"); files.push({ name: base + n, data: utf8(f.svg) }); listed.push([n, what]); }
      if (!wantPng) { step(k + 1); return; }
      pngOf(f, st, (blob) => {
        if (!blob) { if (!wantSvg) { const n = uniq(stem + ".svg"); files.push({ name: base + n, data: utf8(f.svg) }); listed.push([n, what + " (SVG: this browser could not make the PNG)"]); } step(k + 1); return; }
        bytesOf(blob, (u8) => { if (u8) { const n = uniq(stem + ".png"); files.push({ name: base + n, data: u8 }); listed.push([n, what]); } step(k + 1); });
      });
    };
    step(0);
  }
  const styleJson = (st) => JSON.stringify(Object.assign({ ionomos_export_style: 1 }, st), null, 2) + "\n";
  function readmeText(st, listed) {
    const L = ["Figures and tables from the Ionomos report", "", "Experiment:  " + clean(D.title), "Exported:    " + stamp(), D.sourceName ? "Source:      " + clean(D.sourceName) : "",
      "", "Cut-offs", "  In the report as it was open (volcano, compare, overlap, results tables):  " + cutText(null) + (filterText() ? "; " + filterText() : ""),
      "  Saved with the report (heatmap, over-representation, the TSV files):       " + savedCutText(), "  " + analysisText(),
      "", "Style", "  " + styleText(st), "", "Files"];
    const wide = Math.min(60, Math.max(...listed.map((x) => x[0].length)));
    listed.forEach(([n, what]) => L.push("  " + n.padEnd(wide) + "  " + clean(what)));
    L.push("", "SVG files keep their text as text and can be ungrouped and edited in PowerPoint, Illustrator or Inkscape.",
      "Every figure carries these cut-offs in its file (SVG: <desc>, PNG: the Description field), so a figure on a slide can be traced back.",
      "Enrichment figures are made for gene-set libraries with a term at adjusted p <= 0.05; open the report for the others.");
    return L.filter((x) => x != null).join("\r\n") + "\r\n";
  }
  function exportOne(chart, kind) {
    const f = figuresOf(figOfChart(chart), XS)[0];
    if (!f) return;
    if (kind === "svg") download(f.name + ".svg", f.svg, "image/svg+xml");
    else pngOf(f, XS, (b) => b && download(f.name + ".png", b));
  }
  /** "Export for slides": the zip, with a word in `status` while it is made. */
  function exportZip(status) {
    if (exportZip.busy) return;
    exportZip.busy = true;
    if (status) status.textContent = "Drawing every figure…";
    setTimeout(() => {  // let the page show the message first
      const done = (msg) => { exportZip.busy = false; if (status) flash(status, msg); };
      try {
        exportAll(XS, (blob, n) => { download((D.title || "experiment") + "_figures.zip", blob); done(n + " figures and the tables are in the .zip"); });
      } catch (e) {
        if (window.console && console.warn) console.warn("Ionomos export:", e);
        done("The .zip could not be made (" + (e && e.message) + "); the SVG button on each chart still works.");
      }
    }, 20);
  }

  // ---- the dialog
  let XD = null;  // while the dialog is open: { figs, fig, last }
  function exportDialog() {
    let dlg = $("#xdlg");
    if (dlg) return dlg;
    dlg = document.createElement("div");
    dlg.id = "xdlg"; dlg.className = "modal"; dlg.hidden = true;
    const opts = (pairs) => pairs.map(([v, t]) => "<option value='" + esc(v) + "'>" + esc(t) + "</option>").join("");
    dlg.innerHTML = "<div class='modalbox' role='dialog' aria-modal='true' aria-labelledby='xdlgh'>" +
      "<div class='hh'><h3 id='xdlgh'>Export figures</h3><button type='button' id='xclose' aria-label='Close'>×</button></div>" +
      "<div class='xgrid'><div class='xform'>" +
      "<fieldset><legend>Figure</legend>" +
      "<label class='ctl' title='Which figure is shown on the right and downloaded by the buttons under it'>Figure <select id='xfig'></select></label>" +
      "<label class='ctl' title='A title above the figure. Leave the box empty for the automatic one'><input type='checkbox' id='xtitle'> Title <input type='text' id='xtitletext'></label>" +
      "<label class='ctl' title='A second, smaller line under the title'><input type='checkbox' id='xsub'> Subtitle <input type='text' id='xsubtext'></label>" +
      "<label class='ctl' title='What the colours mean, above the plot'><input type='checkbox' id='xlegend'> Legend</label>" +
      "<label class='ctl' title='A small line under the figure with the experiment, the cut-offs and the test. The same facts are always written into the file itself'><input type='checkbox' id='xnote'> Cut-offs under the figure</label>" +
      "<label class='ctl' title='Which names are written on the plot'>Names on the plot <select id='xlabels'>" + opts([["screen", "as in the report"], ["top", "the top hits only"], ["marked", "pinned and searched only"], ["none", "none"]]) + "</select></label>" +
      "<label class='ctl' title='How many of the most significant hits are named (empty: as in the report)'>Top hits named <input type='number' id='xnlab' min='0' max='200' step='1'></label>" +
      "</fieldset><fieldset><legend>Size</legend>" +
      "<label class='ctl' title='The size of the whole figure. A figure that cannot fill it (a square heatmap on a wide slide) is made smaller, never stretched'>Size <select id='xsize'>" + opts(Object.keys(SIZES).map((k) => [k, SIZES[k][0] + (SIZES[k][1] ? " · " + SIZES[k][1] + " × " + SIZES[k][2] + " " + SIZES[k][3] : "")])) + "</select></label>" +
      "<label class='ctl' title='Width and height of a custom size'>Width <input type='number' id='xw' min='20' max='8000'> Height <input type='number' id='xh' min='20' max='8000'> <select id='xunit' aria-label='Unit'>" + opts([["px", "px"], ["mm", "mm"]]) + "</select></label>" +
      "</fieldset><fieldset><legend>Text and marks</legend>" +
      "<label class='ctl' title='The size of the axis numbers in the finished figure, in points (titles are larger in proportion). On a slide 14 pt reads well; journals ask for 6 to 8 pt'>Text size <input type='number' id='xfont' min='4' max='48' step='0.5'> pt</label>" +
      "<label class='ctl' title='The font written into the file, with common fallbacks. It must be installed where the figure is opened'>Font <select id='xfam'>" + opts(FONTS.map((f) => [f, f]).concat([["other", "another font…"]])) + "</select> <input type='text' id='xfamtext' aria-label='Font name' placeholder='font name' maxlength='40'></label>" +
      "<label class='ctl' title='Thickness of every line, as a multiple of the normal one'>Line width × <input type='number' id='xline' min='0.25' max='4' step='0.25'></label>" +
      "<label class='ctl' title='Size of every point, as a multiple of the normal one'>Point size × <input type='number' id='xpoint' min='0.25' max='4' step='0.25'></label>" +
      "</fieldset><fieldset><legend>Colours</legend>" +
      "<label class='ctl' title='Colour-blind safe uses the Okabe and Ito colours; greyscale is for print without colour'>Palette <select id='xpal'>" + opts([["default", "Ionomos (red up, blue down)"], ["colorblind", "colour-blind safe"], ["grey", "greyscale"], ["custom", "my own colours"]]) + "</select></label>" +
      "<label class='ctl' id='xcustom' title='Your own colours for up, down and not significant'>Up <input type='color' id='xup'> Down <input type='color' id='xdown'> Not significant <input type='color' id='xns'></label>" +
      "<label class='ctl' title='Transparent leaves the slide background showing through (dark text)'>Background <select id='xbg'>" + opts([["light", "white"], ["dark", "dark"], ["transparent", "transparent"]]) + "</select></label>" +
      "</fieldset><fieldset><legend>PNG</legend>" +
      "<label class='ctl' title='How many pixels a PNG gets. 2× is sharp on a slide; printers ask for 300 dpi or more'>Resolution <select id='xres'>" + opts([["x1", "1×"], ["x2", "2× (slides)"], ["x3", "3×"], ["x4", "4×"], ["d150", "150 dpi"], ["d300", "300 dpi (print)"], ["d600", "600 dpi"]]) + "</select></label> <span id='xpx' class='muted'></span>" +
      "</fieldset><fieldset><legend>House style</legend>" +
      "<div class='chips'><button type='button' id='xreset' title='Every setting here back to the lab defaults (config.yaml analysis.export), and forget the style kept in this browser'>Reset to lab defaults</button>" +
      "<button type='button' id='xsave' title='Save these settings as a small file to share with the lab'>Save style</button>" +
      "<label class='filebtn' title='Load a style file saved here or by a colleague'>Load style<input type='file' id='xload' accept='.json,application/json'></label></div>" +
      "<p class='muted'>These settings are kept in this browser and used by every Ionomos report you open in it.</p>" +
      "</fieldset></div><div class='xside'><div id='xprev' class='xprev'></div><div id='xinfo' class='muted'></div>" +
      "<div class='chips'><button type='button' id='xsvg' title='This figure as SVG: text stays text, so it can be edited in PowerPoint, Illustrator or Inkscape'>Download SVG</button>" +
      "<button type='button' id='xpng' title='This figure as a PNG picture at the resolution chosen'>Download PNG</button>" +
      "<button type='button' id='xcopy' title='Put this figure on the clipboard as a picture, to paste into a slide'>Copy image</button></div>" +
      "<div class='chips'><label class='ctl' title='What the .zip holds for each figure'>In the .zip <select id='xzipfmt'>" + opts([["both", "SVG and PNG"], ["svg", "SVG"], ["png", "PNG"]]) + "</select></label>" +
      "<button type='button' id='xzip' title='Every figure of this report with these settings, the tables as CSV and a README, in one .zip'>Export for slides (.zip)</button></div>" +
      "<div id='xmsg' class='muted' role='status'></div></div></div></div>";
    document.body.appendChild(dlg);
    addHelp($(".hh h3", dlg), "report.export", $(".hh", dlg));
    $("#xclose").onclick = closeExport;
    dlg.addEventListener("mousedown", (e) => { if (e.target === dlg) closeExport(); });
    let timer = null;
    const changed = (e) => {
      if (e.target.id === "xload") return;
      if (e.target.id === "xfam" && e.target.value === "other") { $("#xfamtext").hidden = false; $("#xfamtext").focus(); return; }  // then the name is typed
      if (e.target.id === "xsize" && e.target.value !== "custom") $("#xfont").value = SIZES[e.target.value][4];
      if (e.target.id === "xfig") XD.fig = XD.figs[+e.target.value] || XD.fig;
      else if (e.target.id === "xtitletext" || e.target.id === "xsubtext") { if (XD.last) FIG_TEXT[XD.last.name] = { title: $("#xtitletext").value.trim(), subtitle: $("#xsubtext").value.trim() }; }
      else {
        XS = readStyle(); saveStyle();
        if (e.type === "change" && e.target.id === "xfamtext" && XS.font_family !== e.target.value.trim()) $("#xmsg").textContent = "A font name may hold letters, digits, spaces, - and _ only; the font was not changed.";
      }
      clearTimeout(timer);
      if (e.type === "change") { writeStyle(); renderPreview(); } else timer = setTimeout(renderPreview, 200);
    };
    $(".xform", dlg).addEventListener("change", changed);
    $(".xform", dlg).addEventListener("input", changed);
    $("#xzipfmt").onchange = () => { XS = readStyle(); saveStyle(); };
    $("#xreset").onclick = () => { XS = normStyle(null, LAB); store.set(STYLE_KEY, null); writeStyle(); renderPreview(); flash($("#xmsg"), "Back to the lab defaults."); };
    $("#xsave").onclick = () => download("ionomos_export_style.json", styleJson(XS), "application/json");
    $("#xload").onchange = (e) => {
      const f = e.target.files && e.target.files[0];
      e.target.value = "";
      if (!f) return;
      if (f.size > 20000) { $("#xmsg").textContent = "That file is too large to be an export style; nothing was changed."; return; }
      const fr = new FileReader();
      fr.onload = () => { $("#xmsg").textContent = loadStyle(String(fr.result || "")); writeStyle(); renderPreview(); };
      fr.readAsText(f);
    };
    $("#xsvg").onclick = () => { const f = renderPreview(); if (f) download(f.name + ".svg", f.svg, "image/svg+xml"); };
    $("#xpng").onclick = () => { const f = renderPreview(); if (f) pngOf(f, XS, (b) => (b ? download(f.name + ".png", b) : ($("#xmsg").textContent = "This browser could not make the PNG; the SVG works everywhere."))); };
    $("#xcopy").onclick = () => { const f = renderPreview(); if (f) copyFigure(f, (ok) => ($("#xmsg").textContent = ok ? "Copied: paste it into a slide." : "This browser does not let a page copy pictures; use Download PNG.")); };
    $("#xzip").onclick = () => exportZip($("#xmsg"));
    return dlg;
  }
  /** Take a style file's text (untrusted): returns what to tell the user. Only a valid style changes anything. */
  function loadStyle(txt) {
    let raw;
    try { raw = JSON.parse(txt); } catch (e) { return "That file is not JSON; nothing was changed."; }
    if (!raw || typeof raw !== "object" || Array.isArray(raw) || raw.ionomos_export_style !== 1) return "That file is not an Ionomos export style; nothing was changed.";
    const bad = [];
    XS = normStyle(raw, LAB, bad);
    saveStyle();
    return "Style loaded." + (bad.length ? " Not understood and left out: " + bad.slice(0, 12).map((k) => String(k).slice(0, 30)).join(", ") + (bad.length > 12 ? " …" : "") + "." : "");
  }
  function readStyle() {
    const val = (id) => $("#" + id).value, num = (id) => parseFloat(val(id)), on = (id) => $("#" + id).checked, res = val("xres");
    const nl = val("xnlab").trim();
    return normStyle({ size: val("xsize"), width: num("xw"), height: num("xh"), unit: val("xunit"), font_pt: num("xfont"), font_family: val("xfam") === "other" ? val("xfamtext").trim() : val("xfam"),
      line_scale: num("xline"), point_scale: num("xpoint"), palette: val("xpal"), up: val("xup"), down: val("xdown"), neutral: val("xns"), background: val("xbg"),
      title: on("xtitle"), subtitle: on("xsub"), legend: on("xlegend"), note: on("xnote"), labels: val("xlabels"), label_count: nl === "" ? null : parseInt(nl, 10),
      png_scale: res[0] === "x" ? +res.slice(1) : XS.png_scale, png_dpi: res[0] === "d" ? +res.slice(1) : 0, zip_format: val("xzipfmt") }, XS);
  }
  function writeStyle() {
    const set = (id, v) => { const e = $("#" + id); if (e) e.value = v; }, own = XS.size === "custom", z = SIZES[XS.size];
    set("xsize", XS.size); set("xw", own ? XS.width : z[1]); set("xh", own ? XS.height : z[2]); set("xunit", own ? XS.unit : z[3]);
    ["xw", "xh", "xunit"].forEach((id) => ($("#" + id).disabled = !own));
    set("xfont", XS.font_pt); set("xline", XS.line_scale); set("xpoint", XS.point_scale); set("xpal", XS.palette); set("xbg", XS.background);
    const listed = FONTS.includes(XS.font_family);
    set("xfam", listed ? XS.font_family : "other"); set("xfamtext", listed ? "" : XS.font_family);
    $("#xfamtext").hidden = listed;
    set("xup", XS.up); set("xdown", XS.down); set("xns", XS.neutral);
    $("#xcustom").hidden = XS.palette !== "custom";
    $("#xtitle").checked = XS.title; $("#xsub").checked = XS.subtitle; $("#xlegend").checked = XS.legend; $("#xnote").checked = XS.note;
    set("xlabels", XS.labels); set("xnlab", XS.label_count == null ? "" : XS.label_count);
    $("#xnlab").disabled = XS.labels !== "top";
    set("xres", XS.png_dpi ? "d" + XS.png_dpi : "x" + XS.png_scale); set("xzipfmt", XS.zip_format);
  }
  /** Draw the chosen figure with the style into the dialog; returns it (or null). */
  function renderPreview() {
    if (!XD) return null;
    const f = figuresOf(XD.fig, XS)[0] || null, host = $("#xprev"), tt = $("#xtitletext"), su = $("#xsubtext");
    XD.last = f;
    host.className = "xprev" + (XS.background === "transparent" ? " clear" : "");
    host.innerHTML = "";
    if (!f) { host.innerHTML = "<div class='empty'>This figure has nothing to draw here.</div>"; $("#xinfo").textContent = ""; return null; }
    const img = document.createElement("img");
    img.alt = "Preview of the exported figure";
    img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(f.svg);
    host.appendChild(img);
    const own = FIG_TEXT[f.name] || {}, k = f.mm ? 25.4 / 96 : 1, [ps] = pngScale(XS, f.w, f.h);
    tt.placeholder = f.info.title; su.placeholder = f.info.subtitle;
    if (document.activeElement !== tt) tt.value = own.title || "";
    if (document.activeElement !== su) su.value = own.subtitle || "";
    $("#xpx").textContent = "PNG: " + Math.round(f.w * ps) + " × " + Math.round(f.h * ps) + " px";
    $("#xinfo").textContent = +(f.w * k).toFixed(1) + " × " + +(f.h * k).toFixed(1) + (f.mm ? " mm" : " px") + " · text " + XS.font_pt + " pt" +
      (f.fit < 0.97 ? " · the chart was made " + Math.round(f.fit * 100) + "% of its size to fit, so its text is smaller than " + XS.font_pt + " pt" : "") + (f.info.cuts ? " · " + f.info.cuts : "");
    return f;
  }
  function copyFigure(f, done) {
    try {
      if (!navigator.clipboard || !navigator.clipboard.write || typeof ClipboardItem === "undefined") { done(false); return; }
      // the picture is promised inside the click, as Safari asks
      const item = new ClipboardItem({ "image/png": new Promise((res, rej) => pngOf(f, XS, (b) => (b ? res(b) : rej(new Error("no PNG"))))) });
      navigator.clipboard.write([item]).then(() => done(true), () => done(false));
    } catch (e) { done(false); }
  }
  /** Open the dialog on a chart (its own buttons) or on the report's first figure (Options, the top bar). */
  function openExport(chart) {
    const dlg = exportDialog(), figs = allFigs();
    let at = 0;
    if (chart) { const f = figOfChart(chart); f.label = "This chart: " + f.label; figs.unshift(f); }
    else if (C()) at = Math.max(0, figs.findIndex((f) => f.label === "Volcano: " + C().name));
    if (!figs.length) figs.push({ label: "The charts on this page", draw: redraw });
    XD = { figs: figs, fig: figs[at], last: null };
    $("#xfig").innerHTML = figs.map((f, k) => "<option value='" + k + "'>" + esc(f.label) + "</option>").join("");
    $("#xfig").value = String(figs.indexOf(XD.fig));
    $("#xmsg").textContent = "";
    writeStyle();
    dlg.hidden = false;
    renderPreview();
    $("#xclose").focus();
  }
  function closeExport() { const d = $("#xdlg"); if (d) d.hidden = true; XD = null; }
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && XD) closeExport(); });

  // ------------------------------------------------------------- controls
  function syncControls() {
    const sel = $("#comp");
    if (sel) sel.value = String(ST.ci);
    if ($("#lfc")) $("#lfc").value = ST.lfc;
    if ($("#alpha")) $("#alpha").value = ST.alpha;
    if ($("#adj")) $("#adj").checked = ST.adj;
    if ($("#hl")) $("#hl").innerHTML = ST.highlight ? "Marked: <b>" + esc(ST.highlightName) + "</b> (" + ST.highlight.size + ") <button id='hlclear'>clear</button>" : "";
    const hc = $("#hlclear");
    if (hc) hc.onclick = () => { ST.highlight = null; syncControls(); renderSearchInfo(); renderDiff(); };
  }
  function renderDiff() {
    ST.zoom = null;
    if (!C()) return;
    safe(renderVolcano, "#volcano");
    safe(() => renderTable(true), "#table");
    safe(renderPHist, "#phist");
    safe(renderDetail, "#detail");
    safe(renderTiles, "#tiles");
    safe(renderFindings, "#findings");
    safe(renderSearchInfo, "#searchinfo");
    safe(renderCompare, "#comparebody");
    const c = C();
    const note = $("#cutnote");
    if (note) {
      const changed = ST.lfc !== D.settings.log2fc || ST.alpha !== D.settings.alpha || ST.adj !== D.settings.use_adjusted;
      const filters = [ST.opt.hideImp ? "imputation-driven hits ignored" : "", ST.opt.minPep ? "hits need ≥ " + ST.opt.minPep + " " + (D.evidence || "peptides") : ""].filter(Boolean);
      note.innerHTML = (changed ? "Cut-offs changed here only — the TSV files, heatmap and enrichment use the saved settings (|log2FC| ≥ " + D.settings.log2fc +
        ", " + (D.settings.use_adjusted ? "adj. p" : "p") + " ≤ " + D.settings.alpha + "). " : "") + (filters.length ? "Hit filters on: " + filters.join(", ") + "." : "");
    }
    const view = $("#viewnote");
    if (view) view.textContent = "Hits here: " + cutText(c) + (c.conf === "none" || !ST.lfc ? "" : " (" + +Math.pow(2, ST.lfc).toFixed(2) + "-fold or more)") + (filterText() ? "; " + filterText() : "") + " · " + analysisText() + ". Every exported figure says the same.";
    const fold = $("#foldhint");
    if (fold) fold.textContent = ST.lfc > 0 ? "= " + +Math.pow(2, ST.lfc).toFixed(2) + "-fold" : "any change";
    $("#ma").disabled = D.kind === "ratio" || !c.t2 || c.conf === "none";
    $("#volc").disabled = c.conf === "none";
    writeHash();
  }
  // the view lives in the address (#c=1&lfc=1.5&q=BTK), so a bookmark or a shared link reopens it
  function writeHash() {
    try {
      const p = new URLSearchParams();
      if (ST.ci) p.set("c", ST.ci);
      if (ST.lfc !== D.settings.log2fc) p.set("lfc", ST.lfc);
      if (ST.alpha !== D.settings.alpha) p.set("p", ST.alpha);
      if (ST.adj !== D.settings.use_adjusted) p.set("adj", ST.adj ? 1 : 0);
      if (ST.search) p.set("q", ST.search);
      if (ST.mode !== "volcano") p.set("m", ST.mode);
      if (ST.focus != null) p.set("f", D.f.id[ST.focus]);
      const s = p.toString();
      if (!s && !/=/.test(location.hash)) return;
      try { history.replaceState(null, "", s ? "#" + s : location.pathname + location.search); }
      catch (e) { location.replace("#" + s); }  // some browsers refuse replaceState on file:// pages
    } catch (e) { /* the view just isn't remembered in the address */ }
  }
  function readHash() {
    try {
      const h = location.hash.slice(1);
      if (!h.includes("=")) return;
      const p = new URLSearchParams(h), num = (k) => (p.has(k) && !isNaN(parseFloat(p.get(k))) ? parseFloat(p.get(k)) : null);
      const c = num("c");
      if (c != null && c >= 0 && c < D.comps.length) ST.ci = Math.floor(c);
      if (num("lfc") != null) ST.lfc = num("lfc");
      if (num("p") != null) ST.alpha = num("p");
      if (p.has("adj")) ST.adj = p.get("adj") === "1";
      if (p.get("m") === "ma") ST.mode = "ma";
      if (p.has("q")) { ST.search = p.get("q"); ST.q = parseQuery(ST.search); if ($("#search")) $("#search").value = ST.search; }
      if (p.has("f")) { const k = D.f.id.indexOf(p.get("f")); if (k >= 0) ST.focus = k; }
    } catch (e) { /* a hand-edited address: ignore it */ }
  }
  function setup() {
    loadGroups();
    if (D.f.pep) { const ev = $("#evword"); if (ev) ev.textContent = D.evidence || "peptides"; } else { const w = $("#minpepwrap"); if (w) w.style.display = "none"; }
    if (!D.comps.length) {
      const d = $("#differential-body");
      if (d) d.innerHTML = "<div class='empty'>No comparisons were made" + (D.notes.length ? " — see the notes above." : ".") + "</div>";
    } else {
      readHash();
      $("#comp").innerHTML = D.comps.map((c, k) => "<option value='" + k + "'>" + esc(c.name) + "</option>").join("");
      $("#comp").onchange = (e) => { ST.ci = +e.target.value; renderDiff(); };
      const num = (id, key) => ($("#" + id).oninput = (e) => { const v = parseFloat(e.target.value); if (!isNaN(v) && v >= 0) { ST[key] = v; renderDiff(); } });
      num("lfc", "lfc");
      num("alpha", "alpha");
      $("#adj").onchange = (e) => { ST.adj = e.target.checked; renderDiff(); };
      $("#labels").oninput = (e) => { ST.labels = Math.max(0, parseInt(e.target.value, 10) || 0); renderVolcano(); };
      const box = $("#search");
      box.oninput = () => { ST.search = box.value.trim(); ST.q = parseQuery(ST.search); ST.highlight = null; syncControls(); renderSearchInfo(); renderVolcano(); renderTable(true); suggest(); safe(doseOnSearch, "#dosebody"); safe(renderCys, "#cysbody"); safe(timeOnSearch, "#timebody"); writeHash(); };
      box.onkeydown = (e) => {
        if (e.key === "ArrowDown") { if (moveSug(1)) e.preventDefault(); }
        else if (e.key === "ArrowUp") { if (moveSug(-1)) e.preventDefault(); }
        else if (e.key === "Enter" || e.key === "Tab") {
          if (sugAt >= 0 && !$("#suggest").hidden) { e.preventDefault(); pick(sugAt); }
          else if (e.key === "Enter") { $("#suggest").hidden = true; const l = markedList(); if (l.length === 1) setFocus(l[0]); }
        } else if (e.key === "Escape") { e.preventDefault(); if (!$("#suggest").hidden) $("#suggest").hidden = true; else setSearch(""); }
      };
      box.onpaste = (e) => {  // a column pasted from Excel keeps its line breaks as separators (inputs drop them)
        const t = e.clipboardData && e.clipboardData.getData("text");
        if (!t || !/[\r\n\t]/.test(t)) return;
        e.preventDefault();
        const list = t.split(/[\r\n\t]+/).map((x) => x.trim()).filter(Boolean).join(", ");
        const a = box.selectionStart == null ? box.value.length : box.selectionStart, b = box.selectionEnd == null ? a : box.selectionEnd;
        box.value = box.value.slice(0, a) + list + box.value.slice(b);
        box.dispatchEvent(new Event("input"));
      };
      box.onfocus = suggest;
      box.onblur = () => setTimeout(() => { const s = $("#suggest"); if (s) s.hidden = true; }, 120);
      $("#sigonly").onchange = (e) => { ST.sigOnly = e.target.checked; renderTable(true); };
      $("#csv").onclick = exportCSV;
      $("#copyup").onclick = () => copyGenes("up");
      $("#copydown").onclick = () => copyGenes("down");
      const seg = (a, b, key, val, after) => { $("#" + a).onclick = () => { ST[key] = val[0]; $("#" + a).className = "on"; $("#" + b).className = ""; after(); }; $("#" + b).onclick = () => { ST[key] = val[1]; $("#" + b).className = "on"; $("#" + a).className = ""; after(); }; };
      seg("volc", "ma", "mode", ["volcano", "ma"], () => { renderVolcano(); writeHash(); });
      seg("dzoom", "dsel", "drag", ["zoom", "select"], renderVolcano);
      if ($("#rsites")) seg("rsites", "rprot", "rows", ["features", "proteins"], () => renderTable(true));
      if (ST.mode === "ma") { $("#ma").className = "on"; $("#volc").className = ""; }
      $("#reset").onclick = () => { ST.lfc = D.settings.log2fc; ST.alpha = D.settings.alpha; ST.adj = D.settings.use_adjusted; syncControls(); renderDiff(); };
      $("#opts").onclick = () => openOptions();
      const opt = (id, key, parse, what) => { const el = $("#" + id); if (!el) return; el.oninput = el.onchange = () => { ST.opt[key] = parse(el); (what || renderDiff)(); }; };
      const shown = () => { if ($("#ptsizev")) $("#ptsizev").textContent = "×" + ST.opt.pt.toFixed(1); if ($("#labsizev")) $("#labsizev").textContent = ST.opt.lab + " px"; };
      opt("ptsize", "pt", (el) => parseFloat(el.value) || 1, () => { shown(); renderVolcano(); });
      opt("labsize", "lab", (el) => parseFloat(el.value) || 11.5, () => { shown(); renderVolcano(); });
      shown();
      const all = $("#optreset");
      if (all) all.onclick = () => {  // the cut-offs, the plot and the hit filters as the report was made (groups and the export style stay)
        Object.assign(ST, { lfc: D.settings.log2fc, alpha: D.settings.alpha, adj: D.settings.use_adjusted, labels: D.settings.top_labels, zoom: null });
        Object.assign(ST.opt, { pt: 1, lab: 11.5, labelMatches: true, lines: true, onoff: true, hideImp: false, minPep: 0 });
        [["labels", ST.labels], ["ptsize", 1], ["labsize", 11.5], ["minpep", 0]].forEach(([id, v]) => { if ($("#" + id)) $("#" + id).value = v; });
        [["labmatch", true], ["lines", true], ["markonoff", true], ["hideimp", false]].forEach(([id, v]) => { if ($("#" + id)) $("#" + id).checked = v; });
        shown(); syncControls(); renderDiff();
        flash($("#optmsg"), "Back to the lab defaults.");
      };
      opt("labmatch", "labelMatches", (el) => el.checked, renderVolcano);
      opt("lines", "lines", (el) => el.checked, renderVolcano);
      opt("markonoff", "onoff", (el) => el.checked, renderVolcano);
      opt("hideimp", "hideImp", (el) => el.checked);
      opt("minpep", "minPep", (el) => Math.max(0, parseInt(el.value, 10) || 0));
      $("#gadd").onclick = () => { addGroup($("#gname").value, $("#ggenes").value.split(/[\s,;]+/)); $("#gname").value = ""; $("#ggenes").value = ""; };
      $("#gexport").onclick = exportGroups;
      $("#gimport").onchange = (e) => { const f = e.target.files && e.target.files[0]; if (f) importGroups(f); e.target.value = ""; };
      renderGroups();
      syncControls();
      renderDiff();
      renderPins();
    }
    safe(renderTiles, "#tiles");
    safe(renderFindings, "#findings");
    safe(renderOnOff, "#onoffbody");
    safe(renderHeatmap, "#heatmap");
    safe(renderEnrichment, "#enrich");
    safe(renderDose, "#dosebody");
    safe(renderCys, "#cysbody"); safe(renderTime, "#timebody");
    safe(renderQC, "#qc");
    safe(renderHelp, "#helpbody");
    const xo = $("#xopen"), xz = $("#xzipnow"), sl = $("#slides");
    if (xo) xo.onclick = () => openExport(null);
    if (xz) xz.onclick = () => exportZip($("#optmsg"));
    if (sl) sl.onclick = () => exportZip($("#slidesmsg"));
    const theme = $("#theme");
    if (theme) theme.onclick = () => {
      const r = document.documentElement, cur = r.getAttribute("data-theme");
      const dark = cur ? cur === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
      r.setAttribute("data-theme", dark ? "light" : "dark");
      redraw();
    };
    const share = $("#share");
    if (share) share.onclick = () => { writeHash(); copyText(location.href, () => { share.textContent = "Link copied"; setTimeout(() => (share.textContent = "Link"), 2000); }); };
    document.addEventListener("keydown", (e) => {
      const t = e.target, typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT");
      if (e.key === "/" && !typing && $("#search")) { e.preventDefault(); $("#search").focus(); $("#search").select(); }
    });
  }
  function redraw() {
    if (D.comps.length) { safe(renderVolcano, "#volcano"); safe(renderPHist, "#phist"); safe(renderDetail, "#detail"); safe(renderCompare, "#comparebody"); }
    safe(renderHeatmap, "#heatmap"); safe(renderEnrichment, "#enrich"); safe(renderDose, "#dosebody"); safe(renderCys, "#cysbody"); safe(renderTime, "#timebody"); safe(renderQC, "#qc");
  }
  let rt = null;
  window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(redraw, 150); });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", redraw);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", setup);
  else setup();
})();
