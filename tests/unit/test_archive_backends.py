import base64
import hashlib
import json
from pathlib import Path

import pytest

from pkit.archive.cas import Entry, Hashes
from pkit.archive.errors import ArchiveError, InputError, TransientError
from pkit.archive.rclone import RcloneBackend
from pkit.archive.remote import RemoteFile
from pkit.archive.wayback import WaybackBackend
from pkit.lib.shell import CommandNotFound, ShellError
from pkit.wayback.client import BASE_URL, CdxCapture, WaybackClient

PAYLOAD = b"pandora"
SHA1 = hashlib.sha1(PAYLOAD).hexdigest()
WANT = Hashes(sha256="c" * 64, sha1=SHA1)


def _row(hashes: dict, path: str = "v1.cbz") -> dict:
    return {"Path": path, "Name": path, "Size": len(PAYLOAD), "IsDir": False, "Hashes": hashes}


class TestRcloneBackend:
    def test_requires_colon_prefix(self):
        with pytest.raises(InputError, match="not an rclone remote"):
            RcloneBackend("local-path")

    def test_prefix_loses_trailing_slash(self):
        assert RcloneBackend("ia:x/dir/").prefix == "ia:x/dir"

    def test_location_forms(self):
        assert RcloneBackend("ia:x").location("a/b") == "ia:x/a/b"
        assert RcloneBackend("ia:x").location("/abs") == "ia:x/abs"
        assert RcloneBackend("ia:x/dir").location("a") == "ia:x/dir/a"
        assert RcloneBackend("ia:x").location("") == "ia:x"

    def test_list_parses_rows_and_hashes(self, rclone):
        rclone.rows = [
            _row(
                {"SHA-1": SHA1.upper(), "MD5": "B" * 32, "bogus": "x", "SHA-256": "c" * 64},
                "manga/v1.cbz",
            )
        ]

        with RcloneBackend("ia:x") as backend:
            files = backend.list()

        assert files == [
            RemoteFile(
                location="ia:x/manga/v1.cbz",
                hashes=(f"md5:{'b' * 32}", f"sha1:{SHA1}", f"sha256:{'c' * 64}"),
            )
        ]

    def test_list_uses_name_when_path_missing(self, rclone):
        rclone.rows = [{"Name": "loose.bin", "IsDir": False, "Hashes": {"MD5": "b" * 32}}]

        with RcloneBackend("ia:x") as backend:
            files = backend.list()

        assert files[0].location == "ia:x/loose.bin"

    def test_list_rejects_invalid_json(self, rclone):
        rclone.enqueue("not-json{")
        with RcloneBackend("ia:x") as backend, pytest.raises(ArchiveError, match="invalid JSON"):
            backend.list()

    @pytest.mark.parametrize(
        ("row", "status"),
        [
            (_row({"SHA-1": SHA1}), "ok"),
            (_row({"MD5": "c" * 32}), "mismatch"),
            (_row({}), "unknown"),
            ({"IsDir": True}, "missing"),
        ],
    )
    def test_verify_statuses(self, rclone, row, status):
        rclone.enqueue(json.dumps([row]))
        with RcloneBackend("ia:x") as backend:
            assert backend.verify("ia:x/v1.cbz", WANT) == status

    def test_verify_missing_when_rclone_says_not_found(self, rclone):
        rclone.enqueue(ShellError("rclone exited 3: not found", returncode=3))
        with RcloneBackend("ia:x") as backend:
            assert backend.verify("ia:x/v1.cbz", WANT) == "missing"

    def test_error_mapping(self, rclone):
        rclone.enqueue(CommandNotFound("command not found: rclone"))
        with RcloneBackend("ia:x") as backend, pytest.raises(InputError):
            backend.list()

        rclone.enqueue(ShellError("rclone exited 5: busy", returncode=5))
        with RcloneBackend("ia:x") as backend, pytest.raises(TransientError):
            backend.list()

        rclone.enqueue(ShellError("rclone exited 1: bad", returncode=1))
        with RcloneBackend("ia:x") as backend, pytest.raises(ArchiveError):
            backend.list()

    def test_copy_writes_manifest_then_lists(self, rclone, tmp_path, monkeypatch):
        root = tmp_path
        (root / "v1.cbz").write_bytes(PAYLOAD)
        entry = Entry(path=root / "v1.cbz", relpath="v1.cbz", size=7, hashes=WANT)
        rclone.rows = [_row({"SHA-1": SHA1})]
        manifests: list[str] = []
        real_run = RcloneBackend._run

        def spy_run(self, args):
            if "copy" in args:
                manifest = Path(next(arg for arg in args if arg.endswith(".txt")))
                manifests.append(manifest.read_text())
            return real_run(self, args)

        monkeypatch.setattr(RcloneBackend, "_run", spy_run)

        with RcloneBackend("ia:x") as backend:
            files = list(backend.copy(root, [entry]))

        assert manifests == ["v1.cbz\n"]
        copy_call = next(call for call in rclone.calls if call[1] == "copy")
        assert "--files-from-raw" in copy_call
        assert copy_call[-2:] == [str(root), "ia:x"]
        assert files[0].location == "ia:x/v1.cbz"

    def test_copy_with_no_entries_transfers_nothing(self, rclone, tmp_path):
        with RcloneBackend("ia:x") as backend:
            assert list(backend.copy(tmp_path, [])) == []
        assert not any(call[1] == "copy" for call in rclone.calls)

    def test_fetch_runs_copyto(self, rclone, tmp_path):
        with RcloneBackend("ia:x") as backend:
            backend.fetch("ia:x/v1.cbz", tmp_path / "dest")

        assert rclone.calls[0][:3] == ["rclone", "copyto", "ia:x/v1.cbz"]


def _capture(
    digest: bytes, timestamp: str, original: str = "https://host/manga/v1.cbz", code: str = "200"
):
    return CdxCapture(
        original=original,
        timestamp=timestamp,
        digest=base64.b32encode(digest).decode(),
        statuscode=code,
        mimetype="application/octet-stream",
    )


def _digest() -> bytes:
    return hashlib.sha1(PAYLOAD).digest()


class TestWaybackBackend:
    def backend(self, fake_http, *, base_url: str = "https://host") -> WaybackBackend:
        client = WaybackClient(http=fake_http, lock_file="", timeout=0.1)
        return WaybackBackend("manga", base_url=base_url, client=client, http=fake_http)

    def cdx(self, fake_http, captures, *, header: bool = False) -> None:
        rows = [
            ["original", "timestamp", "digest", "statuscode", "mimetype"],
            *[[c.original, c.timestamp, c.digest, c.statuscode, c.mimetype] for c in captures],
        ]
        fake_http.on(f"{BASE_URL}/cdx/search/cdx", json=rows if header else rows[1:])

    def test_list_dedupes_oldest_200_capture(self, fake_http):
        self.cdx(
            fake_http,
            [
                _capture(_digest(), "20260202000000"),
                _capture(_digest(), "20260101000000"),
                _capture(_digest(), "20260303000000", code="404"),
                _capture(b"\x00" * 20, "20260404000000", original="https://host/other"),
            ],
            header=True,
        )

        with self.backend(fake_http) as backend:
            files = backend.list()

        assert len(files) == 2
        ours = next(item for item in files if "manga" in item.location)
        assert "20260101000000" in ours.location
        assert ours.hashes == (f"sha1:{SHA1}",)

    def test_list_skips_unparsable_digests(self, fake_http):
        bad = CdxCapture(
            original="https://host/manga/v1.cbz",
            timestamp="20260101000000",
            digest="!!!not-base32!!!",
            statuscode="200",
            mimetype="application/octet-stream",
        )
        self.cdx(fake_http, [bad])

        with self.backend(fake_http) as backend:
            assert backend.list() == []

    def test_requires_base_url_to_build_urls(self, fake_http):
        with (
            self.backend(fake_http, base_url="") as backend,
            pytest.raises(InputError, match="wayback_url"),
        ):
            backend.url_for("v1.cbz")

    def test_copy_stages_and_saves(self, fake_http, disable_sleep, monkeypatch, tmp_path):
        staged: list[str] = []
        monkeypatch.setattr(RcloneBackend, "put", lambda self, src, rel: staged.append(rel))
        source = tmp_path / "v1.cbz"
        source.write_bytes(PAYLOAD)
        entry = Entry(path=source, relpath="v1.cbz", size=7, hashes=WANT)
        fake_http.on(f"{BASE_URL}/save/status/user", json={"available": 1})
        fake_http.on(f"{BASE_URL}/save", json={"job_id": "j1"})
        fake_http.on(
            f"{BASE_URL}/save/status/j1",
            json={
                "status": "success",
                "timestamp": "9",
                "original_url": "https://host/manga/v1.cbz",
            },
        )

        with self.backend(fake_http) as backend:
            out = list(backend.copy(tmp_path, [entry]))

        assert staged == ["v1.cbz"]
        assert out[0].location == f"wayback:{BASE_URL}/web/9id_/https://host/manga/v1.cbz"
        assert out[0].hashes == WANT.identifiers

    def test_copy_maps_rate_limit_to_transient(
        self, fake_http, disable_sleep, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(RcloneBackend, "put", lambda self, src, rel: None)
        entry = Entry(path=tmp_path / "v1.cbz", relpath="v1.cbz", size=7, hashes=WANT)
        fake_http.on(f"{BASE_URL}/save/status/user", json={"available": 1})
        fake_http.on(f"{BASE_URL}/save", status=429, text="rate limit")

        with self.backend(fake_http) as backend, pytest.raises(TransientError, match="429"):
            list(backend.copy(tmp_path, [entry]))

    def test_verify_ok_and_mismatch(self, fake_http):
        location = f"wayback:{BASE_URL}/web/9id_/https://host/manga/v1.cbz"

        self.cdx(fake_http, [_capture(_digest(), "9")])
        with self.backend(fake_http) as backend:
            assert backend.verify(location, WANT) == "ok"

        self.cdx(fake_http, [_capture(hashlib.sha1(b"other").digest(), "9")])
        with self.backend(fake_http) as backend:
            assert backend.verify(location, WANT) == "mismatch"

    def test_verify_ignores_non_200_captures(self, fake_http):
        location = f"wayback:{BASE_URL}/web/9id_/https://host/manga/v1.cbz"
        self.cdx(fake_http, [_capture(_digest(), "9", code="404")])

        with self.backend(fake_http) as backend:
            assert backend.verify(location, WANT) == "mismatch"

    def test_verify_unknown_when_not_indexed(self, fake_http):
        self.cdx(fake_http, [])
        location = f"wayback:{BASE_URL}/web/9id_/https://host/manga/v1.cbz"

        with self.backend(fake_http) as backend:
            assert backend.verify(location, WANT) == "unknown"

    def test_fetch_downloads_payload(self, fake_http, tmp_path):
        url = f"{BASE_URL}/web/9id_/https://host/manga/v1.cbz"
        fake_http.on(url, content=PAYLOAD)
        dest = tmp_path / "obj"

        with self.backend(fake_http) as backend:
            backend.fetch(f"wayback:{url}", dest)

        assert dest.read_bytes() == PAYLOAD

    def test_fetch_maps_server_errors_to_transient(self, fake_http, tmp_path):
        url = f"{BASE_URL}/web/9id_/https://host/manga/v1.cbz"
        fake_http.on(url, status=503, text="busy")

        with self.backend(fake_http) as backend, pytest.raises(TransientError):
            backend.fetch(f"wayback:{url}", tmp_path / "obj")

    def test_fetch_maps_client_errors_to_archive_error(self, fake_http, tmp_path):
        url = f"{BASE_URL}/web/9id_/https://host/manga/v1.cbz"
        fake_http.on(url, status=404, text="gone")

        with self.backend(fake_http) as backend, pytest.raises(ArchiveError):
            backend.fetch(f"wayback:{url}", tmp_path / "obj")
