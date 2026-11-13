from __future__ import annotations

import sqlite3

import pytest

from pkit.meta import db, sync
from pkit.meta.models import File

ID = "sha256:" + "a" * 64


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "records.db"


def test_put_accepts_hash_id(db_path):
    with db.session(db_path) as conn:
        db.put(conn, File(id=ID, size=3, identifiers=["sha1:b", "md5:c"]))
        stored = db.get(conn, ID)
    assert isinstance(stored, File)
    assert stored.id == ID
    assert stored.slug == ID
    assert stored.size == 3


def test_id_must_be_content_addressed(db_path):
    with db.session(db_path) as conn, pytest.raises(sqlite3.IntegrityError):
        db.put(conn, File(id="not-a-hash"))


def test_dump_load_round_trip(db_path, tmp_path):
    repo = tmp_path / "repo"
    with db.session(db_path) as conn:
        db.put(conn, File(id=ID, size=1, locations=["ia:item/a"]))
    assert sync.dump(db_path, repo) >= 1
    other = tmp_path / "other.db"
    assert sync.load(other, repo) >= 1
    with db.session(other) as conn:
        record = db.get(conn, ID)
    assert isinstance(record, File)
    assert record.locations == ["ia:item/a"]
