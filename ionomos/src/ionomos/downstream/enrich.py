"""
Pathway / GO enrichment of each comparison's hits (FragPipe-Analyst's "Enrichment" tab).

FragPipe-Analyst sends the gene list to the Enrichr web service and then
corrects the odds ratio for the quantified background. Here the same Enrichr
gene-set libraries are downloaded once, cached on the PC, and the test is run
locally against the right background (every gene that could have been a hit
in that comparison): a one-sided hypergeometric (Fisher) test per term,
Benjamini-Hochberg across terms. Gene lists never leave the PC, and after the
first download it works offline.

    libs = load_libraries(["Hallmark", "GO Biological Process"])   # {name: {term: {genes}}}, notes
    rows = ora(hit_genes, background_genes, libs["Hallmark"])
    rows = rank_test(gene_scores, libs["Hallmark"], residuals)     # every quantified gene, no cut-off

ORA only sees the hits, so a pathway whose members all move a little is missed.
rank_test() ranks every quantified gene by its signed statistic and asks whether a
set's members sit higher or lower than the rest (Wilcoxon rank-sum, as limma's
geneSetTest / wilcoxGST). Genes in a pathway are correlated, which makes that test
too optimistic; as in limma's camera, the variance is inflated by 1 + (k - 1) * r,
r = the set's mean inter-gene correlation of the residuals across samples.

A lab-made .gmt file (e.g. from MSigDB) can be used as another library.
"""
from __future__ import annotations

import logging
import math
import os
import re
from pathlib import Path

from ionomos.downstream import stats

log = logging.getLogger("ionomos.enrich")

# the names FragPipe-Analyst uses for each Enrichr library
LIBRARIES = {
    "Hallmark": "MSigDB_Hallmark_2020",
    "GO Biological Process": "GO_Biological_Process_2021",
    "GO Molecular Function": "GO_Molecular_Function_2021",
    "GO Cellular Component": "GO_Cellular_Component_2021",
    "KEGG": "KEGG_2021_Human",
    "Reactome": "Reactome_2022",
    "WikiPathways": "WikiPathway_2023_Human",
}
DEFAULT_LIBRARIES = ["Hallmark", "GO Biological Process", "Reactome"]
URL = "https://maayanlab.cloud/Enrichr/geneSetLibrary?mode=text&libraryName={}"
MIN_TERM_SIZE = 3  # term genes present in the background


def cache_dir() -> Path:
    env = os.environ.get("IONOMOS_GENESETS")
    if env:
        return Path(env)
    from ionomos import names

    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / names.APPDATA_DIR
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / names.SLUG
    return base / "genesets"


def parse_library(text: str) -> dict[str, set[str]]:
    """Enrichr text / GMT: term <tab> description <tab> gene <tab> gene ... ('GENE,1.0' weights allowed)."""
    out: dict[str, set[str]] = {}
    for line in text.splitlines():
        parts = line.rstrip("\r\n").split("\t")
        if len(parts) < 3 or not parts[0].strip():
            continue
        genes = {g.split(",")[0].strip().upper() for g in parts[2:] if g.strip()}
        if genes:
            out.setdefault(parts[0].strip(), set()).update(genes)
    return out


def _download(lib: str, dest: Path, timeout: float) -> None:
    import urllib.request

    from ionomos import __version__

    req = urllib.request.Request(URL.format(lib), headers={"User-Agent": f"Ionomos/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    if not parse_library(data.decode("utf-8", errors="replace")):
        raise ValueError("the download was not a gene-set library")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(".part")
    part.write_bytes(data)
    os.replace(part, dest)


def load_libraries(names_: list[str], gmt: str | None = None, timeout: float = 30) -> tuple[dict, list[str]]:
    """{display name: {term: genes}} for the chosen libraries (cached, downloaded when missing)."""
    libs: dict[str, dict[str, set[str]]] = {}
    notes: list[str] = []
    offline = bool(os.environ.get("IONOMOS_OFFLINE"))
    for name in names_:
        lib = LIBRARIES.get(name)
        if lib is None:
            notes.append(f"enrichment: unknown library {name!r} (known: {', '.join(LIBRARIES)})")
            continue
        path = cache_dir() / f"{lib}.txt"
        if not path.is_file():
            if offline:
                notes.append(f"enrichment: {name} is not downloaded yet and this PC is offline")
                continue
            try:
                _download(lib, path, timeout)
                log.info("downloaded gene sets %s -> %s", lib, path)
            except Exception as exc:  # noqa: BLE001 - no internet must not break the analysis
                notes.append(f"enrichment: could not download {name} ({exc}); it is tried again next time")
                continue
        try:
            libs[name] = parse_library(path.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            notes.append(f"enrichment: could not read {path} ({exc})")
    if gmt:
        p = Path(gmt)
        try:
            libs[p.stem] = parse_library(p.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            notes.append(f"enrichment: could not read the gene-set file {gmt} ({exc})")
    return libs, notes


def _log_choose(n: int, k: int) -> float:
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def hyper_upper(k: int, big_k: int, n: int, big_n: int) -> float:
    """P(X >= k), X ~ hypergeometric: n genes drawn from big_n, of which big_k are in the term."""
    hi = min(big_k, n)
    if k <= 0:
        return 1.0
    if k > hi:
        return 0.0
    denom = _log_choose(big_n, n)
    terms = [_log_choose(big_k, x) + _log_choose(big_n - big_k, n - x) - denom for x in range(k, hi + 1)]
    top = max(terms)
    return min(1.0, math.exp(top) * sum(math.exp(t - top) for t in terms))


def gene_symbol(label: str) -> str:
    """'CA9' / 'CA9;CA9P' / 'CA9 C174' (a site) / 'CA9.1' (made unique) -> 'CA9'."""
    g = re.split(r"[;\s]", (label or "").strip())[0]
    return re.sub(r"\.\d+$", "", g).upper()


def ora(hits: list[str], background: list[str], library: dict[str, set[str]], limit: int = 60) -> list[dict]:
    """Over-representation of hits among the background, per term (sorted by p)."""
    bg = {g for g in background if g}
    hit = {g for g in hits if g in bg}
    n, big_n = len(hit), len(bg)
    if not n or not big_n:
        return []
    rows = []
    for term, genes in library.items():
        in_bg = genes & bg
        big_k = len(in_bg)
        if big_k < MIN_TERM_SIZE:
            continue
        overlap = sorted(hit & in_bg)
        k = len(overlap)
        p = hyper_upper(k, big_k, n, big_n)
        out_ = n - k
        bg_out = big_n - big_k
        lo = math.log2((k * bg_out) / (out_ * big_k)) if k and out_ and big_k else (math.inf if k else -math.inf)
        rows.append({"term": term, "k": k, "K": big_k, "n": n, "N": big_n, "p": p, "log2_odds": lo, "genes": overlap})
    qs = stats.bh_adjust([r["p"] for r in rows])
    for r, q in zip(rows, qs, strict=True):
        r["q"] = q
    rows = [r for r in rows if r["k"] > 0]
    rows.sort(key=lambda r: (r["p"], -r["k"]))
    return rows[:limit]


def _avg_ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def _norm_sf2(z: float) -> float:
    """Two-sided normal tail probability."""
    return math.erfc(abs(z) / math.sqrt(2))


def _unit(v: list[float] | None) -> list[float] | None:
    if not v:
        return None
    mu = sum(v) / len(v)
    d = [x - mu for x in v]
    n = math.sqrt(sum(x * x for x in d))
    return [x / n for x in d] if n > 0 else None


def inter_gene_correlation(vectors: list[list[float] | None]) -> float:
    """Mean pairwise Pearson correlation of residual vectors, in O(k * samples):
    with unit-length centred vectors u, sum_{a != b} u_a . u_b = |sum u|^2 - k."""
    us = [u for u in (_unit(v) for v in vectors) if u is not None]
    k = len(us)
    if k < 2:
        return 0.0
    tot = [sum(col) for col in zip(*us, strict=True)]
    return (sum(x * x for x in tot) - k) / (k * (k - 1))


def rank_test(scores: dict[str, float], library: dict[str, set[str]], residuals: dict[str, list[float]] | None = None,
              min_size: int = 5, max_size: int = 500, limit: int = 60) -> list[dict]:
    """Rank-based gene-set test on every quantified gene (sorted by p). scores: gene -> signed statistic
    (moderated t, or sign(log2FC) * -log10 p). residuals: gene -> residuals across samples, for the
    correlation adjustment (without it the test assumes independent genes and is optimistic).

    Each row: term, n (members measured), z, p, q, direction (up / down), median (members' median score),
    corr (inter-gene correlation used), leading (members driving the shift, strongest first), genes (all
    members measured, strongest first)."""
    genes = [g for g, v in scores.items() if g and v is not None and math.isfinite(v)]
    big_n = len(genes)
    if big_n < 2 * min_size:
        return []
    ranks = dict(zip(genes, _avg_ranks([scores[g] for g in genes]), strict=True))
    expect = (big_n + 1) / 2
    rows = []
    for term, members in library.items():
        inset = [g for g in members if g in ranks]
        k = len(inset)
        if k < min_size or k > max_size or k >= big_n:
            continue
        mean_rank = sum(ranks[g] for g in inset) / k
        var = (big_n - k) * (big_n + 1) / (12 * k)
        corr = 0.0
        if residuals:
            corr = max(0.0, inter_gene_correlation([residuals.get(g) for g in inset]))
            var *= 1 + (k - 1) * corr
        z = (mean_rank - expect) / math.sqrt(var) if var > 0 else 0.0
        up = z > 0
        ordered = sorted(inset, key=lambda g: -scores[g] if up else scores[g])
        rows.append({"term": term, "n": k, "N": big_n, "z": z, "p": _norm_sf2(z), "direction": "up" if up else "down",
                     "median": stats.median([scores[g] for g in inset]), "corr": corr,
                     "leading": [g for g in ordered[:12] if (scores[g] > 0) == up], "genes": ordered})
    qs = stats.bh_adjust([r["p"] for r in rows])
    for r, q in zip(rows, qs, strict=True):
        r["q"] = q
    rows.sort(key=lambda r: (r["p"], -r["n"]))
    return rows[:limit]
