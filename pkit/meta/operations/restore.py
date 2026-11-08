from threading import local
from os import getenv
from pathlib import Path
from pkit.meta.serialize import from_json
import pkit.meta.db as db

def restore():   
    data_path = Path.home() / ".local" / "share" / "pkit"
    archive_path = Path(getenv("PKIT_META_ARCHIVE", str(data_path / "records")))
    db_path = Path(getenv("PKIT_META_DB", str(data_path / "records.db")))
    arhive_files = archive_path.glob("*.json")
    entities = [from_json(f.read_bytes()) for f in arhive_files]

    with db.session(db_path) as conn:
        db.setup(conn)
        for entity in entities:
            db.create_entity(conn, entity)
