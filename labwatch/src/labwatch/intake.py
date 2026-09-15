"""
Intake — turn a stable inbox folder into a queued job, or reject it.

Contract (Phase 1):
    intake(folder: Path, config, ledger) -> Job | None

    1. naming.parse_folder_name(folder.name, config.method_lookup)
    2. collect *.raw from folder/ and folder/raw/ ; naming.group_raws(...)
    3. optional experiment.yaml -> manifest.load_experiment_yaml (validate only here)
    4. dest = users_root / user / folder.name
         - users_root/user must exist  -> else reject ("unknown user; folder
           C:/Fragpipe_General/<user> does not exist")
         - dest must NOT exist          -> else reject ("already exists; rename
           with a suffix, e.g. _redo")
    5. move: os.replace(folder, dest). Same-volume => atomic rename. If
       config says cross-volume, copy+verify+delete instead (explicit flag).
    6. write dest/labwatch.json  {status, folder fields, raw layout, method
       config snapshot, timestamps, labwatch version}
    7. ledger.insert(...)

    Any NamingError / validation error -> write
    inbox/<folder.name>.REJECTED.txt with the reason and a pointer to the
    naming doc; leave the folder where it is; return None.

Never deletes. Never overwrites.
"""
from __future__ import annotations


def intake(folder, config, ledger):
    raise NotImplementedError("Phase 1")
