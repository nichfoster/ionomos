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
