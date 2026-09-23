"""
results/report.html — one self-contained page per experiment: no internet, no
external files needed to view it, light and dark, printable.

Sections: summary tiles · notes · one block per comparison (volcano with hover
tooltips, filterable/sortable top-hits table, links to the full TSV and SVG) ·
quality control (quantified per sample, distributions, correlation, a QC table)
· methods paragraph (ready to paste into a notebook) · files.
"""
from __future__ import annotations

import math
from datetime import datetime
from html import escape

from ionomos.downstream import charts
from ionomos.downstream.analysis import DiffResult
from ionomos.downstream.quant import QuantMatrix

PAGE_CSS = """
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--text:#0b0b0b;--text2:#52514e;--muted:#898781;
--line:#e1e0d9;--ring:rgba(11,11,11,.10);--accent:#2a78d6;--warnbg:#fff6e0;--warnink:#6b4a00;--up:#e34948;--down:#2a78d6}
@media (prefers-color-scheme: dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--page:#0d0d0d;
--surface:#1a1a19;--text:#fff;--text2:#c3c2b7;--muted:#898781;--line:#2c2c2a;--ring:rgba(255,255,255,.10);
--accent:#3987e5;--warnbg:#2a2210;--warnink:#f2d58a;--up:#e66767;--down:#3987e5}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--text:#fff;--text2:#c3c2b7;
--muted:#898781;--line:#2c2c2a;--ring:rgba(255,255,255,.10);--accent:#3987e5;--warnbg:#2a2210;--warnink:#f2d58a;
--up:#e66767;--down:#3987e5}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--text);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1080px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:24px;margin:0 0 4px;font-weight:650;letter-spacing:-.01em}
h2{font-size:18px;margin:36px 0 4px;font-weight:620}
h3{font-size:14px;margin:22px 0 6px;font-weight:600;color:var(--text2)}
.sub{color:var(--text2);margin:0 0 14px}
.meta{color:var(--muted);font-size:13px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:20px 0}
.tile{background:var(--surface);border:1px solid var(--ring);border-radius:10px;padding:12px 14px}
.tile .k{color:var(--text2);font-size:12px}.tile .v{font-size:26px;font-weight:620}
.tile .d{font-size:12px;color:var(--muted)}
.card{background:var(--surface);border:1px solid var(--ring);border-radius:12px;padding:16px;margin:12px 0}
.notes{background:var(--warnbg);color:var(--warnink);border-radius:10px;padding:10px 14px;margin:14px 0}
.notes li{margin:2px 0}
.chart{overflow-x:auto}
.chart svg{display:block;height:auto}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:8px 0}
input[type=search]{font:inherit;padding:6px 10px;border-radius:8px;border:1px solid var(--line);background:var(--page);
color:var(--text);min-width:220px}
a{color:var(--accent)}
.tablewrap{overflow-x:auto;max-height:520px;overflow-y:auto;border:1px solid var(--line);border-radius:8px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:5px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{position:sticky;top:0;background:var(--surface);color:var(--text2);font-weight:600;cursor:pointer;user-select:none}
td.n{text-align:right;font-variant-numeric:tabular-nums}
td.desc{white-space:normal;min-width:220px;color:var(--text2)}
.pill{display:inline-flex;align-items:center;gap:5px}.dot{width:8px;height:8px;border-radius:50%;display:inline-block}
.dot.up{background:var(--up)}.dot.down{background:var(--down)}
#tip{position:fixed;pointer-events:none;background:var(--surface);color:var(--text);border:1px solid var(--ring);
border-radius:8px;padding:6px 10px;font-size:12px;box-shadow:0 4px 16px rgba(0,0,0,.15);display:none;z-index:9;
font-variant-numeric:tabular-nums}
.methods{color:var(--text2);max-width:78ch}
.files li{margin:3px 0}
@media print{body{background:#fff}.tablewrap{max-height:none}#tip,input{display:none}}
"""

PAGE_JS = """
(function(){
 const tip=document.getElementById('tip');
 document.addEventListener('mousemove',e=>{
  const t=e.target.closest('[data-l]');
  if(!t){tip.style.display='none';return}
  let h='<b>'+t.dataset.l+'</b>';
  if(t.dataset.fc!==undefined){h+='<br>log2FC '+t.dataset.fc+' · p '+t.dataset.p+(t.dataset.q?' · q '+t.dataset.q:'')}
  else if(t.dataset.v){h+='<br>'+t.dataset.v}
  tip.innerHTML=h;tip.style.display='block';
  const x=Math.min(e.clientX+14,window.innerWidth-tip.offsetWidth-8);
  tip.style.left=x+'px';tip.style.top=(e.clientY+14)+'px';
 });
 document.querySelectorAll('input[data-filter]').forEach(inp=>{
  inp.addEventListener('input',()=>{
   const q=inp.value.toLowerCase();
   document.querySelectorAll('#'+inp.dataset.filter+' tbody tr').forEach(tr=>{
    tr.style.display=tr.textContent.toLowerCase().includes(q)?'':'none'});
  });
 });
 document.querySelectorAll('table.sortable th').forEach((th,i)=>{
  th.addEventListener('click',()=>{
   const tb=th.closest('table').tBodies[0];const rows=[...tb.rows];
   const asc=th.dataset.dir!=='asc';th.dataset.dir=asc?'asc':'desc';
   const key=r=>{const v=r.cells[i].dataset.v??r.cells[i].textContent;const n=parseFloat(v);return isNaN(n)?v:n};
   rows.sort((a,b)=>{const x=key(a),y=key(b);return (x>y?1:x<y?-1:0)*(asc?1:-1)});
   rows.forEach(r=>tb.appendChild(r));
  });
 });
})();
"""


def _num(v, digits: int = 3) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    if isinstance(v, float):
        return f"{v:.{digits}g}" if (abs(v) < 1e-3 and v != 0) else f"{v:.{digits}f}".rstrip("0").rstrip(".") or "0"
    return str(v)


def _hits_table(d: DiffResult, tid: str, limit: int = 200) -> str:
    rows = [r for r in d.rows if r["significant"]] or [r for r in d.rows if r["pvalue"] is not None][:25]
    rows = rows[:limit]
    head = ["", "Name", "log2FC", "p", "q", "n", "Description"]
    out = [f'<div class="tablewrap"><table class="sortable" id="{tid}"><thead><tr>'
           + "".join(f"<th>{h}</th>" for h in head) + "</tr></thead><tbody>"]
    for r in rows:
        dot = f'<span class="dot {r["significant"]}"></span>' if r["significant"] else ""
        n = f'{r["n_treatment"]}' + (f' v {r["n_control"]}' if r["n_control"] is not None else "")
        out.append(
            f'<tr><td>{dot}</td><td>{escape(r["label"])}</td>'
            f'<td class="n" data-v="{r["log2fc"]}">{_num(r["log2fc"], 2)}</td>'
            f'<td class="n" data-v="{r["pvalue"] if r["pvalue"] is not None else ""}">{_num(r["pvalue"])}</td>'
            f'<td class="n" data-v="{r["qvalue"] if r["qvalue"] is not None else ""}">{_num(r["qvalue"])}</td>'
            f'<td class="n">{n}</td><td class="desc">{escape(r["description"][:140])}</td></tr>')
    out.append("</tbody></table></div>")
    return "".join(out)


def _qc_table(m: QuantMatrix) -> str:
    out = ['<div class="tablewrap"><table class="sortable"><thead><tr><th>Sample</th><th>Condition</th>'
           f'<th>{m.level.capitalize()}s quantified</th><th>Missing</th><th>Median log2</th></tr></thead><tbody>']
    n = len(m.features) or 1
    for s in m.samples:
        col = [v for v in m.column(s) if v is not None]
        med = sorted(col)[len(col) // 2] if col else None
        out.append(f'<tr><td>{escape(s)}</td><td>{escape(m.condition[s])}</td><td class="n">{len(col):,}</td>'
                   f'<td class="n">{100 * (1 - len(col) / n):.1f}%</td><td class="n">{_num(med, 3)}</td></tr>')
    out.append("</tbody></table></div>")
    return "".join(out)


def methods_text(m: QuantMatrix, diffs: list[DiffResult], method: str, fragpipe_note: str) -> str:
    s = diffs[0].settings if diffs else None
    parts = [f"Raw files were searched with FragPipe{(' (' + fragpipe_note + ')') if fragpipe_note else ''} using the "
             f"lab's pinned {escape(method)} workflow, run automatically by Ionomos."]
    if m.kind == "ratio":
        parts.append(f"Labelled peptides were merged to {m.level}s (mean log2 heavy/light ratio per replicate, "
                     "as in the lab's isoDTB site script). For each site, replicate ratios were tested against 0 "
                     "with a two-sided one-sample t-test.")
    else:
        norm = "median-centred per sample, " if (s and s.normalize == "median") else ""
        parts.append(f"{m.level.capitalize()} quantities were log2-transformed ({norm}missing values not imputed) "
                     "and conditions were compared with a two-sided Welch's t-test.")
    if s:
        parts.append(f"P-values were adjusted with the Benjamini–Hochberg procedure. Features were tested when at "
                     f"least {s.min_valid} values were present per group, and called significant at {escape(s.describe())}.")
    return " ".join(parts)


def render(ctx: dict, m: QuantMatrix | None, diffs: list[DiffResult], notes: list[str], files: list[str]) -> str:
    title = ctx.get("experiment") or "Experiment"
    meta = " · ".join(x for x in (ctx.get("user"), ctx.get("method"), ctx.get("date"),
                                  f"generated {datetime.now():%Y-%m-%d %H:%M}", f"Ionomos {ctx.get('version', '')}") if x)
    b = [f"<main><h1>{escape(title)}</h1><div class='meta'>{escape(meta)}</div>"]
    if m is not None:
        tiles = [(f"{m.level.capitalize()}s quantified", f"{len(m.features):,}", f"from {escape(m.source.split('/')[-1].split(chr(92))[-1])}"),
                 ("Samples", f"{len(m.samples)}", f"{len(m.conditions)} condition(s): " + escape(", ".join(m.conditions)))]
        for d in diffs:
            tiles.append((escape(d.name), f"{d.up + d.down:,}", f"<span class='pill'><span class='dot up'></span>{d.up:,} up</span> · "
                          f"<span class='pill'><span class='dot down'></span>{d.down:,} down</span> of {d.tested:,} tested"))
        b.append("<div class='tiles'>" + "".join(
            f"<div class='tile'><div class='k'>{k}</div><div class='v'>{v}</div><div class='d'>{dd}</div></div>"
            for k, v, dd in tiles) + "</div>")
    if notes:
        b.append("<div class='notes'><b>Notes</b><ul>" + "".join(f"<li>{escape(n)}</li>" for n in notes) + "</ul></div>")
    for i, d in enumerate(diffs):
        b.append(f"<h2>{escape(d.name)}</h2><p class='sub'>Significant: {escape(d.settings.describe())}. "
                 f"Hover a point for details.</p>")
        b.append(f"<div class='card chart'>{charts.volcano(d)}</div>")
        n_sig = d.up + d.down
        cap = f"{n_sig:,} significant" if n_sig else "no significant hits — the 25 lowest p-values"
        b.append(f"<h3>Hits ({cap})</h3><div class='row'><input type='search' placeholder='Filter by name or description' "
                 f"data-filter='hits{i}'> <a href='{escape(d.slug())}_differential.tsv'>full table (TSV)</a> · "
                 f"<a href='volcano_{escape(d.slug())}.svg'>volcano (SVG)</a></div>")
        b.append(_hits_table(d, f"hits{i}"))
    if m is not None:
        b.append("<h2>Quality control</h2><p class='sub'>Per-sample depth, value distributions and how similar the "
                 "samples are. Replicates of one condition should look alike.</p>")
        b.append(f"<h3>{m.level.capitalize()}s quantified per sample</h3><div class='card chart'>{charts.id_bars(m)}</div>")
        bp = charts.box_plots(m)
        if bp:
            b.append(f"<h3>Value distribution</h3><div class='card chart'>{bp}</div>")
        hm = charts.correlation_heatmap(m)
        if hm:
            b.append(f"<h3>Sample correlation (Pearson r)</h3><div class='card chart'>{hm}</div>")
        b.append(_qc_table(m))
        b.append(f"<h2>Methods</h2><p class='methods'>{methods_text(m, diffs, ctx.get('method', ''), ctx.get('fragpipe', ''))}</p>")
    if files:
        b.append("<h2>Files</h2><ul class='files'>" + "".join(
            f"<li><a href='{escape(f)}'>{escape(f)}</a></li>" for f in files) + "</ul>")
    b.append("</main><div id='tip'></div>")
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
            f"content='width=device-width,initial-scale=1'><title>{escape(title)} — Ionomos report</title>"
            f"<style>{PAGE_CSS}{charts.STYLE}</style></head><body>{''.join(b)}<script>{PAGE_JS}</script></body></html>")
