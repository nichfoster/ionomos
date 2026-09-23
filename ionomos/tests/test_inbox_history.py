from contextlib import closing

from ionomos.inbox import remove
from ionomos.intake import DraftFile, IntakeResult, intake, note_path, plan
from ionomos.ledger import Ledger
from ionomos.manifest import Overrides
from ionomos.naming_history import remember, suggestions
from tests.conftest import make_drop


def test_remove_folder_and_note(lab):
    folder = make_drop(lab['inbox'], 'bad', ['bad.raw'])
    note_path(folder).write_text('bad')
    destination = remove(lab['inbox'], folder)
    assert not folder.exists() and not note_path(folder).exists()
    assert (destination / 'bad.raw').exists()
    assert note_path(destination).exists()


def test_remove_during_resolve_does_not_recreate_note_or_queue(lab):
    folder = make_drop(lab['inbox'], 'bad', ['bad.raw'])

    class Resolver:
        def resolve(self, draft):
            remove(lab['inbox'], folder)
            return None

    with closing(Ledger(lab['cfg'].database)) as ledger:
        assert intake(folder, lab['cfg'], ledger, Resolver()) == IntakeResult.RETRY
        assert not ledger.list()
    assert not note_path(folder).exists()


def test_remove_one_raw_during_resolve_retries(lab):
    folder = make_drop(lab['inbox'], 'bad', ['a.raw', 'b.raw'])

    class Resolver:
        def resolve(self, draft):
            remove(lab['inbox'], folder / 'a.raw')
            return Overrides(user='EJQ', method='DIA')

    with closing(Ledger(lab['cfg'].database)) as ledger:
        assert intake(folder, lab['cfg'], ledger, Resolver()) == IntakeResult.RETRY
        assert not ledger.list()
    assert (folder / 'b.raw').exists()
    assert not (folder / 'experiment.yaml').exists()


def test_learning_scoped_and_reused_for_new_replicates(lab):
    cfg = lab['cfg']
    remember(cfg, 'example', 'EJQ', 'DIA', [DraftFile('vehicle_1.raw', 'DMSO', '1', '')])
    folder = make_drop(lab['inbox'], 'EJQ_DIA_next', ['vehicle_2.raw'])
    assert plan(folder, cfg).manifest[0].experiment == 'DMSO'
    assert plan(folder, cfg).manifest[0].bioreplicate == 2
    assert not suggestions(cfg, 'Isaac', 'DIA', ['vehicle_2.raw'])
    assert not suggestions(cfg, 'EJQ', 'TMT', ['vehicle_2.raw'])
    remember(cfg, 'conflict', 'EJQ', 'DIA', [DraftFile('vehicle_1.raw', 'Control', '1', '')])
    assert not suggestions(cfg, 'EJQ', 'DIA', ['vehicle_1.raw', 'vehicle_2.raw'])


def test_learning_from_accepted_resolver(lab):
    from ionomos.manifest import FileOverride

    folder = make_drop(lab['inbox'], 'unknown', ['vehicle_1.raw'])

    class Resolver:
        def resolve(self, draft):
            return Overrides(user='EJQ', method='DIA', files={
                'vehicle_1.raw': FileOverride('DMSO', 1, -1)})

    with closing(Ledger(lab['cfg'].database)) as ledger:
        assert intake(folder, lab['cfg'], ledger, Resolver()) == IntakeResult.QUEUED
    assert suggestions(lab['cfg'], 'EJQ', 'DIA', ['vehicle_2.raw'])['vehicle_2.raw'].experiment == 'DMSO'


def test_removed_storage_not_ingested(lab):
    from ionomos.watcher import Watcher

    folder = make_drop(lab['inbox'], 'EJQ_DIA_bad', ['bad_1.raw'])
    remove(lab['inbox'], folder)
    seen = []
    watcher = Watcher(lab['inbox'], on_stable=seen.append, stable_seconds=0)
    watcher.scan_once()
    watcher.scan_once()
    assert not seen


def test_deleted_raw_does_not_leave_stale_gui_override(lab):
    from ionomos.manifest import FileOverride, save_overrides

    folder = make_drop(lab['inbox'], 'EJQ_DIA_run', ['a_1.raw', 'b_1.raw'])
    save_overrides(folder, Overrides(resolved_by='gui', files={
        'a_1.raw': FileOverride('A', 1, -1), 'b_1.raw': FileOverride('B', 1, -1)}))
    remove(lab['inbox'], folder / 'a_1.raw')
    assert [line.experiment for line in plan(folder, lab['cfg']).manifest] == ['B']


def test_explicit_yaml_beats_history(lab):
    from ionomos.manifest import FileOverride, save_overrides

    remember(lab['cfg'], 'example', 'EJQ', 'DIA', [DraftFile('vehicle_1.raw', 'DMSO', '1', '')])
    folder = make_drop(lab['inbox'], 'EJQ_DIA_next', ['vehicle_2.raw'])
    save_overrides(folder, Overrides(files={'vehicle_2.raw': FileOverride('Control', 2, -1)}))
    assert plan(folder, lab['cfg']).manifest[0].experiment == 'Control'
