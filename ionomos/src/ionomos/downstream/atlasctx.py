"""
The lab's cysteine atlas beside the liganded sites (D124): its tier for each site, how many of its experiments saw
the site, and which sites are new to it.

    lk, note = read_lookup(folder)        # folder: <atlas.handoff_dir>/_atlas (atlas.lookup_dir). (None, why) when
                                          # it isn't there, or when it is malformed (the note then starts "malformed")
    lk.get("P04406", 152)                 # Site(tier="high", experiments=12, spectra=340, qc_suspect=False) or None
    lk.describe()                         # "12,345 sites, built 2026-10-19, rule …, quotable: no"
    site_context(lk, "sp|P04406|G3P_HUMAN|C152")   # {"tier", "experiments", "spectra", "qc_suspect", "new"} or None
    with_atlas(cys_payload, lk, feature_ids)        # the report's D.cys with D.cys.atlas (report.js renderCys)
    add_tsv_columns(lk, columns, rows)              # cysteine_sites.tsv's rows (cys.table_rows) with the atlas columns

The atlas is built elsewhere (a DuckDB) from the searches Ionomos hands it (atlas.py). It publishes this lookup back,
contract "ionomos-atlas-lookup/1":

    _atlas/atlas_sites_lookup.tsv    accession, site (1-based position of the C in the canonical UniProt sequence),
                                     tier (high / medium / low), experiments, spectra, qc_suspect (0 / 1); human
                                     only, sorted by accession then site
    _atlas/atlas_sites_lookup.json   contract, atlas_sha256, built, rule, sources, set, rows, quotable

Ionomos only reads it, and only when it is there; a malformed one is left out with a note. A site is matched on its
canonical UniProt accession and its position: the 1-based position in the protein that the lab's R script
(isodtb.py) computes from FragPipe's Start, the atlas' own numbering. A site gets no atlas context (blank cells, and
never "new") when its protein is an isoform (P12345-2), not a UniProt accession (a contaminant, an Ensembl id), or
not human. The lookup is human only, so a site missing from it is called new only when its protein is human: a
UniProt entry name ending _HUMAN (sp|P04406|G3P_HUMAN), or a protein the atlas holds other sites of.

These are the lab's internal numbers, not a published resource: the report says when the atlas was built, by which
rule, and whether the set may be quoted (the JSON's `quotable`).
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from ionomos import names
from ionomos.downstream.cys import site_key

CONTRACT = "ionomos-atlas-lookup/1"
TIERS = ("high", "medium", "low")
TSV_COLUMNS = ("accession", "site", "tier", "experiments", "spectra", "qc_suspect")
JSON_KEYS = ("contract", "atlas_sha256", "built", "rule", "sources", "set", "rows", "quotable")
# the atlas columns of results/cysteine_sites.tsv (add_tsv_columns)
SITE_COLUMNS = ["atlas_tier", "atlas_experiments", "atlas_spectra", "atlas_qc_suspect", "new_to_atlas"]
MAX_BYTES = 512 * 1024**2
# a canonical UniProt accession (uniprot.org/help/accession_numbers): P04406, A0A024R161; never an isoform (P04406-2)
_ACCESSION = re.compile(r"[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2}")
_ENTRY = re.compile(r"(?:^|\|)(?:sp|tr)\|[^|]+\|([^|\s]+)")  # sp|P04406|G3P_HUMAN -> G3P_HUMAN


class Site(NamedTuple):
    tier: str            # high / medium / low
    experiments: int     # atlas experiments that saw the site
    spectra: int
    qc_suspect: bool


@dataclass
class Lookup:
    folder: Path
    meta: dict                                        # the JSON, as published
    sites: dict[tuple[str, int], Site]
    proteins: frozenset[str] = field(default_factory=frozenset)  # accessions the atlas has any site of

    @property
    def quotable(self) -> bool:
        return self.meta.get("quotable") is True

    @property
    def built(self) -> str:
        """The day the atlas was built ("2026-10-19" from "2026-10-19T08:12:00Z"), or the text as published."""
        b = str(self.meta.get("built") or "").strip()
        m = re.match(r"\d{4}-\d{2}-\d{2}", b)
        return m.group(0) if m else _short(b, 40)

    @property
    def rule(self) -> str:
        return _short(str(self.meta.get("rule") or "").strip(), 160)

    def get(self, accession: str, site: int) -> Site | None:
        try:
            return self.sites.get((str(accession).strip().upper(), int(site)))
        except (TypeError, ValueError):
            return None

    def knows(self, accession: str) -> bool:
        return str(accession).strip().upper() in self.proteins

    def describe(self) -> str:
        return (f"{len(self.sites):,} sites, built {self.built or '?'}, rule {_short(self.rule, 80) or '?'}, "
                f"quotable: {'yes' if self.quotable else 'no'}")


class _Malformed(ValueError):
    pass


def _short(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


def _show(v: str) -> str:
    return repr(_short(v, 30))


def _text(path: Path) -> str:
    """The file as text: UTF-8 with or without a BOM, CRLF or LF line ends."""
    try:
        if path.stat().st_size > MAX_BYTES:
            raise _Malformed(f"{path.name} is larger than {MAX_BYTES // 2**20} MB")
        data = path.read_bytes()
    except OSError as exc:
        raise _Malformed(f"{path.name} can't be read ({exc.strerror or exc})") from None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise _Malformed(f"{path.name} is not UTF-8 text") from None


def _read_json(path: Path) -> dict:
    try:
        meta = json.loads(_text(path))
    except ValueError as exc:
        raise _Malformed(f"{path.name} is not JSON ({exc})") from None
    if not isinstance(meta, dict):
        raise _Malformed(f"{path.name} is not a JSON object")
    if meta.get("contract") != CONTRACT:
        raise _Malformed(f"{path.name} has no contract" if "contract" not in meta else
                         f"{path.name} is contract {_show(str(meta['contract']))}, not {CONTRACT}")
    lacks = [k for k in JSON_KEYS if k not in meta]
    if lacks:
        raise _Malformed(f"{path.name} lacks {', '.join(lacks)}")
    if not isinstance(meta["quotable"], bool):
        raise _Malformed(f"{path.name}: quotable is {_show(str(meta['quotable']))}, not true or false")
    if not isinstance(meta["rows"], int) or isinstance(meta["rows"], bool) or meta["rows"] < 0:
        raise _Malformed(f"{path.name}: rows is {_show(str(meta['rows']))}, not a count")
    for k in ("built", "rule"):
        if not isinstance(meta[k], str):
            raise _Malformed(f"{path.name}: {k} is not text")
    return meta


def _count(v: str) -> int | None:
    v = v.strip()
    return int(v) if v.isascii() and v.isdigit() else None


def _read_tsv(path: Path) -> dict[tuple[str, int], Site]:
    lines = _text(path).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    rows = csv.reader(lines, delimiter="\t")
    head = next((r for r in rows if any(c.strip() for c in r)), None)
    if head is None:
        raise _Malformed(f"{path.name} is empty")
    col = {h.strip().lower(): j for j, h in reversed(list(enumerate(head)))}
    lacks = [c for c in TSV_COLUMNS if c not in col]
    if lacks:
        raise _Malformed(f"{path.name} lacks the column{'s' if len(lacks) > 1 else ''} {', '.join(lacks)}")
    ia, isite, itier, iexp, ispec, iqc = (col[c] for c in TSV_COLUMNS)
    width = max(ia, isite, itier, iexp, ispec, iqc) + 1
    tiers = {t: t for t in TIERS}
    sites: dict[tuple[str, int], Site] = {}
    for r in rows:
        if len(r) < width:
            if not any(c.strip() for c in r):
                continue  # a blank line (the last line end)
            raise _Malformed(f"{path.name} line {rows.line_num} has {len(r)} columns, the header {len(head)}")
        where = f"{path.name} line {rows.line_num}"
        acc = r[ia].strip().upper()
        if not acc:
            raise _Malformed(f"{where} has no accession")
        pos = _count(r[isite])
        if not pos:
            raise _Malformed(f"{where}: site {_show(r[isite])} is not a positive whole number")
        tier = tiers.get(r[itier].strip().lower())
        if tier is None:
            raise _Malformed(f"{where}: tier {_show(r[itier])} is not high, medium or low")
        exp, spec = _count(r[iexp]), _count(r[ispec])
        if exp is None or spec is None:
            bad = r[iexp] if exp is None else r[ispec]
            raise _Malformed(f"{where}: {'experiments' if exp is None else 'spectra'} {_show(bad)} is not a count")
        qc = r[iqc].strip()
        if qc not in ("0", "1"):
            raise _Malformed(f"{where}: qc_suspect {_show(r[iqc])} is not 0 or 1")
        if (acc, pos) in sites:
            raise _Malformed(f"{where} repeats {acc} site {pos}")
        sites[(acc, pos)] = Site(tier, exp, spec, qc == "1")
    return sites


def read_lookup(folder: Path | None) -> tuple[Lookup | None, str]:
    """The atlas' site lookup in `folder` (the _atlas folder), read only. (Lookup, its description), or (None, why):
    "not published yet: no <path>" when a file isn't there, "malformed: <what>" when it can't be used. Never raises
    for a bad file."""
    if folder is None:
        return None, "not published yet: no atlas folder (the hand-off is off or has no handoff_dir)"
    folder = Path(folder)
    tsv, js = folder / names.ATLAS_LOOKUP_TSV, folder / names.ATLAS_LOOKUP_JSON
    for p in (tsv, js):
        if not p.is_file():
            return None, f"not published yet: no {p}"
    try:
        meta = _read_json(js)
        sites = _read_tsv(tsv)
    except _Malformed as exc:
        return None, f"malformed: {exc}"
    except Exception as exc:  # noqa: BLE001 - whatever is in the file, the report goes on without it
        return None, f"malformed: {type(exc).__name__}: {exc}"
    if meta["rows"] != len(sites):
        return None, (f"malformed: {js.name} says {meta['rows']:,} rows, {tsv.name} has {len(sites):,} "
                      "(still syncing, or the two files are from different builds)")
    lk = Lookup(folder, meta, sites, frozenset(a for a, _ in sites))
    return lk, lk.describe()


# ------------------------------------------------------------------------------------------------- the join --


def canonical(feature_id: str) -> tuple[str | None, int | None, bool]:
    """(canonical UniProt accession or None, site position or None, human by its entry name) of a cysteine site
    id as the isoDTB table names it: 'sp|P04406|G3P_HUMAN|C152' -> ('P04406', 152, True). An isoform, a
    contaminant, a non-UniProt id or a residue other than C gives (None, ...)."""
    acc, residue, pos = site_key(feature_id)
    if pos is None or residue != "C" or not _ACCESSION.fullmatch(acc.strip()):
        return None, pos, False
    entry = _ENTRY.search(feature_id.rpartition("|")[0])
    return acc.strip(), pos, bool(entry) and entry.group(1).upper().endswith("_HUMAN")


def site_context(lookup: Lookup, feature_id: str) -> dict | None:
    """The atlas beside one site: {"tier", "experiments", "spectra", "qc_suspect", "new"}; for a human site the
    atlas doesn't have, tier / experiments / spectra are None and new is True. None (blank cells) when the atlas
    can't say: an isoform, not a UniProt accession, or not a human protein."""
    acc, pos, human = canonical(feature_id)
    if acc is None:
        return None
    hit = lookup.get(acc, pos)
    if hit is not None:
        return {"tier": hit.tier, "experiments": hit.experiments, "spectra": hit.spectra,
                "qc_suspect": hit.qc_suspect, "new": False}
    if human or lookup.knows(acc):  # the lookup is human only: a missing site is only new for a human protein
        return {"tier": None, "experiments": None, "spectra": None, "qc_suspect": False, "new": True}
    return None


def line(lookup: Lookup) -> str:
    """The report's one line under Liganded sites: whose numbers these are, and whether they may be quoted."""
    return (f"Atlas columns: the lab's internal cysteine atlas, not a published resource (built {lookup.built or '?'}"
            + (f"; tier rule: {lookup.rule}" if lookup.rule else "") + f"; {len(lookup.sites):,} human sites). "
            + ("The atlas marks this set as quotable." if lookup.quotable else
               "The atlas marks this set as not quotable: use it inside the lab, don't cite it."))


def with_atlas(cys: dict, lookup: Lookup, feature_ids: list[str]) -> dict:
    """The report's D.cys (cys.report_payload) with "atlas": per site (parallel to D.cys.i) the tier index into
    tiers, the experiments and spectra, qc (0 / 1) and new (0 / 1); null where the atlas can't say."""
    if not cys.get("ran"):
        return cys
    ctx = [site_context(lookup, feature_ids[i]) if 0 <= i < len(feature_ids) else None for i in cys["i"]]
    lig = cys.get("nlig") or [0] * len(ctx)
    held = [c is not None and not c["new"] for c in ctx]
    new = [c is not None and c["new"] for c in ctx]
    return {**cys, "atlas": {
        "built": lookup.built, "rule": lookup.rule, "quotable": lookup.quotable, "sites": len(lookup.sites),
        "line": line(lookup), "tiers": list(TIERS),
        "tier": [TIERS.index(c["tier"]) if h else None for c, h in zip(ctx, held, strict=True)],
        "exp": [c["experiments"] if h else None for c, h in zip(ctx, held, strict=True)],
        "spec": [c["spectra"] if h else None for c, h in zip(ctx, held, strict=True)],
        "qc": [1 if h and c["qc_suspect"] else 0 for c, h in zip(ctx, held, strict=True)],
        "new": [1 if x else 0 for x in new],
        "counts": {"held": sum(held), "new": sum(new), "blank": sum(1 for c in ctx if c is None),
                   "ligHeld": sum(1 for h, n in zip(held, lig, strict=True) if h and n),
                   "ligNew": sum(1 for x, n in zip(new, lig, strict=True) if x and n)}}}


def add_tsv_columns(lookup: Lookup, columns: list[str], rows: list[dict]) -> tuple[list[str], list[dict]]:
    """cysteine_sites.tsv (cys.columns / cys.table_rows) with the atlas columns: the tier, experiments, spectra and
    qc_suspect (yes / no) of a site the atlas holds; new_to_atlas yes / no; blank where the atlas can't say."""
    out = []
    for r in rows:
        c = site_context(lookup, str(r.get("id") or ""))
        held = c is not None and not c["new"]
        out.append({**r, "atlas_tier": c["tier"] if held else "", "atlas_experiments": c["experiments"] if held else "",
                    "atlas_spectra": c["spectra"] if held else "",
                    "atlas_qc_suspect": ("yes" if c["qc_suspect"] else "no") if held else "",
                    "new_to_atlas": "" if c is None else "yes" if c["new"] else "no"})
    return [*columns, *SITE_COLUMNS], out
