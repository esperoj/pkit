from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from dataclasses import replace

import pytest

from pkit import archive
from pkit.archive.cas import Entry, cas_path, hash_file
from pkit.archive.rclone import RcloneBackend
from pkit.archive.wayback import WaybackBackend
from pkit.meta import db
from pkit.meta.models import File
from pkit.wayback.client import CdxCapture, SaveOptions, SaveResult, WaybackClient

PAYLOAD = b"pandora"
SHA256 = hashlib.sha256(PAYLOAD).hexdigest()
LOCATION = "ia:x/v1.cbz"


@pytest.fixture
def tree(tmp_path):
    (tmp_path / ".git").mkdir()
    folder = tmp_path / "manga"
    folder.mkdir()
    (folder / "v1.cbz").write_bytes(PAYLOAD)
    return tmp_path


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "records.db"


@pytest.fixture
def added(tree, db_path):
    archive.add(tree / "manga", db_path=db_path)
    return hash_file(cas_path(tree / ".git" / "pkit-archive", SHA256))


def _stored(db_path, record_id) -> File:
    """db.get returns Record | None; narrow once here instead of in every test."""
    with db.session(db_path) as conn:
        record = db.get(conn, record_id)
    assert isinstance(record, File)
    return record


def _record_location(db_path, record_id, location) -> None:
    with db.session(db_path) as conn:
        record = db.get(conn, record_id)
        assert isinstance(record, File)
        db.put(conn, replace(record, locations=[location]))


class FakeRclone:
    def __init__(self, rows):
        self.rows = rows
        self.calls: list[list[str]] = []

    def __call__(self, args, *, timeout=None):
        self.calls.append(list(args))
        out = json.dumps(self.rows) if "lsjson" in args else ""
        return subprocess.CompletedProcess(list(args), 0, out, "")


def _row(hashes, path="v1.cbz"):
    return {"Path": path, "Name": path, "Size": len(PAYLOAD), "IsDir": False, "Hashes": hashes}


def test_match_registers_known_hash(tree, db_path, added, monkeypatch):
    fake = FakeRclone([_row({"SHA-1": added.sha1})])
    monkeypatch.setattr("pkit.archive.rclone.shell.run", fake)

    result = archive.match("ia:pandora-hearts", db_path=db_path)

    assert result.matched == 1
    assert _stored(db_path, added.record_id).locations == ["ia:pandora-hearts/v1.cbz"]


def test_match_ignores_unknown_hashes(tree, db_path, added, monkeypatch):
    monkeypatch.setattr("pkit.archive.rclone.shell.run", FakeRclone([_row({"MD5": "0" * 32})]))
    assert archive.match("ia:x", db_path=db_path).unknown == 1


def test_copy_skips_present_content(tree, db_path, added, monkeypatch):
    fake = FakeRclone([_row({"MD5": added.md5})])
    monkeypatch.setattr("pkit.archive.rclone.shell.run", fake)

    result = archive.copy(tree / "manga", "ia:x", db_path=db_path)

    assert result.copied == 0
    assert result.present == 1
    assert not any(call[1] == "copy" for call in fake.calls)


def test_copy_uploads_missing(tree, db_path, added, monkeypatch):
    fake = FakeRclone([])
    monkeypatch.setattr("pkit.archive.rclone.shell.run", fake)
    monkeypatch.setattr(RcloneBackend, "list", lambda self: [])

    result = archive.copy(tree / "manga", "ia:x", db_path=db_path)

    assert result.copied == 1
    assert any(call[1] == "copy" for call in fake.calls)


def test_verify_reports_ok_and_missing(tree, db_path, added, monkeypatch):
    _record_location(db_path, added.record_id, LOCATION)
    monkeypatch.setattr("pkit.archive.rclone.shell.run", FakeRclone([_row({"SHA-1": added.sha1})]))

    result = archive.verify(tree / "manga" / "v1.cbz", db_path=db_path)

    assert result.ok
    assert [check.location for check in result.checks] == ["local", LOCATION]


def test_get_restores_and_verifies(tree, db_path, added, monkeypatch):
    item = tree / "manga" / "v1.cbz"
    obj = cas_path(tree / ".git" / "pkit-archive", added.sha256)
    _record_location(db_path, added.record_id, LOCATION)
    obj.unlink()

    def fake_fetch(self, location, dest):
        dest.write_bytes(PAYLOAD)

    monkeypatch.setattr(RcloneBackend, "fetch", fake_fetch)
    result = archive.get(item, db_path=db_path)

    assert result.location == LOCATION
    assert obj.read_bytes() == PAYLOAD
    assert item.read_bytes() == PAYLOAD


def _capture(digest_bytes, timestamp, original="https://host/manga/v1.cbz", code="200"):
    return CdxCapture(
        original=original,
        timestamp=timestamp,
        digest=base64.b32encode(digest_bytes).decode(),
        statuscode=code,
        mimetype="application/octet-stream",
    )


class FakeWayback(WaybackClient):
    """A real client with the transport methods stubbed: correct type, no network."""

    def __init__(self, captures: list[CdxCapture]) -> None:
        super().__init__(lock_file="", timeout=0.1)
        self.captures = captures
        self.saved: list[str] = []

    def cdx(
        self, url: str, *, match_type: str = "exact", limit: int | None = None
    ) -> list[CdxCapture]:
        return self.captures

    def save_url(self, url: str, *, opts: SaveOptions | None = None) -> SaveResult:
        self.saved.append(url)
        return SaveResult(
            url=url, archive_url=f"https://web.archive.org/web/9id_/{url}", job_id="j"
        )


def test_wayback_list_dedupes_oldest_capture(added):
    digest = hashlib.sha1(PAYLOAD).digest()
    captures = [
        _capture(digest, "20260202000000"),
        _capture(digest, "20260101000000"),
        _capture(digest, "20260303000000", code="404"),
    ]
    with (
        FakeWayback(captures) as client,
        WaybackBackend("manga", base_url="https://host", client=client) as backend,
    ):
        files = backend.list()

    assert len(files) == 1
    assert "20260101000000" in files[0].location
    assert files[0].hashes == (f"sha1:{added.sha1}",)


def test_wayback_copy_stages_and_saves(tree, added, monkeypatch):
    staged: list[str] = []
    monkeypatch.setattr(RcloneBackend, "put", lambda self, src, rel: staged.append(rel))
    entry = Entry(path=tree / "manga" / "v1.cbz", relpath="v1.cbz", size=7, hashes=added)

    with FakeWayback([]) as client:
        with WaybackBackend("manga", base_url="https://host", client=client) as backend:
            out = list(backend.copy(tree / "manga", [entry]))
        assert client.saved == ["https://host/manga/v1.cbz"]

    assert staged == ["v1.cbz"]
    assert out[0].location.startswith("wayback:https://web.archive.org/web/9id_/")


def test_wayback_verify_unknown_when_not_indexed(added):
    location = "wayback:https://web.archive.org/web/9id_/https://host/manga/v1.cbz"
    with (
        FakeWayback([]) as client,
        WaybackBackend("manga", base_url="https://host", client=client) as backend,
    ):
        assert backend.verify(location, added) == "unknown"
