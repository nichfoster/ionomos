/* Ionomos report: everything is drawn here from the JSON in #ionomos-data. No libraries, works offline. */
(function () {
  "use strict";
  const D = JSON.parse(document.getElementById("ionomos-data").textContent);
  const SVGNS = "http://www.w3.org/2000/svg";
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
  const nF = D.f.id.length, nS = D.samples.length;
  const tip = $("#tip");
  const ST = {
    ci: 0, lfc: D.settings.log2fc, alpha: D.settings.alpha, adj: D.settings.use_adjusted, labels: D.settings.top_labels,
    search: "", focus: null, pinned: [], mode: "volcano", zoom: null, sigOnly: true, sortKey: "p", sortDir: 1,
    page: 0, highlight: null, highlightName: "",
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
  const fmt = (v, d) => (v == null || isNaN(v) ? "–" : (+v).toFixed(d == null ? 2 : d));
  function fmtP(p) {
    if (p == null || isNaN(p)) return "–";
    if (p === 0) return "0";
    if (p < 1e-3) { const e = Math.floor(Math.log10(p)); return (p / Math.pow(10, e)).toFixed(1) + "e" + e; }
    return p.toFixed(p < 0.01 ? 4 : 3);
  }
  const fmtInt = (n) => (n == null ? "–" : n.toLocaleString());
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
  const condColor = (c) => css("--c" + (condIndex[c] % 8));
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
    a.href = URL.createObjectURL(new Blob([content], { type: type }));
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }
  function svgTools(host, root, name) {
    let t = $(".tools", host);
    if (!t) { t = document.createElement("div"); t.className = "tools"; host.appendChild(t); }
    t.innerHTML = "";
    const b = document.createElement("button");
    b.textContent = "SVG";
    b.title = "Download this chart as SVG (for slides)";
    b.onclick = () => {
      const c = root.cloneNode(true);
      c.setAttribute("xmlns", SVGNS);
      const bg = document.createElementNS(SVGNS, "rect");
      bg.setAttribute("width", "100%"); bg.setAttribute("height", "100%"); bg.setAttribute("fill", css("--surface"));
      c.insertBefore(bg, c.firstChild);
      download(name + ".svg", new XMLSerializer().serializeToString(c), "image/svg+xml");
    };
    t.appendChild(b);
    return t;
  }
  function frame(host, w, h) {
    host.querySelectorAll("svg").forEach((s) => s.remove());
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

  // --------------------------------------------------------- comparisons
  const C = () => D.comps[ST.ci];
  function score(c, i) { return ST.adj ? c.q[i] : c.p[i]; }
  function sigOf(c, i) {
    const s = score(c, i), fc = c.fc[i];
    if (s == null || fc == null || isNaN(s) || isNaN(fc)) return "";
    return s <= ST.alpha && Math.abs(fc) >= ST.lfc ? (fc > 0 ? "up" : "down") : "";
  }
  function pThreshold(c) {
    if (!ST.adj) return ST.alpha;
    let t = null;
    for (let i = 0; i < nF; i++) if (c.q[i] != null && c.q[i] <= ST.alpha && c.p[i] != null) t = t == null ? c.p[i] : Math.max(t, c.p[i]);
    return t;
  }
  function counts(c) {
    let up = 0, down = 0, tested = 0;
    for (let i = 0; i < nF; i++) {
      if (c.p[i] != null) tested++;
      const s = sigOf(c, i);
      if (s === "up") up++; else if (s === "down") down++;
    }
    return { up: up, down: down, tested: tested };
  }
  function matches(i) {
    if (ST.highlight) return ST.highlight.has(i);
    if (!ST.search) return false;
    const q = ST.search.toLowerCase();
    return (D.f.label[i] || "").toLowerCase().includes(q) || (D.f.id[i] || "").toLowerCase().includes(q) ||
      (D.f.desc[i] || "").toLowerCase().includes(q);
  }

  // ------------------------------------------------------------- overview
  function renderTiles() {
    const host = $("#tiles");
    if (!host) return;
    let h = '<div class="tile"><div class="k">' + esc(D.levelTitle) + ' in the analysis</div><div class="v">' + fmtInt(nF) +
      '</div><div class="d">' + esc(D.sourceName) + "</div></div>" +
      '<div class="tile"><div class="k">Samples</div><div class="v">' + nS + '</div><div class="d">' + D.conditions.length +
      " condition(s): " + esc(D.conditions.join(", ")) + "</div></div>";
    D.comps.forEach((c, k) => {
      const n = counts(c);
      h += '<div class="tile" style="cursor:pointer" data-ci="' + k + '"><div class="k">' + esc(c.name) + '</div><div class="v">' +
        fmtInt(n.up + n.down) + '</div><div class="d"><span class="dot up"></span> ' + fmtInt(n.up) + ' up · <span class="dot down"></span> ' +
        fmtInt(n.down) + " down of " + fmtInt(n.tested) + "</div></div>";
    });
    host.innerHTML = h;
    $$(".tile[data-ci]", host).forEach((t) => (t.onclick = () => { ST.ci = +t.dataset.ci; syncControls(); renderDiff(); goTo("differential"); }));
  }

  // -------------------------------------------------------------- volcano
  function renderVolcano() {
    const host = $("#volcano");
    if (!host) return;
    const c = C();
    const W = widthOf(host), H = Math.round(Math.min(520, Math.max(360, W * 0.62)));
    const L = 56, R = 18, T = 26, B = 44;
    const root = frame(host, W, H);
    const ma = ST.mode === "ma";
    const pts = [];
    let xmax = 1, ymax = 1, amin = Infinity, amax = -Infinity;
    for (let i = 0; i < nF; i++) {
      const fc = c.fc[i], p = c.p[i];
      if (fc == null || p == null) continue;
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
    if (ST.zoom && ST.zoom.mode === ST.mode) { x0 = ST.zoom.x0; x1 = ST.zoom.x1; y0 = ST.zoom.y0; y1 = ST.zoom.y1; }
    const X = (v) => L + ((v - x0) / (x1 - x0)) * (W - L - R);
    const Y = (v) => H - B - ((v - y0) / (y1 - y0)) * (H - T - B);
    const g = svg("g", {}, root);
    axes(g, X, Y, niceTicks(x0, x1, 8), niceTicks(y0, y1, 6), L, R, T, B, W, H,
      ma ? "mean log2 abundance" : (D.kind === "ratio" ? "log2 H/L" : "log2 fold change"),
      ma ? "log2 fold change" : (ST.adj ? "−log10 p (line: adjusted p cut-off)" : "−log10 p"));
    const clip = svg("clipPath", { id: "vclip" }, svg("defs", {}, root));
    svg("rect", { x: L, y: T, width: W - L - R, height: H - T - B }, clip);
    const pg = svg("g", { "clip-path": "url(#vclip)" }, root);
    const thrP = pThreshold(c), muted = css("--muted");
    if (!ma) {
      [-ST.lfc, ST.lfc].forEach((v) => { if (ST.lfc > 0) svg("line", { x1: X(v), x2: X(v), y1: T, y2: H - B, stroke: muted, "stroke-dasharray": "4 4" }, pg); });
      if (thrP != null) svg("line", { x1: L, x2: W - R, y1: Y(-Math.log10(thrP)), y2: Y(-Math.log10(thrP)), stroke: muted, "stroke-dasharray": "4 4" }, pg);
    } else {
      [-ST.lfc, 0, ST.lfc].forEach((v) => svg("line", { x1: L, x2: W - R, y1: Y(v), y2: Y(v), stroke: muted, "stroke-dasharray": v ? "4 4" : null }, pg));
    }
    const colUp = css("--up"), colDown = css("--down"), colNs = css("--ns"), surf = css("--surface"), selC = css("--sel");
    const sigs = new Map();
    pts.forEach((p) => sigs.set(p[0], sigOf(c, p[0])));
    const order = pts.slice().sort((a, b) => (sigs.get(a[0]) ? 1 : 0) - (sigs.get(b[0]) ? 1 : 0));
    const screen = [];
    order.forEach(([i, x, y]) => {
      const s = sigs.get(i), cx = X(x), cy = Y(y);
      screen.push([i, cx, cy]);
      if (cx < L - 5 || cx > W - R + 5 || cy < T - 5 || cy > H - B + 5) return;
      svg("circle", { cx: cx.toFixed(1), cy: cy.toFixed(1), r: s ? 3.4 : 2.6, fill: s === "up" ? colUp : s === "down" ? colDown : colNs,
        "fill-opacity": s ? 1 : 0.7, stroke: s ? surf : null, "stroke-width": s ? 1 : null }, pg);
    });
    // search / term highlight, pinned, focus
    const ring = (i, color, r, w) => {
      const p = screen.find((q) => q[0] === i);
      if (p) svg("circle", { cx: p[1], cy: p[2], r: r, fill: "none", stroke: color, "stroke-width": w }, pg);
    };
    if (ST.search || ST.highlight) for (const p of screen) if (matches(p[0])) svg("circle", { cx: p[1], cy: p[2], r: 5.5, fill: "none", stroke: selC, "stroke-width": 1.3 }, pg);
    ST.pinned.forEach((i) => ring(i, selC, 6.5, 2));
    if (ST.focus != null) ring(ST.focus, css("--accent"), 8, 2.5);
    // labels: top hits by p + pinned + focus, greedy non-overlap
    const want = [];
    const ranked = pts.filter((p) => sigs.get(p[0])).sort((a, b) => c.p[a[0]] - c.p[b[0]]).slice(0, ST.labels).map((p) => p[0]);
    ST.pinned.concat(ST.focus != null ? [ST.focus] : []).concat(ranked).forEach((i) => { if (!want.includes(i)) want.push(i); });
    const boxes = [];
    want.forEach((i) => {
      const p = screen.find((q) => q[0] === i);
      if (!p) return;
      const s = (D.f.label[i] || D.f.id[i]).slice(0, 22), w = s.length * 6.6 + 4, h = 14;
      const cands = [[p[1] + 6, p[2] - 6], [p[1] - 6 - w, p[2] - 6], [p[1] + 6, p[2] + 14], [p[1] - 6 - w, p[2] + 14]];
      for (const [bx, by] of cands) {
        const box = [bx, by - 11, bx + w, by - 11 + h];
        if (box[0] < L || box[2] > W - R || box[1] < T || box[3] > H - B) continue;
        if (boxes.some((o) => !(box[2] < o[0] || box[0] > o[2] || box[3] < o[1] || box[1] > o[3]))) continue;
        boxes.push(box);
        text(pg, bx, by, s, { fill: css("--text"), "font-size": 11.5, "font-weight": ST.pinned.includes(i) || i === ST.focus ? 650 : 400 });
        break;
      }
    });
    // corner names (FragPipe-Analyst draws the two conditions)
    if (!ma && c.t2 && D.kind !== "ratio") {
      text(root, W - R - 4, H - B - 8, c.t1 + " ↑", { "text-anchor": "end", fill: colUp, "font-size": 12.5, "font-weight": 600 });
      text(root, L + 6, H - B - 8, "↑ " + c.t2, { fill: colDown, "font-size": 12.5, "font-weight": 600 });
    }
    // interaction: hover nearest, click focus, drag zoom, dblclick reset
    const hit = svg("rect", { x: L, y: T, width: W - L - R, height: H - T - B, fill: "transparent", style: "cursor:crosshair" }, root);
    const band = svg("rect", { fill: css("--accent"), "fill-opacity": 0.08, stroke: css("--accent"), "stroke-dasharray": "3 3", visibility: "hidden" }, root);
    const toLocal = (e) => { const r = root.getBoundingClientRect(); return [(e.clientX - r.left) * (W / r.width), (e.clientY - r.top) * (H / r.height)]; };
    const nearest = (mx, my) => {
      let best = null, bd = 64;
      for (const p of screen) { const d = (p[1] - mx) ** 2 + (p[2] - my) ** 2; if (d < bd) { bd = d; best = p[0]; } }
      return best;
    };
    let drag = null;
    hit.addEventListener("mousedown", (e) => { drag = toLocal(e); });
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
        const inv = (px, lo, hi, a, b) => lo + ((px - a) / (b - a)) * (hi - lo);
        const xa = inv(Math.min(mx, drag[0]), x0, x1, L, W - R), xb = inv(Math.max(mx, drag[0]), x0, x1, L, W - R);
        const ya = y0 + ((H - B - Math.max(my, drag[1])) / (H - T - B)) * (y1 - y0), yb = y0 + ((H - B - Math.min(my, drag[1])) / (H - T - B)) * (y1 - y0);
        ST.zoom = { mode: ST.mode, x0: xa, x1: xb, y0: ya, y1: yb };
        drag = null;
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
    $("#vcount").innerHTML = '<span class="dot up"></span> <b>' + fmtInt(n.up) + '</b> up &nbsp; <span class="dot down"></span> <b>' +
      fmtInt(n.down) + "</b> down &nbsp;<span class='muted'>of " + fmtInt(n.tested) + " tested · drag to zoom, double-click to reset, shift-click to pin</span>";
  }
  function featureTip(i) {
    const c = C();
    return "<b>" + esc(D.f.label[i] || D.f.id[i]) + "</b> <span class='muted'>" + esc(D.f.id[i]) + "</span><br>log2FC " + fmt(c.fc[i]) +
      " · p " + fmtP(c.p[i]) + " · adj. p " + fmtP(c.q[i]) + (D.f.desc[i] ? "<br><span class='muted'>" + esc(D.f.desc[i].slice(0, 90)) + "</span>" : "");
  }

  // --------------------------------------------------------------- detail
  function isImputed(i, j) { return D.imp && D.imp[i] && D.imp[i][j] === "1"; }
  function setFocus(i) { ST.focus = i; renderVolcano(); renderDetail(); renderTable(false); }
  function togglePin(i) {
    const k = ST.pinned.indexOf(i);
    if (k >= 0) ST.pinned.splice(k, 1); else ST.pinned.push(i);
    renderPins();
  }
  function renderPins() {
    const host = $("#pins");
    if (!host) return;
    host.innerHTML = ST.pinned.length ? ST.pinned.map((i) => '<span class="chip' + (i === ST.focus ? " on" : "") + '" data-i="' + i + '">' +
      esc(D.f.label[i] || D.f.id[i]) + " ✕</span>").join("") + ' <button id="pinprof">Profile of pinned</button>' : "";
    $$(".chip", host).forEach((ch) => (ch.onclick = (e) => { const i = +ch.dataset.i; if (e.target === ch && e.offsetX > ch.offsetWidth - 18) { togglePin(i); renderVolcano(); } else setFocus(i); }));
    const pp = $("#pinprof");
    if (pp) pp.onclick = () => renderProfile();
  }
  function renderDetail() {
    const host = $("#detail");
    if (!host) return;
    const i = ST.focus;
    if (i == null) { host.innerHTML = '<div class="empty">Click a point or a table row to see that ' + esc(D.levelWord) + " here.<br>Shift-click to pin several.</div>"; return; }
    let h = "<h4>" + esc(D.f.label[i] || D.f.id[i]) + '</h4><div class="id">' + esc(D.f.id[i]) + '</div><div class="desc">' + esc(D.f.desc[i] || "") + "</div>";
    h += '<div id="strip" class="chart"></div>';
    h += "<table><thead><tr><th>Comparison</th><th>log2FC</th><th>95% CI</th><th>adj. p</th></tr></thead><tbody>";
    D.comps.forEach((c) => {
      const s = sigOf(c, i);
      h += "<tr><td>" + (s ? '<span class="dot ' + s + '"></span> ' : "") + esc(c.name) + '</td><td class="n">' + fmt(c.fc[i]) + '</td><td class="n">' +
        (c.ciL[i] == null ? "–" : fmt(c.ciL[i]) + " – " + fmt(c.ciR[i])) + '</td><td class="n">' + fmtP(c.q[i]) + "</td></tr>";
    });
    h += '</tbody></table><div class="chips"><button id="pinbtn">' + (ST.pinned.includes(i) ? "Unpin" : "Pin") + '</button><button id="copybtn">Copy values</button></div>';
    host.innerHTML = h;
    $("#pinbtn").onclick = () => { togglePin(i); renderDetail(); renderVolcano(); };
    $("#copybtn").onclick = () => {
      const rows = ["sample\tcondition\tlog2\timputed"].concat(D.samples.map((s, j) => s + "\t" + D.cond[j] + "\t" + (D.v[i][j] == null ? "" : D.v[i][j]) + "\t" + (isImputed(i, j) ? "yes" : "")));
      navigator.clipboard && navigator.clipboard.writeText(rows.join("\n"));
    };
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
      const t = text(g, cx, H - B + 16, cnd.length > 14 ? cnd.slice(0, 13) + "…" : cnd, { "text-anchor": "middle" });
      t.appendChild(document.createElementNS(SVGNS, "title")).textContent = cnd;
      feats.forEach((i, fi) => {
        const off = feats.length > 1 ? (fi - (feats.length - 1) / 2) * Math.min(18, bw / (feats.length + 1)) : 0;
        const vals = [];
        D.samples.forEach((s, j) => {
          if (D.cond[j] !== cnd || D.v[i][j] == null) return;
          const v = D.v[i][j], imp = isImputed(i, j);
          vals.push(v);
          const jit = ((j * 7919) % 13 - 6) * (feats.length > 1 ? 0.6 : 1.6);
          const col = feats.length > 1 ? css("--c" + (fi % 8)) : condColor(cnd);
          const dot = svg("circle", { cx: cx + off + jit, cy: Y(v), r: 4.2, fill: imp ? surf : col, stroke: col, "stroke-width": 1.6 }, g);
          dot.appendChild(document.createElementNS(SVGNS, "title")).textContent = s + ": " + fmt(v) + (imp ? " (imputed)" : "");
        });
        if (vals.length) {
          const m = vals.reduce((a, b) => a + b, 0) / vals.length;
          svg("line", { x1: cx + off - 12, x2: cx + off + 12, y1: Y(m), y2: Y(m), stroke: css("--text"), "stroke-width": 2 }, g);
        }
      });
    });
    const hasImp = feats.some((i) => D.samples.some((_, j) => isImputed(i, j)));
    if (hasImp) text(g, L + 4, H - 4, "○ imputed   ● measured", { "font-size": 11 });
    svgTools(host, root, "values_" + feats.map((i) => D.f.label[i] || D.f.id[i]).join("_").slice(0, 60));
  }
  function renderProfile() {
    const host = $("#detail");
    if (!ST.pinned.length) return;
    host.innerHTML = "<h4>Pinned (" + ST.pinned.length + ")</h4><div class='legend'>" + ST.pinned.map((i, k) => "<span><span class='sw' style='background:" +
      css("--c" + (k % 8)) + "'></span>" + esc(D.f.label[i] || D.f.id[i]) + "</span>").join("") + "</div><div id='strip' class='chart'></div>";
    renderStrip($("#strip"), ST.pinned.slice(0, 8));
  }

  // ---------------------------------------------------------------- table
  const COLS = [
    { k: "sig", t: "", f: (c, i) => { const s = sigOf(c, i); return s ? '<span class="dot ' + s + '"></span>' : ""; }, v: (c, i) => (sigOf(c, i) ? 0 : 1) },
    { k: "label", t: D.kind === "ratio" ? "Site" : "Gene", f: (c, i) => esc(D.f.label[i] || ""), v: (c, i) => (D.f.label[i] || "").toLowerCase() },
    { k: "id", t: "ID", f: (c, i) => esc((D.f.id[i] || "").slice(0, 40)), v: (c, i) => D.f.id[i] },
    { k: "fc", t: "log2FC", n: 1, f: (c, i) => fmt(c.fc[i]), v: (c, i) => (c.fc[i] == null ? -1e9 : c.fc[i]) },
    { k: "ci", t: "95% CI", n: 1, f: (c, i) => (c.ciL[i] == null ? "–" : fmt(c.ciL[i]) + " – " + fmt(c.ciR[i])), v: (c, i) => c.ciL[i] },
    { k: "p", t: "p", n: 1, f: (c, i) => fmtP(c.p[i]), v: (c, i) => (c.p[i] == null ? 2 : c.p[i]) },
    { k: "q", t: "adj. p", n: 1, f: (c, i) => fmtP(c.q[i]), v: (c, i) => (c.q[i] == null ? 2 : c.q[i]) },
    { k: "n", t: "measured", n: 1, f: (c, i) => (c.nt[i] == null ? "" : c.nt[i] + (c.nc[i] != null && c.t2 ? " / " + c.nc[i] : "")), v: (c, i) => (c.nt[i] || 0) + (c.nc[i] || 0) },
    { k: "desc", t: "Description", f: (c, i) => esc((D.f.desc[i] || "").slice(0, 120)), v: (c, i) => D.f.desc[i] || "", cls: "desc" },
  ];
  function tableRows() {
    const c = C();
    const out = [];
    for (let i = 0; i < nF; i++) {
      if (c.p[i] == null && c.fc[i] == null) continue;
      if (ST.sigOnly && !sigOf(c, i) && !(ST.search || ST.highlight)) continue;
      if ((ST.search || ST.highlight) && !matches(i)) continue;
      out.push(i);
    }
    const col = COLS.find((x) => x.k === ST.sortKey) || COLS[5];
    out.sort((a, b) => { const x = col.v(c, a), y = col.v(c, b); return (x > y ? 1 : x < y ? -1 : 0) * ST.sortDir; });
    return out;
  }
  function renderTable(resetPage) {
    const host = $("#table");
    if (!host) return;
    if (resetPage) ST.page = 0;
    const c = C(), rows = tableRows(), per = 50;
    const pages = Math.max(1, Math.ceil(rows.length / per));
    ST.page = Math.min(ST.page, pages - 1);
    let h = '<div class="tablewrap"><table><thead><tr>' + COLS.map((x) => "<th data-k='" + x.k + "'" + (x.k === ST.sortKey ? " data-dir='" + (ST.sortDir > 0 ? "asc" : "desc") + "'" : "") + ">" + x.t + "</th>").join("") + "</tr></thead><tbody>";
    rows.slice(ST.page * per, ST.page * per + per).forEach((i) => {
      h += "<tr data-i='" + i + "'" + (i === ST.focus ? " class='focus'" : "") + ">" + COLS.map((x) => "<td" + (x.n ? " class='n'" : x.cls ? " class='" + x.cls + "'" : "") + ">" + x.f(c, i) + "</td>").join("") + "</tr>";
    });
    if (!rows.length) h += "<tr><td colspan='" + COLS.length + "' class='muted'>" + (ST.sigOnly ? "No significant features at these cut-offs. Untick “significant only” to see everything." : "Nothing matches.") + "</td></tr>";
    h += "</tbody></table></div><div class='pager'>" + fmtInt(rows.length) + " rows · page " + (ST.page + 1) + " of " + pages +
      " <button id='pprev'>‹</button><button id='pnext'>›</button></div>";
    host.innerHTML = h;
    $$("th", host).forEach((th) => (th.onclick = () => { if (ST.sortKey === th.dataset.k) ST.sortDir *= -1; else { ST.sortKey = th.dataset.k; ST.sortDir = 1; } renderTable(true); }));
    $$("tbody tr[data-i]", host).forEach((tr) => (tr.onclick = (e) => { const i = +tr.dataset.i; if (e.shiftKey) togglePin(i); setFocus(i); }));
    $("#pprev").onclick = () => { ST.page = Math.max(0, ST.page - 1); renderTable(false); };
    $("#pnext").onclick = () => { ST.page = Math.min(pages - 1, ST.page + 1); renderTable(false); };
  }
  function exportCSV() {
    const c = C(), rows = tableRows();
    const head = ["id", "label", "description", "log2fc", "ci_low", "ci_high", "p", "adj_p", "significant", ...D.samples];
    const q = (s) => '"' + String(s == null ? "" : s).replace(/"/g, '""') + '"';
    const lines = [head.join(",")].concat(rows.map((i) => [q(D.f.id[i]), q(D.f.label[i]), q(D.f.desc[i]), c.fc[i], c.ciL[i], c.ciR[i], c.p[i], c.q[i], sigOf(c, i), ...D.v[i]].map((v) => (v == null ? "" : v)).join(",")));
    download(c.slug + "_filtered.csv", lines.join("\n"), "text/csv");
  }

  // -------------------------------------------------------- p histogram
  function renderPHist() {
    const host = $("#phist");
    if (!host) return;
    const c = C(), bins = new Array(20).fill(0);
    let n = 0;
    for (let i = 0; i < nF; i++) if (c.p[i] != null) { bins[Math.min(19, Math.floor(c.p[i] * 20))]++; n++; }
    const W = widthOf(host, 300), H = 170, L = 44, R = 8, T = 10, B = 34;
    const root = frame(host, W, H), g = svg("g", {}, root);
    const ymax = Math.max(1, ...bins);
    const X = (v) => L + v * (W - L - R), Y = (v) => H - B - (v / ymax) * (H - T - B);
    axes(g, X, Y, [0, 0.25, 0.5, 0.75, 1], niceTicks(0, ymax, 3), L, R, T, B, W, H, "p-value", "");
    const col = css("--c0");
    bins.forEach((b, k) => svg("rect", { x: X(k / 20) + 0.5, y: Y(b), width: (W - L - R) / 20 - 1, height: H - B - Y(b), fill: col, "fill-opacity": 0.75 }, g));
    if (n) svg("line", { x1: L, x2: W - R, y1: Y(n / 20), y2: Y(n / 20), stroke: css("--muted"), "stroke-dasharray": "3 3" }, g);
  }

  // ------------------------------------------------------------- heatmap
  function divColor(v, lim) {
    const t = Math.max(-1, Math.min(1, v / lim));
    const a = t >= 0 ? hex(css("--up")) : hex(css("--down")), s = hex(css("--surface"));
    const k = Math.abs(t);
    return "rgb(" + Math.round(s[0] + (a[0] - s[0]) * k) + "," + Math.round(s[1] + (a[1] - s[1]) * k) + "," + Math.round(s[2] + (a[2] - s[2]) * k) + ")";
  }
  function hex(c) {
    c = c.replace("#", "");
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
    rows.forEach((i, r) => {
      vals[r].forEach((v, k) => {
        ctx.fillStyle = v == null ? css("--sunk") : divColor(v, lim);
        ctx.fillRect(labW + k * cellW, topH + r * cellH, cellW - (cellW > 10 ? 1 : 0), cellH - (cellH > 6 ? 1 : 0));
      });
      if (showNames) {
        ctx.fillStyle = css("--text2");
        ctx.textAlign = "right";
        ctx.fillText((D.f.label[i] || D.f.id[i]).slice(0, 16), labW - 6, topH + r * cellH + cellH - 2);
        ctx.textAlign = "left";
      }
    });
    cv.onmousemove = (e) => {
      const rc = cv.getBoundingClientRect(), x = e.clientX - rc.left, y = e.clientY - rc.top;
      const k = Math.floor((x - labW) / cellW), r = Math.floor((y - topH) / cellH);
      if (k < 0 || k >= cols.length || r < 0 || r >= rows.length) { hideTip(); return; }
      const i = rows[r], j = cols[k];
      showTip(e, "<b>" + esc(D.f.label[i] || D.f.id[i]) + "</b><br>" + esc(D.samples[j]) + " (" + esc(D.cond[j]) + ")<br>centred log2 " + fmt(vals[r][k]) + (isImputed(i, j) ? " · imputed" : ""));
    };
    cv.onmouseleave = hideTip;
    cv.onclick = (e) => {
      const rc = cv.getBoundingClientRect(), r = Math.floor((e.clientY - rc.top - topH) / cellH);
      if (r >= 0 && r < rows.length) { setFocus(rows[r]); goTo("differential"); }
    };
    $("#heatlegend").innerHTML = "<span><span class='sw' style='background:" + css("--down") + "'></span>below the protein's mean</span><span><span class='sw' style='background:" +
      css("--up") + "'></span>above</span><span class='muted'>colour saturates at ±" + fmt(lim, 1) + " log2 · " + rows.length + " of " + fmtInt(hm.total_significant) +
      " significant features, clustered (euclidean, complete linkage) · click a row to open it</span>";
  }

  // ----------------------------------------------------------- enrichment
  function renderEnrichment() {
    const host = $("#enrich");
    if (!host) return;
    if (!D.enr.length) { host.innerHTML = '<div class="empty">' + esc(D.enrNote || "Enrichment was not run.") + "</div>"; return; }
    const comps = [...new Set(D.enr.map((b) => b.comparison))], libs = [...new Set(D.enr.map((b) => b.library))];
    const st = host._st || (host._st = { comp: comps[0], dir: "up", lib: libs[0] });
    let h = "<div class='bar' style='position:static'><label class='ctl'>Comparison <select id='ecomp'>" + comps.map((c) => "<option" + (c === st.comp ? " selected" : "") + ">" + esc(c) + "</option>").join("") + "</select></label>" +
      "<label class='ctl'>Hits <select id='edir'><option value='up'" + (st.dir === "up" ? " selected" : "") + ">up</option><option value='down'" + (st.dir === "down" ? " selected" : "") + ">down</option></select></label>" +
      "<label class='ctl'>Gene sets <select id='elib'>" + libs.map((c) => "<option" + (c === st.lib ? " selected" : "") + ">" + esc(c) + "</option>").join("") + "</select></label></div>";
    const b = D.enr.find((x) => x.comparison === st.comp && x.direction === st.dir && x.library === st.lib);
    h += "<p class='sub'>" + (b ? fmtInt(b.hits) + " " + st.dir + " hits tested against " + fmtInt(b.background) + " quantified genes (one-sided Fisher test, BH-adjusted)." : "") + "</p>";
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
      const r = svg("rect", { x: L, y: y + 3, width: Math.max(1, X(v) - L), height: rowH - 7, fill: col, "fill-opacity": t.q <= 0.05 ? 0.9 : 0.35, rx: 2 }, g);
      r.appendChild(document.createElementNS(SVGNS, "title")).textContent = t.term + "\n" + t.k + " of " + t.K + " genes · adj. p " + fmtP(t.q) + "\n" + t.genes.join(", ");
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
      const t = terms[+tr.dataset.k], genes = new Set(t.genes.map((x) => x.toUpperCase()));
      const set = new Set();
      for (let i = 0; i < nF; i++) { const gname = (D.f.label[i] || "").split(/[;\s]/)[0].replace(/\.\d+$/, "").toUpperCase(); if (genes.has(gname)) set.add(i); }
      ST.highlight = set; ST.highlightName = t.term;
      const k = D.comps.findIndex((c) => c.name === st.comp);
      if (k >= 0) ST.ci = k;
      syncControls(); renderDiff(); goTo("differential");
    }));
  }

  // ------------------------------------------------------------------- QC
  const QC_TABS = [["pca", "PCA"], ["corr", "Correlation"], ["missing", "Missing values"], ["dist", "Distributions"], ["cv", "CV"], ["ids", "Identifications"], ["imp", "Imputation"]];
  let qcTab = "pca";
  function renderQC() {
    const host = $("#qc");
    if (!host) return;
    const tabs = QC_TABS.filter(([k]) => k !== "imp" || D.imp);
    host.innerHTML = "<div class='tabs'>" + tabs.map(([k, t]) => "<button data-k='" + k + "'" + (k === qcTab ? " class='on'" : "") + ">" + t + "</button>").join("") + "</div><div id='qcbody'></div>";
    $$(".tabs button", host).forEach((b) => (b.onclick = () => { qcTab = b.dataset.k; renderQC(); }));
    ({ pca: qcPCA, corr: qcCorr, missing: qcMissing, dist: qcDist, cv: qcCV, ids: qcIds, imp: qcImp })[qcTab]($("#qcbody"));
  }
  function legend() { return "<div class='legend'>" + D.conditions.map((c) => "<span><span class='sw' style='background:" + condColor(c) + "'></span>" + esc(c) + "</span>").join("") + "</div>"; }
  function qcPCA(host) {
    const P = D.qc.pca;
    if (!P || !P.scores.length) { host.innerHTML = "<div class='empty'>Not enough complete features for a PCA.</div>"; return; }
    const npc = P.percent.length, st = host._st || { x: 0, y: 1, names: nS <= 24 };
    host._st = st;
    const opts = (sel) => P.percent.map((p, k) => "<option value='" + k + "'" + (k === sel ? " selected" : "") + ">PC" + (k + 1) + " (" + p.toFixed(1) + "%)</option>").join("");
    host.innerHTML = "<p class='sub'>Top " + fmtInt(P.n) + " most variable features with no missing values (FragPipe-Analyst plot_pca). Replicates should sit together.</p>" +
      "<div class='row'><label class='ctl'>x <select id='pcx'>" + opts(st.x) + "</select></label> <label class='ctl'>y <select id='pcy'>" + opts(st.y) + "</select></label> <label class='ctl'><input type='checkbox' id='pcn'" + (st.names ? " checked" : "") + "> names</label></div>" +
      legend() + "<div class='chart card' id='pcachart'></div>";
    $("#pcx").onchange = (e) => { st.x = +e.target.value; qcPCA(host); };
    $("#pcy").onchange = (e) => { st.y = +e.target.value; qcPCA(host); };
    $("#pcn").onchange = (e) => { st.names = e.target.checked; qcPCA(host); };
    const ch = $("#pcachart"), W = widthOf(ch), H = Math.min(520, Math.round(W * 0.6)), L = 56, R = 20, T = 16, B = 44;
    const xs = P.scores.map((s) => s[st.x]), ys = P.scores.map((s) => s[st.y]);
    const pad = (a) => { const lo = Math.min(...a), hi = Math.max(...a), p = (hi - lo) * 0.12 || 1; return [lo - p, hi + p]; };
    const [x0, x1] = pad(xs), [y0, y1] = pad(ys);
    const X = (v) => L + ((v - x0) / (x1 - x0)) * (W - L - R), Y = (v) => H - B - ((v - y0) / (y1 - y0)) * (H - T - B);
    const root = frame(ch, W, H), g = svg("g", {}, root);
    axes(g, X, Y, niceTicks(x0, x1, 6), niceTicks(y0, y1, 5), L, R, T, B, W, H, "PC" + (st.x + 1) + " (" + P.percent[st.x].toFixed(1) + "%)", "PC" + (st.y + 1) + " (" + P.percent[st.y].toFixed(1) + "%)");
    P.scores.forEach((s, j) => {
      const c = svg("circle", { cx: X(s[st.x]), cy: Y(s[st.y]), r: 7, fill: condColor(D.cond[j]), stroke: css("--surface"), "stroke-width": 1.5 }, g);
      c.addEventListener("mousemove", (e) => showTip(e, "<b>" + esc(D.samples[j]) + "</b><br>" + esc(D.cond[j])));
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
        showTip(e, "<b>" + esc(D.f.label[i] || D.f.id[i]) + "</b><br>" + esc(D.samples[j]) + ": " + (rows[r][j] === "1" ? "measured" : "missing"));
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
  function boxes(host, stats, title) {
    const W = widthOf(host), H = 300, L = 50, R = 10, T = 14, B = 90;
    const root = frame(host, W, H), g = svg("g", {}, root);
    let lo = Infinity, hi = -Infinity;
    stats.forEach((s) => { if (s) { lo = Math.min(lo, s.lo); hi = Math.max(hi, s.hi); } });
    if (!isFinite(lo)) return;
    const pad = (hi - lo) * 0.06 || 1;
    lo -= pad; hi += pad;
    const bw = (W - L - R) / nS, Y = (v) => H - B - ((v - lo) / (hi - lo)) * (H - T - B);
    axes(g, () => 0, Y, [], niceTicks(lo, hi, 5), L, R, T, B, W, H, "", title);
    stats.forEach((s, j) => {
      const cx = L + bw * (j + 0.5), w = Math.min(28, bw * 0.6), col = condColor(D.cond[j]);
      const t = text(g, 0, 0, D.samples[j].slice(0, 16), { "font-size": 10.5, transform: "translate(" + (cx + 3) + "," + (H - B + 10) + ") rotate(60)" });
      t.setAttribute("x", 0);
      if (!s) return;
      svg("line", { x1: cx, x2: cx, y1: Y(s.lo), y2: Y(s.hi), stroke: css("--muted") }, g);
      const r = svg("rect", { x: cx - w / 2, y: Y(s.q3), width: w, height: Math.max(1, Y(s.q1) - Y(s.q3)), fill: col, "fill-opacity": 0.35, stroke: col }, g);
      r.appendChild(document.createElementNS(SVGNS, "title")).textContent = D.samples[j] + "\nmedian " + fmt(s.median) + " · IQR " + fmt(s.q1) + "–" + fmt(s.q3) + " · n " + s.n;
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
    boxes($("#distchart"), st.after || !hasNorm ? D.qc.box_after : D.qc.box_before, D.kind === "ratio" ? "log2 H/L" : "log2 value");
  }
  function qcCV(host) {
    const cv = D.qc.cv || {};
    const conds = Object.keys(cv);
    if (D.kind === "ratio") { host.innerHTML = "<div class='empty'>CVs are for intensities; this data is ratios.</div>"; return; }
    host.innerHTML = "<p class='sub'>Coefficient of variation (sd / mean of un-logged values) per feature within each condition (FragPipe-Analyst plot_cvs). Lower is more reproducible; the dashed line is the median.</p><div class='grid2' id='cvgrid'></div>";
    const grid = $("#cvgrid");
    conds.forEach((c) => {
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
  function qcIds(host) {
    const a = D.qc.features_per_sample, b = D.qc.features_per_sample_after;
    host.innerHTML = "<p class='sub'>" + esc(D.levelTitle) + " measured per sample (FragPipe-Analyst plot_feature_numbers): before filtering (light) and in the analysis (solid).</p>" + legend() + "<div class='chart card' id='idchart'></div>";
    const ch = $("#idchart"), W = widthOf(ch), H = 300, L = 60, R = 10, T = 14, B = 90;
    const root = frame(ch, W, H), g = svg("g", {}, root), ymax = Math.max(1, ...a);
    const bw = (W - L - R) / nS, Y = (v) => H - B - (v / ymax) * (H - T - B);
    axes(g, () => 0, Y, [], niceTicks(0, ymax, 5), L, R, T, B, W, H, "", "features");
    a.forEach((v, j) => {
      const x = L + bw * j + bw * 0.15, w = bw * 0.7, col = condColor(D.cond[j]);
      svg("rect", { x: x, y: Y(v), width: w, height: H - B - Y(v), fill: col, "fill-opacity": 0.3 }, g);
      const r = svg("rect", { x: x, y: Y(b[j]), width: w, height: H - B - Y(b[j]), fill: col }, g);
      r.appendChild(document.createElementNS(SVGNS, "title")).textContent = D.samples[j] + ": " + v + " measured, " + b[j] + " in the analysis";
      const t = text(g, 0, 0, D.samples[j].slice(0, 16), { "font-size": 10.5, transform: "translate(" + (x + w / 2 + 3) + "," + (H - B + 10) + ") rotate(60)" });
      t.setAttribute("x", 0);
    });
    svgTools(ch, root, "identifications");
  }
  function qcImp(host) {
    host.innerHTML = "<p class='sub'>Where the imputed values landed: " + esc(D.imputationLabel) + ". Imputed values should sit at the low end of the measured distribution.</p><div class='chart card' id='impchart'></div>";
    const meas = [], imp = [];
    for (let i = 0; i < nF; i++) for (let j = 0; j < nS; j++) { const v = D.v[i][j]; if (v == null) continue; (isImputed(i, j) ? imp : meas).push(v); }
    const all = meas.concat(imp), lo = Math.min(...all), hi = Math.max(...all), nb = 40;
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

  // ------------------------------------------------------------- controls
  function syncControls() {
    const sel = $("#comp");
    if (sel) sel.value = String(ST.ci);
    if ($("#lfc")) $("#lfc").value = ST.lfc;
    if ($("#alpha")) $("#alpha").value = ST.alpha;
    if ($("#adj")) $("#adj").checked = ST.adj;
    if ($("#hl")) $("#hl").innerHTML = ST.highlight ? "Marked: <b>" + esc(ST.highlightName) + "</b> (" + ST.highlight.size + ") <button id='hlclear'>clear</button>" : "";
    const hc = $("#hlclear");
    if (hc) hc.onclick = () => { ST.highlight = null; syncControls(); renderDiff(); };
  }
  function renderDiff() {
    ST.zoom = null;
    renderVolcano();
    renderTable(true);
    renderPHist();
    renderDetail();
    renderTiles();
    const c = C();
    const note = $("#cutnote");
    if (note) {
      const changed = ST.lfc !== D.settings.log2fc || ST.alpha !== D.settings.alpha || ST.adj !== D.settings.use_adjusted;
      note.innerHTML = changed ? "Cut-offs changed here only — the TSV files, heatmap and enrichment use the saved settings (|log2FC| ≥ " + D.settings.log2fc +
        ", " + (D.settings.use_adjusted ? "adj. p" : "p") + " ≤ " + D.settings.alpha + ")." : "";
    }
    $("#ma").disabled = D.kind === "ratio" || !c.t2;
  }
  function setup() {
    if (!D.comps.length) {
      const d = $("#differential-body");
      if (d) d.innerHTML = "<div class='empty'>No comparisons were made" + (D.notes.length ? " — see the notes above." : ".") + "</div>";
    } else {
      $("#comp").innerHTML = D.comps.map((c, k) => "<option value='" + k + "'>" + esc(c.name) + "</option>").join("");
      $("#comp").onchange = (e) => { ST.ci = +e.target.value; renderDiff(); };
      const num = (id, key) => ($("#" + id).oninput = (e) => { const v = parseFloat(e.target.value); if (!isNaN(v) && v >= 0) { ST[key] = v; renderDiff(); } });
      num("lfc", "lfc");
      num("alpha", "alpha");
      $("#adj").onchange = (e) => { ST.adj = e.target.checked; renderDiff(); };
      $("#labels").oninput = (e) => { ST.labels = Math.max(0, parseInt(e.target.value, 10) || 0); renderVolcano(); };
      $("#search").oninput = (e) => { ST.search = e.target.value.trim(); ST.highlight = null; syncControls(); renderVolcano(); renderTable(true); };
      $("#sigonly").onchange = (e) => { ST.sigOnly = e.target.checked; renderTable(true); };
      $("#csv").onclick = exportCSV;
      $("#volc").onclick = () => { ST.mode = "volcano"; $("#volc").className = "on"; $("#ma").className = ""; renderVolcano(); };
      $("#ma").onclick = () => { ST.mode = "ma"; $("#ma").className = "on"; $("#volc").className = ""; renderVolcano(); };
      $("#reset").onclick = () => { ST.lfc = D.settings.log2fc; ST.alpha = D.settings.alpha; ST.adj = D.settings.use_adjusted; syncControls(); renderDiff(); };
      syncControls();
      renderDiff();
      renderPins();
    }
    renderTiles();
    renderHeatmap();
    renderEnrichment();
    renderQC();
    const theme = $("#theme");
    if (theme) theme.onclick = () => {
      const r = document.documentElement, cur = r.getAttribute("data-theme");
      const dark = cur ? cur === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
      r.setAttribute("data-theme", dark ? "light" : "dark");
      redraw();
    };
  }
  function redraw() { if (D.comps.length) { renderVolcano(); renderPHist(); renderDetail(); } renderHeatmap(); renderEnrichment(); renderQC(); }
  let rt = null;
  window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(redraw, 150); });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", redraw);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", setup);
  else setup();
})();
