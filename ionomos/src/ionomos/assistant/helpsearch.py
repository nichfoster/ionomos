"""
Search the help (ionomos/help/*.md) by words: BM25 over the entries, title weighted above the text.

    search("why are there no hits", limit=3) -> [Hit(id="faq.no-hits", title=..., score=...), ...]

SQLite's FTS5 does the ranking when this Python's SQLite has it (CPython's installers do); otherwise the
same BM25 in plain Python (`engine="python"`). Both read the entries from help.entries(), so the index can
never fall behind the help. No embeddings: ROADMAP Phase 6 adds them only if the evaluation shows misses.
"""
from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass
from functools import lru_cache

from ionomos import help as helpdoc

TITLE_WEIGHT = 4.0
K1, B = 1.2, 0.75
_WORD = re.compile(r"[A-Za-z0-9]+")
# words that carry no topic; dropped from the query only (the index keeps everything)
STOP = frozenset("a an and are as at be by can do does did for from how i in is it me my of on or so that the this "
                 "to was what when where which who why will with you your".split())


@dataclass(frozen=True)
class Hit:
    id: str
    title: str
    score: float


def stem(w: str) -> str:
    """A crude stem, so that delete / deletes / deleted and file / files meet. Both engines index these."""
    for suffix in ("ing", "ed", "es", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            w = w[: -len(suffix)]
            break
    return w[:-1] if w.endswith("e") and len(w) > 3 else w


def words(text: str) -> list[str]:
    """Lower-case stemmed words; an issue code or id like NO_TABLE / faq.no-hits is split into its parts."""
    return [stem(w.lower()) for w in _WORD.findall(text or "")]


def query_words(query: str) -> list[str]:
    ws = [stem(w.lower()) for w in _WORD.findall(query or "") if w.lower() not in STOP]
    return list(dict.fromkeys(ws))[:24]


@lru_cache(maxsize=1)
def _docs() -> tuple[tuple[str, str, str], ...]:
    """(id, the stemmed words of the title and the id, those of the text) per help entry, in page order."""
    return tuple((e.id, " ".join(words(f"{e.title} {e.id}")), " ".join(words(helpdoc.to_text(e.body, width=10_000))))
                 for e in helpdoc.entries().values())


@lru_cache(maxsize=1)
def fts5_available() -> bool:
    try:
        con = sqlite3.connect(":memory:")
        try:
            con.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        finally:
            con.close()
        return True
    except sqlite3.Error:
        return False


def _fts5(ws: list[str], limit: int) -> list[Hit]:
    con = sqlite3.connect(":memory:")  # ~150 short entries: built per search, so no connection is shared
    try:
        con.execute("CREATE VIRTUAL TABLE h USING fts5(id UNINDEXED, title, body, tokenize='unicode61')")
        con.executemany("INSERT INTO h VALUES (?, ?, ?)", _docs())
        match = " OR ".join(f'"{w}"' for w in ws)  # words are [a-z0-9]+ only: nothing of FTS5's syntax gets in
        rows = con.execute("SELECT id, bm25(h, 0.0, ?, 1.0) AS s FROM h WHERE h MATCH ? ORDER BY s, id LIMIT ?",
                           (TITLE_WEIGHT, match, limit)).fetchall()
    finally:
        con.close()
    ents = helpdoc.entries()
    return [Hit(i, ents[i].title, round(-s, 4)) for i, s in rows]  # FTS5's bm25 is negative: lower is better


@lru_cache(maxsize=1)
def _index():
    docs = [(i, t.split(), b.split()) for i, t, b in _docs()]
    n = len(docs)
    df: dict[str, int] = {}
    for _i, t, b in docs:
        for w in set(t) | set(b):
            df[w] = df.get(w, 0) + 1
    avg_t = sum(len(t) for _i, t, _b in docs) / max(n, 1)
    avg_b = sum(len(b) for _i, _t, b in docs) / max(n, 1)
    return docs, df, n, avg_t, avg_b


def _python(ws: list[str], limit: int) -> list[Hit]:
    docs, df, n, avg_t, avg_b = _index()
    ents = helpdoc.entries()
    out = []
    for i, t, b in docs:
        score = 0.0
        for w in ws:
            if w not in df:
                continue
            idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
            for field, avg, weight in ((t, avg_t, TITLE_WEIGHT), (b, avg_b, 1.0)):
                tf = field.count(w)
                if tf:
                    score += weight * idf * tf * (K1 + 1) / (tf + K1 * (1 - B + B * len(field) / (avg or 1)))
        if score > 0:
            out.append(Hit(i, ents[i].title, round(score, 4)))
    out.sort(key=lambda h: (-h.score, h.id))
    return out[:limit]


def search(query: str, limit: int = 3, engine: str | None = None) -> list[Hit]:
    """The best entries for a question, best first. engine: "fts5" | "python" | None (FTS5 when available)."""
    ws = query_words(query)
    if not ws:
        return []
    limit = max(1, min(int(limit), 10))
    exact = helpdoc.topic(query.strip()) if len(query.split()) == 1 else None  # an id, an issue code, a kind
    if engine is None:
        engine = "fts5" if fts5_available() else "python"
    hits = _fts5(ws, limit) if engine == "fts5" else _python(ws, limit)
    if exact and exact in helpdoc.entries():
        hits = [Hit(exact, helpdoc.entries()[exact].title, 1e9)] + [h for h in hits if h.id != exact]
    return hits[:limit]
