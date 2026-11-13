# pkit meta

Record database: sqlite tables for typed records, a JSON file repo for
sync between machines, and integrity checks.

## CLI

```sh
pkit meta dump [REPO]     # database -> <repo>/<table>.json; prints the record count
pkit meta load [REPO]     # JSON repo -> database; prints the record count
pkit meta validate        # orphan link rows, duplicate ids; silent when clean
```

`REPO` defaults to `[meta] repo_path`. The database comes from
`[meta] db_path` or `$PKIT_META_DB_PATH`. `validate` prints each issue to
stdout and exits 1 if any were found (0 when clean).

## Sync between machines

```sh
pkit meta dump ~/records       # then commit ~/records to git
git pull && pkit meta load      # on the other machine
```

One file per table, one JSON array per file, rows sorted by id, so
unchanged data produces byte-identical files (clean git diffs). Link
tables are not synced separately — links live inside their parent rows
and are rebuilt on load.

## Library

```python
from pkit.meta import db, sync
from pkit.meta.models import Book, File, Person

with db.session() as conn:
    db.put(conn, Book(id="b1", title="Pandora Hearts", author_ids=["p1"]))
    record = db.get(conn, "b1")
    records = db.find(conn, Book)  # newest first
    issues = db.validate(conn)  # orphans, duplicate ids

sync.dump()  # records -> JSON repo
sync.load()  # JSON repo -> records
```

`put` accepts a record or a plain mapping; `get`/`find` return typed
records. `File.id` is `sha256:<hex>` so re-adding the same bytes is the
same row.
