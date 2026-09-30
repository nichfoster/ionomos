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
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
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
    a.download = name.replace(/[^\w.+-]+/g, "_");
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
  function svgString(root) {
    const c = root.cloneNode(true);
    c.setAttribute("xmlns", SVGNS);
    c.removeAttribute("style");
    const bg = document.createElementNS(SVGNS, "rect");
    bg.setAttribute("width", "100%"); bg.setAttribute("height", "100%"); bg.setAttribute("fill", css("--surface"));
    c.insertBefore(bg, c.firstChild);
    // inline the font so the file looks the same outside the page
    c.setAttribute("font-family", "system-ui, -apple-system, 'Segoe UI', sans-serif");
    return new XMLSerializer().serializeToString(c);
  }
  function svgToPng(root, name) {
    try {
      const w = +root.getAttribute("width"), h = +root.getAttribute("height"), scale = 3;
      const img = new Image();
      img.onload = () => {
        const cv = document.createElement("canvas");
        cv.width = w * scale; cv.height = h * scale;
        const ctx = cv.getContext("2d");
        ctx.scale(scale, scale);
        ctx.drawImage(img, 0, 0, w, h);
        if (cv.toBlob) cv.toBlob((b) => b && download(name + ".png", b));
      };
      img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svgString(root));
    } catch (e) { /* PNG export is a convenience; SVG always works */ }
  }
  function svgTools(host, root, name) {
    let t = $(".tools", host);
    if (!t) { t = document.createElement("div"); t.className = "tools"; host.appendChild(t); }
    t.innerHTML = "";
    const b = document.createElement("button");
    b.textContent = "SVG";
    b.title = "Download this chart as SVG (for slides, editable in Illustrator / Inkscape)";
    b.onclick = () => download(name + ".svg", svgString(root), "image/svg+xml");
    t.appendChild(b);
    const png = document.createElement("button");
    png.textContent = "PNG";
    png.title = "Download this chart as a high-resolution PNG";
    png.onclick = () => svgToPng(root, name);
    t.appendChild(png);
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
  function widthOf(host, fallback) { return Math.max(320, Math.floor(host.clientWidth || fallback || 760)); }
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
  }

  // -------------------------------------------------------------- volcano
  function renderVolcano() {
    const host = $("#volcano");
    if (!host) return;
    const c = C();
    const W = widthOf(host), H = Math.round(Math.min(560, Math.max(360, W * 0.62)));
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
    renderVolcano(); renderTable(true); renderCompare(); safe(renderHeatmap, "#heatmap");
    writeHash();
  }
  function setHighlight(set, name) {
    ST.highlight = set && set.size ? set : null;
    ST.highlightName = name || "";
    syncControls();
    renderSearchInfo();
    renderVolcano(); renderTable(true); renderCompare(); safe(renderHeatmap, "#heatmap");
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
    const W = widthOf(host, 300), H = 230, L = 44, R = 10, T = 26, B = 50;
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
    svgTools(host, root, "values_" + feats.map(nameOf).join("_").slice(0, 60));
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
  ].concat(D.f.pep ? [{ k: "pep", t: D.evidence || "peptides", n: 1, f: (c, i) => (D.f.pep[i] == null ? "" : String(D.f.pep[i])), v: (c, i) => (D.f.pep[i] == null ? -1 : D.f.pep[i]) }] : []).concat([
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
    const c = C(), rows = tableRows();
    const head = ["id", "label", "description", "log2fc", "ci_low", "ci_high", "p", "adj_p", "significant", "imputation_driven"].concat(D.f.pep ? [D.evidence || "peptides"] : [], D.samples);
    const q = (s) => '"' + String(s == null ? "" : s).replace(/"/g, '""') + '"';
    const imp = impDriven(c);
    const lines = [head.map(q).join(",")].concat(rows.map((i) => [q(D.f.id[i]), q(D.f.label[i]), q(D.f.desc[i]), c.fc[i], c.ciL[i], c.ciR[i], c.p[i], c.q[i], sigOf(c, i), imp[i] ? "yes" : ""]
      .concat(D.f.pep ? [D.f.pep[i]] : [], D.v[i]).map((v) => (v == null ? "" : v)).join(",")));
    download(c.slug + "_filtered.csv", lines.join("\n"), "text/csv");
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
    const W = widthOf(host, 300), H = 180, L = 44, R = 8, T = 10, B = 34;
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
    const W = widthOf(host, 480), H = Math.min(480, Math.max(340, W * 0.85)), L = 52, R = 14, T = 34, B = 44;
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
    const W = widthOf(host), H = 176, L = 20, R = 20, T = 70, B = 30;
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
    ["mv", "Mean–variance"], ["rank", "Abundance rank"], ["ids", "Identifications"], ["imp", "Imputation"], ["power", "Power"]];
  let qcTab = null;
  function renderQC() {
    const host = $("#qc");
    if (!host) return;
    const tabs = QC_TABS.filter(([k]) => (k !== "imp" || D.imp) && (k !== "card" || (D.qc.scorecard && D.qc.scorecard.length)) && (k !== "mnar" || D.qc.mnar) && (k !== "power" || D.qc.power) && (k !== "mv" || D.kind !== "ratio" || nS > 2));
    if (!qcTab || !tabs.some(([k]) => k === qcTab)) qcTab = tabs.some(([k]) => k === "card") ? "card" : "pca";
    host.innerHTML = "<div class='tabs'>" + tabs.map(([k, t]) => "<button data-k='" + k + "'" + (k === qcTab ? " class='on'" : "") + ">" + t + "</button>").join("") + "</div><div id='qcbody'></div>";
    $$(".tabs button", host).forEach((b) => (b.onclick = () => { qcTab = b.dataset.k; renderQC(); }));
    const body = $("#qcbody");
    safe(() => ({ card: qcCard, pca: qcPCA, corr: qcCorr, missing: qcMissing, mnar: qcMNAR, dist: qcDist, cv: qcCV, mv: qcMV, rank: qcRank, ids: qcIds, imp: qcImp, power: qcPower })[qcTab](body), body);
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
    const P = D.qc.pca;
    if (!P || !P.scores.length) { host.innerHTML = "<div class='empty'>Not enough complete features for a PCA.</div>"; return; }
    const hasRep = D.rep && D.rep.some((r) => r != null);
    const st = host._st || (qcPCA._st = qcPCA._st || { x: 0, y: 1, names: nS <= 24, by: "condition" });
    host._st = st;
    const opts = (sel) => P.percent.map((p, k) => "<option value='" + k + "'" + (k === sel ? " selected" : "") + ">PC" + (k + 1) + " (" + p.toFixed(1) + "%)</option>").join("");
    const reps = hasRep ? [...new Set(D.rep.filter((r) => r != null))].sort((a, b) => a - b) : [];
    const colorOf = (j) => (st.by === "replicate" && hasRep ? css("--c" + (reps.indexOf(D.rep[j]) % 8)) : condColor(D.cond[j]));
    let assoc = "";
    const pcs = D.qc.pcs;
    if (pcs && pcs.pcs && pcs.pcs.length) {
      assoc = "<table class='mini'><thead><tr><th></th>" + pcs.pcs.map((p) => "<th>PC" + p.pc + " (" + fmt(p.percent, 0) + "%)</th>").join("") + "</tr></thead><tbody><tr><td>explained by condition</td>" +
        pcs.pcs.map((p) => "<td class='n'>" + (p.r2_condition == null ? "–" : pct(p.r2_condition)) + "</td>").join("") + "</tr>" +
        (pcs.pcs.some((p) => p.r2_replicate != null) ? "<tr><td>explained by replicate number</td>" + pcs.pcs.map((p) => "<td class='n" + (p.r2_replicate != null && p.r2_replicate >= 0.5 && p.r2_replicate > (p.r2_condition || 0) ? " zbad" : "") + "'>" + (p.r2_replicate == null ? "–" : pct(p.r2_replicate)) + "</td>").join("") + "</tr>" : "") +
        "</tbody></table><p class='muted'>One-way ANOVA R² of each component's scores. Condition should explain the top components; replicate number explaining one hints at a batch (samples of the same replicate number prepared or run together).</p>";
    }
    host.innerHTML = "<p class='sub'>Top " + fmtInt(P.n) + " most variable features with no missing values (FragPipe-Analyst plot_pca). Replicates should sit together.</p>" +
      "<div class='row'><label class='ctl'>x <select id='pcx'>" + opts(st.x) + "</select></label> <label class='ctl'>y <select id='pcy'>" + opts(st.y) + "</select></label> <label class='ctl'><input type='checkbox' id='pcn'" + (st.names ? " checked" : "") + "> names</label>" +
      (hasRep ? " <label class='ctl'>colour by <select id='pcby'><option value='condition'" + (st.by === "condition" ? " selected" : "") + ">condition</option><option value='replicate'" + (st.by === "replicate" ? " selected" : "") + ">replicate number</option></select></label>" : "") + "</div>" +
      (st.by === "replicate" && hasRep ? "<div class='legend'>" + reps.map((r, k) => "<span><span class='sw' style='background:" + css("--c" + (k % 8)) + "'></span>replicate " + r + "</span>").join("") + "</div>" : legend()) +
      "<div class='chart card' id='pcachart'></div>" + assoc;
    $("#pcx").onchange = (e) => { st.x = +e.target.value; qcPCA(host); };
    $("#pcy").onchange = (e) => { st.y = +e.target.value; qcPCA(host); };
    $("#pcn").onchange = (e) => { st.names = e.target.checked; qcPCA(host); };
    const by = $("#pcby");
    if (by) by.onchange = (e) => { st.by = e.target.value; qcPCA(host); };
    const ch = $("#pcachart"), W = widthOf(ch), H = Math.min(520, Math.round(W * 0.6)), L = 56, R = 20, T = 16, B = 44;
    const xs = P.scores.map((s) => s[st.x]), ys = P.scores.map((s) => s[st.y]);
    const pad = (a) => { const lo = Math.min(...a), hi = Math.max(...a), p = (hi - lo) * 0.12 || 1; return [lo - p, hi + p]; };
    const [x0, x1] = pad(xs), [y0, y1] = pad(ys);
    const X = (v) => L + ((v - x0) / (x1 - x0)) * (W - L - R), Y = (v) => H - B - ((v - y0) / (y1 - y0)) * (H - T - B);
    const root = frame(ch, W, H), g = svg("g", {}, root);
    axes(g, X, Y, niceTicks(x0, x1, 6), niceTicks(y0, y1, 5), L, R, T, B, W, H, "PC" + (st.x + 1) + " (" + P.percent[st.x].toFixed(1) + "%)", "PC" + (st.y + 1) + " (" + P.percent[st.y].toFixed(1) + "%)");
    const flagged = new Set((D.qc.scorecard || []).filter((r) => r.status !== "ok").map((r) => r.sample));
    P.scores.forEach((s, j) => {
      const c = svg("circle", { cx: X(s[st.x]), cy: Y(s[st.y]), r: 7, fill: colorOf(j), stroke: flagged.has(D.samples[j]) ? css("--text") : css("--surface"), "stroke-width": flagged.has(D.samples[j]) ? 2.5 : 1.5 }, g);
      c.addEventListener("mousemove", (e) => showTip(e, "<b>" + esc(D.samples[j]) + "</b><br>" + esc(D.cond[j]) + (D.rep && D.rep[j] != null ? " · replicate " + D.rep[j] : "") + (flagged.has(D.samples[j]) ? "<br>flagged in the scorecard" : "")));
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
    const W = widthOf(ch), lab = 130, cell = Math.max(10, Math.min(36, Math.floor((W - lab - 20) / n))), H = lab + n * cell + 10;
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
    const cc = $("#cumchart"), W = widthOf(cc, 360), H = 300, L = 56, R = 14, T = 14, B = 44;
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
    const ch = $("#mnarchart"), W = widthOf(ch, 600), H = 280, L = 56, R = 14, T = 14, B = 44;
    const bins = M.bins, lo = Math.min(...bins.map((b) => b.mean)), hi = Math.max(...bins.map((b) => b.mean));
    const X = (v) => L + ((v - lo) / (hi - lo || 1)) * (W - L - R), Y = (v) => H - B - v * (H - T - B);
    const root = frame(ch, W, H), g = svg("g", {}, root);
    axes(g, X, Y, niceTicks(lo, hi, 6), [0, 0.25, 0.5, 0.75, 1], L, R, T, B, W, H, "mean measured log2 value", "share of samples measured");
    svg("polyline", { points: bins.map((b) => X(b.mean) + "," + Y(b.detected)).join(" "), fill: "none", stroke: css("--c0"), "stroke-width": 2.2 }, g);
    bins.forEach((b) => title(svg("circle", { cx: X(b.mean), cy: Y(b.detected), r: 4.5, fill: css("--c0") }, g), fmtInt(b.n) + " features around log2 " + fmt(b.mean) + ": measured in " + pct(b.detected) + " of samples"));
    svgTools(ch, root, "missingness_vs_intensity");
  }
  function boxes(host, stats, ttl) {
    const W = widthOf(host), H = 300, L = 50, R = 10, T = 14, B = 90;
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
      const bins = cv[c].hist, W = widthOf(d, 300), H = 200, L = 44, R = 8, T = 30, B = 34, ymax = Math.max(1, ...bins);
      const root = frame(d, W, H), g = svg("g", {}, root);
      const X = (v) => L + v * (W - L - R), Y = (v) => H - B - (v / ymax) * (H - T - B);
      axes(g, X, Y, [0, 0.25, 0.5, 0.75, 1], niceTicks(0, ymax, 3), L, R, T, B, W, H, "CV", "");
      bins.forEach((b, k) => svg("rect", { x: X(k / bins.length) + 0.5, y: Y(b), width: (W - L - R) / bins.length - 1, height: H - B - Y(b), fill: condColor(c), "fill-opacity": 0.75 }, g));
      if (cv[c].median != null) svg("line", { x1: X(Math.min(1, cv[c].median)), x2: X(Math.min(1, cv[c].median)), y1: T, y2: H - B, stroke: css("--text"), "stroke-dasharray": "4 3" }, g);
      text(g, 4, 16, c + " · median " + (cv[c].median == null ? "–" : (cv[c].median * 100).toFixed(1) + "%"), { fill: css("--text"), "font-size": 12.5, "font-weight": 600 });
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
    const ch = $("#mvchart"), W = widthOf(ch), H = 320, L = 56, R = 14, T = 14, B = 44;
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
    const ch = $("#rankchart"), W = widthOf(ch), H = 320, L = 56, R = 14, T = 14, B = 44;
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
    const ch = $("#idchart"), W = widthOf(ch), H = 300, L = 60, R = 10, T = 14, B = 90;
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
    const ch = $("#impchart"), W = widthOf(ch), H = 280, L = 56, R = 10, T = 14, B = 44;
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
    const ch = $("#powchart"), W = widthOf(ch, 640), H = 300, L = 56, R = 16, T = 16, B = 44;
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
      box.oninput = () => { ST.search = box.value.trim(); ST.q = parseQuery(ST.search); ST.highlight = null; syncControls(); renderSearchInfo(); renderVolcano(); renderTable(true); suggest(); writeHash(); };
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
      opt("ptsize", "pt", (el) => parseFloat(el.value) || 1, renderVolcano);
      opt("labsize", "lab", (el) => parseFloat(el.value) || 11.5, renderVolcano);
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
    safe(renderQC, "#qc");
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
    safe(renderHeatmap, "#heatmap"); safe(renderEnrichment, "#enrich"); safe(renderQC, "#qc");
  }
  let rt = null;
  window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(redraw, 150); });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", redraw);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", setup);
  else setup();
})();
