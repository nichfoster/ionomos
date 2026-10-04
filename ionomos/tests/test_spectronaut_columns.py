"""D74: the Spectronaut column list Ionomos ships instead of a report schema file (.rs, whose format is not public).
One list for the loader and for `ionomos spectronaut-columns`, so what a lab ticks is what is read."""
import pytest

from ionomos import cli
from ionomos.downstream import engines

COLS = [c for c, _need, _why in engines.SPECTRONAUT_COLUMNS]
NEEDED = [c for c, need, _why in engines.SPECTRONAUT_COLUMNS if need]


def _report(path, head, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\t".join(head) + "\n" + "".join("\t".join(str(c) for c in r) + "\n" for r in rows),
                    encoding="utf-8")
    return path


def _rows(head):
    out = []
    for k, run in enumerate(["DMSO_1", "DMSO_2", "Drug_1", "Drug_2"]):
        for j, (pg, gene) in enumerate((("P10275", "AR"), ("P02768", "ALB"), ("Q9Y6K9", "IKBKG"))):
            cell = {"R.FileName": f"20260930_{run}", "R.Condition": run.split("_")[0], "R.Replicate": run[-1],
                    "PG.ProteinGroups": pg, "PG.Genes": gene, "PG.ProteinDescriptions": f"{gene} protein",
                    "PG.ProteinNames": f"{gene}_HUMAN", "PG.Quantity": 1000.0 * (k + 1) + j, "PG.Qvalue": 0.001,
                    "EG.Qvalue": 0.05 if j == 2 else 0.001, "EG.PrecursorId": f"_PEPK{j}_.2",
                    "EG.ModifiedSequence": "_PEPK_", "FG.Charge": 2, "FG.Quantity": 300.0 * (k + 1) + j}
            out.append([cell[h] for h in head])
    return out


def test_the_loader_reads_exactly_the_listed_columns(tmp_path, monkeypatch):
    seen = []
    real = engines._read_long
    monkeypatch.setattr(engines, "_read_long", lambda path, want: seen.append(list(want)) or real(path, want))
    head = [*reversed(COLS), "EG.ModifiedSequence", "FG.Charge"]  # any order, other columns ignored
    path = _report(tmp_path / "Ionomos_Report.tsv", head, _rows(head))
    assert engines._spectronaut_score(path) == 0.9
    m = engines.load_spectronaut(path)
    assert seen == [COLS]
    assert sorted(m.samples) == ["DMSO_1", "DMSO_2", "Drug_1", "Drug_2"]       # named by R.Condition / R.Replicate
    assert m.condition["Drug_2"] == "Drug" and m.replicate["Drug_2"] == 2
    assert [f.id for f in m.features] == ["P10275", "P02768"]   # EG.Qvalue above 1%: left out
    assert m.features[0].label == "AR" and m.features[0].description == "AR protein"


def test_the_needed_columns_alone_are_enough(tmp_path):
    path = _report(tmp_path / "r.tsv", NEEDED, _rows(NEEDED))
    assert set(NEEDED) == {"R.FileName", "PG.ProteinGroups", "PG.Quantity"}
    m = engines.load_spectronaut(path)
    assert len(m.samples) == 4 and len(m.features) == 3


def test_the_column_list_and_where_it_is_saved(tmp_path, capsys):
    text = engines.spectronaut_columns_text()
    for c, need, _why in engines.SPECTRONAUT_COLUMNS:
        line = next(ln for ln in text.splitlines() if ln.strip().startswith(c + " "))
        assert ("needed" in line) == need
    assert "Report perspective" in text and "Export Report" in text and ".rs" in text and "not published" in text
    assert cli.main(["spectronaut-columns"]) == 0
    assert capsys.readouterr().out.strip() == text.strip()
    out = tmp_path / "share"
    assert cli.main(["spectronaut-columns", "--out", str(out)]) == 0
    assert cli.main(["spectronaut-columns", "--out", str(out)]) == 0     # never over an existing file
    capsys.readouterr()
    files = sorted(p.name for p in out.iterdir())
    assert files == ["Ionomos_Spectronaut_report_columns-2.txt", "Ionomos_Spectronaut_report_columns.txt"]
    assert (out / engines.SPECTRONAUT_COLUMNS_FILE).read_text(encoding="utf-8") == text


@pytest.mark.parametrize("quantity", ["PG.Quantity", "PG.MS2Quantity"])
def test_either_protein_quantity_is_read(tmp_path, quantity):
    head = [quantity if c == "PG.Quantity" else c for c in NEEDED]
    rows = _rows(NEEDED)
    path = _report(tmp_path / "r.tsv", head, rows)
    assert engines._spectronaut_score(path) == 0.9 and engines.load_spectronaut(path).meta["quantity"] == quantity
