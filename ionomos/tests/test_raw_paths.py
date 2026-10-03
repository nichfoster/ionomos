"""intake.raw_paths: where each .raw of a drop is, for the app's inbox list and the review window's Delete
(D69: one folder per plex), and the name check reading the same files."""
from __future__ import annotations

import pytest

from ionomos import inbox, namecheck
from ionomos.intake import IntakeError, raw_paths


def _touch(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


@pytest.mark.parametrize("layout", ["", "raw", "plex"])
def test_raw_paths_finds_each_file_where_it_is(tmp_path, layout):
    drop = tmp_path / "inbox" / "20260902_EJQ_TMT_KL"
    if layout == "plex":
        want = [_touch(drop / "plexA" / "A_1.raw"), _touch(drop / "plexB" / "B_1.raw")]
    else:
        want = [_touch(drop / layout / "S_1.raw"), _touch(drop / layout / "S_2.raw")]
    _touch(drop / "notes.xlsx")
    assert sorted(raw_paths(drop)) == sorted(want)
    assert all(p.is_file() for p in raw_paths(drop))
    assert namecheck._raws_on_disk(drop) == sorted(p.name for p in want)


def test_a_raw_in_a_plex_folder_can_be_removed_and_recovered(tmp_path):
    box = tmp_path / "inbox"
    target = _touch(box / "drop" / "plexA" / "A_1.raw")
    _touch(box / "drop" / "plexB" / "B_1.raw")
    moved = inbox.remove(box, next(p for p in raw_paths(box / "drop") if p.name == "A_1.raw"))
    assert not target.exists() and moved.is_file()  # moved aside, never deleted
    assert [p.name for p in raw_paths(box / "drop")] == ["B_1.raw"]


def test_a_layout_intake_refuses_is_raised_but_the_name_check_still_reads_it(tmp_path):
    drop = tmp_path / "drop"
    _touch(drop / "S_1.raw")
    _touch(drop / "raw" / "S_2.raw")
    with pytest.raises(IntakeError):
        raw_paths(drop)
    assert namecheck._raws_on_disk(drop) == ["S_1.raw", "S_2.raw"]
