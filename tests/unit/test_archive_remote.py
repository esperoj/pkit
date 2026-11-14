"""The archive public remote API against an in-memory backend.

Everything except the remote itself is real: sqlite records, the CAS, hashing.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from pkit import archive
from pkit.archive.cas import cas_path
from pkit.archive.errors import ArchiveError, InputError, TransientError
from pkit.archive.remote import RemoteFile
from pkit.meta.db import MetaDB
from pkit.meta.models import File

PAYLOAD = b"pandora"
LOCATION = "ia:x/v1.cbz"


def _stored(db_path, record_id) -> File:
    with MetaDB(db_path) as meta:
        record = meta.get(record_id)
    assert isinstance(record, File)
    return record


def _record_locations(db_path, record_id, locations: list[str]) -> None:
    with MetaDB(db_path) as meta:
        meta.put(replace(_stored(db_path, record_id), locations=locations))


class TestMatch:
    def test_registers_known_hash(self, repo, db_path, added, install_backend):
        install_backend(files=[RemoteFile(LOCATION, (f"sha1:{added.sha1}",))])

        result = archive.match("ia:x", db_path=db_path)

        assert result.matched == 1
        assert _stored(db_path, added.record_id).locations == [LOCATION]

    def test_matches_by_alternate_hash(self, repo, db_path, added, install_backend):
        install_backend(files=[RemoteFile(LOCATION, (f"md5:{added.md5}",))])

        assert archive.match("ia:x", db_path=db_path).matched == 1

    def test_counts_unknown_and_unhashed(self, repo, db_path, added, install_backend):
        install_backend(
            files=[
                RemoteFile("ia:x/unknown", ("md5:" + "0" * 32,)),
                RemoteFile("ia:x/opaque", ()),
            ]
        )

        result = archive.match("ia:x", db_path=db_path)

        assert result == archive.MatchResult(matched=0, unknown=1, no_hash=1)
        assert _stored(db_path, added.record_id).locations == []

    def test_is_idempotent(self, repo, db_path, added, install_backend):
        install_backend(files=[RemoteFile(LOCATION, (f"sha1:{added.sha1}",))])

        archive.match("ia:x", db_path=db_path)
        archive.match("ia:x", db_path=db_path)

        assert _stored(db_path, added.record_id).locations == [LOCATION]


class TestCopy:
    def test_uploads_missing_content(self, repo, db_path, added, install_backend):
        backend = install_backend(remote="ia:x")

        result = archive.copy(repo / "manga", "ia:x", db_path=db_path)

        assert result.copied == 1
        assert result.present == 0
        assert result.locations == (LOCATION,)
        assert backend.uploads == ["v1.cbz"]
        assert backend.store[LOCATION] == PAYLOAD
        assert _stored(db_path, added.record_id).locations == [LOCATION]

    def test_skips_present_content(self, repo, db_path, added, install_backend):
        backend = install_backend(files=[RemoteFile(LOCATION, (f"sha1:{added.sha1}",))])

        result = archive.copy(repo / "manga", "ia:x", db_path=db_path)

        assert result.copied == 0
        assert result.present == 1
        assert backend.uploads == []
        assert result.locations == ()


class TestVerify:
    def test_reports_local_and_remote(self, repo, db_path, added, install_backend):
        install_backend(files=[RemoteFile(LOCATION, (f"sha1:{added.sha1}",))])
        _record_locations(db_path, added.record_id, [LOCATION])

        result = archive.verify(repo / "manga" / "v1.cbz", db_path=db_path)

        assert result.ok
        assert [(check.location, check.status) for check in result.checks] == [
            ("local", "ok"),
            (LOCATION, "ok"),
        ]

    def test_local_mismatch_when_object_is_tampered(self, repo, db_path, added):
        obj = cas_path(repo / ".git" / "pkit-archive", added.sha256)
        obj.chmod(0o644)
        obj.write_bytes(b"tampered")

        result = archive.verify(repo / "manga" / "v1.cbz", db_path=db_path)

        assert not result.ok
        assert result.checks[0] == archive.Check(location="local", status="mismatch")

    def test_local_missing_after_drop(self, repo, db_path, added):
        cas_path(repo / ".git" / "pkit-archive", added.sha256).unlink()

        result = archive.verify(repo / "manga" / "v1.cbz", db_path=db_path)

        assert result.checks == (archive.Check(location="local", status="missing"),)

    def test_remote_only_and_local_only(self, repo, db_path, added, install_backend):
        install_backend(files=[RemoteFile(LOCATION, (f"sha1:{added.sha1}",))])
        _record_locations(db_path, added.record_id, [LOCATION])

        remote_only = archive.verify(repo / "manga" / "v1.cbz", ["ia:x"], db_path=db_path)
        local_only = archive.verify(repo / "manga" / "v1.cbz", ["local"], db_path=db_path)

        assert [check.location for check in remote_only.checks] == [LOCATION]
        assert [check.location for check in local_only.checks] == ["local"]

    def test_remote_mismatch(self, repo, db_path, added, install_backend):
        install_backend(files=[RemoteFile(LOCATION, ("sha1:" + "0" * 40,))])
        _record_locations(db_path, added.record_id, [LOCATION])

        result = archive.verify(repo / "manga" / "v1.cbz", db_path=db_path)

        assert result.checks[-1].status == "mismatch"
        assert not result.ok

    def test_works_from_bare_hash_id(self, repo, db_path, added):
        result = archive.verify(
            added.record_id,
            ["local"],
            db_path=db_path,
            cas_dir=repo / ".git" / "pkit-archive",
        )

        assert result.record_id == added.record_id
        assert result.checks == (archive.Check(location="local", status="ok"),)

    def test_unknown_record_is_input_error(self, repo, db_path):
        with pytest.raises(InputError, match="run add first"):
            archive.verify("sha256:" + "0" * 64, db_path=db_path)


class TestDrop:
    def test_deletes_local_copy_after_remote_proof(self, repo, db_path, added, install_backend):
        backend = install_backend(files=[RemoteFile(LOCATION, (f"sha1:{added.sha1}",))])
        archive.match("ia:x", db_path=db_path)

        result = archive.drop(repo / "manga" / "v1.cbz", db_path=db_path)

        assert result == archive.DropResult(record_id=added.record_id, location=LOCATION)
        assert not cas_path(repo / ".git" / "pkit-archive", added.sha256).exists()
        assert (repo / "manga" / "v1.cbz").is_symlink()  # the name survives, dangling
        assert backend.verify_calls == [LOCATION]

    def test_tries_locations_until_one_verifies(self, repo, db_path, added, install_backend):
        install_backend(
            files=[
                RemoteFile("ia:a/v1.cbz", ("sha1:" + "0" * 40,)),
                RemoteFile("ia:b/v1.cbz", (f"sha1:{added.sha1}",)),
            ]
        )
        _record_locations(db_path, added.record_id, ["ia:a/v1.cbz", "ia:b/v1.cbz"])

        result = archive.drop(repo / "manga" / "v1.cbz", db_path=db_path)

        assert result.location == "ia:b/v1.cbz"

    def test_refuses_when_nothing_verifies(self, repo, db_path, added, install_backend):
        install_backend(files=[RemoteFile(LOCATION, ("sha1:" + "0" * 40,))])
        _record_locations(db_path, added.record_id, [LOCATION])

        with pytest.raises(ArchiveError, match="refusing to drop"):
            archive.drop(repo / "manga" / "v1.cbz", db_path=db_path)
        assert cas_path(repo / ".git" / "pkit-archive", added.sha256).exists()

    def test_reraises_transient_after_trying_all(self, repo, db_path, added, install_backend):
        backend = install_backend(files=[RemoteFile(LOCATION, (f"sha1:{added.sha1}",))])
        backend.error = TransientError("queue is full")
        _record_locations(db_path, added.record_id, [LOCATION])

        with pytest.raises(TransientError, match="queue is full"):
            archive.drop(repo / "manga" / "v1.cbz", db_path=db_path)
        assert cas_path(repo / ".git" / "pkit-archive", added.sha256).exists()

    def test_requires_local_copy(self, repo, db_path, added, install_backend):
        install_backend(files=[RemoteFile(LOCATION, (f"sha1:{added.sha1}",))])
        archive.match("ia:x", db_path=db_path)
        cas_path(repo / ".git" / "pkit-archive", added.sha256).unlink()

        with pytest.raises(InputError, match="no local copy"):
            archive.drop(repo / "manga" / "v1.cbz", db_path=db_path)


class TestGet:
    def test_restores_verified_copy(self, repo, db_path, added, install_backend):
        install_backend(remote="ia:x")
        archive.copy(repo / "manga", "ia:x", db_path=db_path)
        obj = cas_path(repo / ".git" / "pkit-archive", added.sha256)
        obj.unlink()

        result = archive.get(repo / "manga" / "v1.cbz", db_path=db_path)

        assert result == archive.GetResult(
            record_id=added.record_id, location=LOCATION, path=str(obj)
        )
        assert obj.read_bytes() == PAYLOAD
        assert obj.stat().st_mode & 0o777 == 0o444
        assert (repo / "manga" / "v1.cbz").read_bytes() == PAYLOAD

    def test_local_copy_short_circuits(self, repo, db_path, added, install_backend):
        backend = install_backend(remote="ia:x")

        result = archive.get(repo / "manga" / "v1.cbz", db_path=db_path)

        assert result.location == "local"
        assert backend.uploads == []

    def test_rejects_corrupted_download(self, repo, db_path, added, install_backend):
        backend = install_backend(remote="ia:x")
        archive.copy(repo / "manga", "ia:x", db_path=db_path)
        obj = cas_path(repo / ".git" / "pkit-archive", added.sha256)
        obj.unlink()
        backend.store[LOCATION] = b"corrupted"

        with pytest.raises(ArchiveError, match="could be fetched and verified"):
            archive.get(repo / "manga" / "v1.cbz", db_path=db_path)
        assert not obj.exists()

    def test_prefers_the_requested_remote(self, repo, db_path, added, install_backend):
        install_backend(remote="ia:x")
        archive.copy(repo / "manga", "ia:x", db_path=db_path)
        _record_locations(db_path, added.record_id, ["ia:other/v1.cbz", LOCATION])
        obj = cas_path(repo / ".git" / "pkit-archive", added.sha256)
        obj.unlink()

        result = archive.get(repo / "manga" / "v1.cbz", "ia:x", db_path=db_path)

        assert result.location == LOCATION

    def test_no_remote_copy_fails(self, repo, db_path, added, install_backend):
        install_backend()
        cas_path(repo / ".git" / "pkit-archive", added.sha256).unlink()

        with pytest.raises(ArchiveError, match="could be fetched and verified"):
            archive.get(repo / "manga" / "v1.cbz", db_path=db_path)


class TestRemoveLocation:
    def test_removes_one_location(self, repo, db_path, added, install_backend):
        install_backend(files=[RemoteFile(LOCATION, (f"sha1:{added.sha1}",))])
        archive.match("ia:x", db_path=db_path)

        record = archive.remove_location(repo / "manga" / "v1.cbz", LOCATION, db_path=db_path)

        assert record.locations == []
        assert _stored(db_path, added.record_id).locations == []
