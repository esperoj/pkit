"""verify_wayback and cleanup: the stale-capture re-check and the stage reclaim.

verify_wayback re-runs verify semantics over whatever the records database
holds as old wayback locations; cleanup deletes the staged copies once the
safety window has passed. Together they are the monthly rhythm documented
in docs/archive.org.
"""

import json

import pytest

from pkit import archive
from pkit.archive.cas import hash_file
from pkit.archive.cli import main
from pkit.archive.db import ArchiveDB
from pkit.archive.errors import InputError, TransientError
from pkit.archive.remote import RemoteFile, wayback_location
from pkit.lib import shell
from pkit.lib.cli import EXIT_ERROR, EXIT_OK, EXIT_TEMPFAIL, EXIT_USAGE

OLD = "20200101000000"  # far older than any grace window
FRESH = "20991231235959"  # far newer: still inside the window


def location(timestamp: str, original: str = "https://files.example.com/manga/v1.cbz") -> str:
    return wayback_location(f"https://web.archive.org/web/{timestamp}id_/{original}")


def seeded(repo, db_path, *, locations: list[str]):
    """One ingested file record whose locations are exactly `locations`."""
    hashes = hash_file(repo / "manga" / "v1.cbz")
    archive.add(repo / "manga", db_path=db_path)
    with ArchiveDB(db_path) as db:
        db.register(f"sha256:{hashes.sha256}", locations)
    return hashes


class TestVerifyWayback:
    def test_only_stale_captures_are_rechecked(self, repo, db_path, install_backend):
        hashes = seeded(repo, db_path, locations=[location(OLD), location(FRESH)])
        install_backend(files=[RemoteFile(location=location(OLD), hashes=hashes.identifiers)])

        results = archive.verify_wayback(db_path=db_path)

        assert len(results) == 1
        assert results[0].record_id == f"sha256:{hashes.sha256}"
        assert [check.location for check in results[0].checks] == [location(OLD)]
        assert results[0].ok

    def test_fresh_only_means_no_results(self, repo, db_path, install_backend):
        install_backend()
        seeded(repo, db_path, locations=[location(FRESH)])

        assert archive.verify_wayback(db_path=db_path) == ()

    def test_rclone_locations_are_ignored(self, repo, db_path, install_backend):
        install_backend()
        seeded(repo, db_path, locations=["ia:bucket/v1.cbz", location(OLD)])

        results = archive.verify_wayback(db_path=db_path)

        assert [check.location for check in results[0].checks] == [location(OLD)]

    def test_mismatch_is_reported_not_ok(self, repo, db_path, install_backend):
        seeded(repo, db_path, locations=[location(OLD)])
        install_backend(files=[RemoteFile(location=location(OLD), hashes=("sha1:" + "9" * 40,))])

        results = archive.verify_wayback(db_path=db_path)

        assert results[0].checks[0].status == "mismatch"
        assert not results[0].ok

    def test_days_widens_the_window(self, repo, db_path, install_backend):
        install_backend()
        seeded(repo, db_path, locations=[location(OLD)])

        # A 10000-day window reaches back to 1999: the 2020 capture is too
        # new to re-verify. A 2000-day window (cutoff 2021) includes it.
        assert archive.verify_wayback(days=10000, db_path=db_path) == ()
        assert len(archive.verify_wayback(days=2000, db_path=db_path)) == 1

    def test_malformed_wayback_location_is_skipped(self, repo, db_path, install_backend):
        install_backend()
        seeded(repo, db_path, locations=["wayback:not-a-capture", location(OLD)])

        results = archive.verify_wayback(db_path=db_path)

        assert [check.location for check in results[0].checks] == [location(OLD)]


def test_wayback_stale_selects_from_the_database(repo, db_path, install_backend):
    hashes = seeded(repo, db_path, locations=[location(OLD), location(FRESH), "ia:bucket/v1.cbz"])

    with ArchiveDB(db_path) as db:
        assert db.wayback_stale(32) == [(f"sha256:{hashes.sha256}", (location(OLD),))]


class TestVerifyWaybackCli:
    def test_ok_exit_zero_with_report(self, run_cli, repo, db_path, added, install_backend):
        with ArchiveDB(db_path) as db:
            db.register(f"sha256:{added.sha256}", [location(OLD), location(FRESH)])
        install_backend(files=[RemoteFile(location=location(OLD), hashes=added.identifiers)])

        code, out, err = run_cli(main, ["verify-wayback"])

        assert code == EXIT_OK, err
        assert out.strip() == f"{location(OLD)}\tok"

    def test_empty_report_is_success(self, run_cli, repo, db_path, added, install_backend):
        with ArchiveDB(db_path) as db:
            db.register(f"sha256:{added.sha256}", [location(FRESH)])

        code, out, err = run_cli(main, ["verify-wayback"])

        assert code == EXIT_OK, err
        assert out == ""

    def test_mismatch_exits_one(self, run_cli, repo, db_path, added, install_backend):
        with ArchiveDB(db_path) as db:
            db.register(f"sha256:{added.sha256}", [location(OLD)])
        install_backend(files=[RemoteFile(location=location(OLD), hashes=("sha1:" + "9" * 40,))])

        code, out, _err = run_cli(main, ["verify-wayback"])

        assert code == EXIT_ERROR
        assert out.strip().endswith("mismatch")

    def test_json_output(self, run_cli, repo, db_path, added, install_backend):
        with ArchiveDB(db_path) as db:
            db.register(f"sha256:{added.sha256}", [location(OLD)])
        install_backend(files=[RemoteFile(location=location(OLD), hashes=added.identifiers)])

        code, out, err = run_cli(main, ["verify-wayback", "--json"])

        assert code == EXIT_OK, err
        assert json.loads(out)[0]["checks"][0]["status"] == "ok"

    def test_days_flag_reaches_the_sdk(self, run_cli, repo, db_path, added, install_backend):
        with ArchiveDB(db_path) as db:
            db.register(f"sha256:{added.sha256}", [location(OLD)])

        code, _out, err = run_cli(main, ["verify-wayback", "--days", "10000"])

        assert code == EXIT_OK, err  # the window excludes the 2020 capture


class TestCleanup:
    def test_cleanup_counts_and_deletes(self, rclone):
        rclone.rows = [{"Path": "a/v1.cbz"}, {"Path": "b/v2.cbz"}]

        result = archive.cleanup()

        assert result.eligible == 2
        assert rclone.calls[0] == [
            "rclone",
            "lsf",
            "--recursive",
            "--files-only",
            "--min-age=35d",
            "pkit-archive:",
        ]
        assert rclone.calls[1] == ["rclone", "delete", "--min-age=35d", "pkit-archive:"]

    def test_cleanup_dry_run_and_age(self, rclone):
        rclone.rows = []

        result = archive.cleanup(age="90d", dry_run=True)

        assert result.eligible == 0
        assert result.dry_run is True
        assert rclone.calls == [
            ["rclone", "lsf", "--recursive", "--files-only", "--min-age=90d", "pkit-archive:"],
            ["rclone", "delete", "--dry-run", "--min-age=90d", "pkit-archive:"],
        ]

    def test_cleanup_empty_stage_is_zero(self, rclone):
        assert archive.cleanup().eligible == 0
        assert rclone.calls[0][1] == "lsf"

    def test_cleanup_maps_transient(self, rclone):
        rclone.enqueue(shell.ShellError("rclone exited 5: busy", returncode=5))

        with pytest.raises(TransientError, match="busy"):
            archive.cleanup()

    def test_cleanup_maps_missing_binary_to_input_error(self, rclone):
        rclone.enqueue(shell.CommandNotFound("command not found: rclone"))

        with pytest.raises(InputError):
            archive.cleanup()


class TestCleanupCli:
    def test_plain_output(self, run_cli, rclone):
        rclone.rows = [{"Path": "a/v1.cbz"}, {"Path": "b/v2.cbz"}, {"Path": "c/v3.cbz"}]

        code, out, _err = run_cli(main, ["cleanup"])

        assert code == EXIT_OK
        assert out.strip() == "3 staged files older than 35d on pkit-archive — deleted"

    def test_dry_run_output(self, run_cli, rclone):
        rclone.rows = [{"Path": "a/v1.cbz"}]

        code, out, _err = run_cli(main, ["cleanup", "--dry-run", "--age", "90d"])

        assert code == EXIT_OK
        assert out.strip() == "1 staged files older than 90d on pkit-archive — would delete"

    def test_json_output(self, run_cli, rclone):
        rclone.rows = []

        code, out, err = run_cli(main, ["cleanup", "--json"])

        assert code == EXIT_OK, err
        assert json.loads(out) == {
            "remote": "pkit-archive:",
            "age": "35d",
            "eligible": 0,
            "dry_run": False,
        }

    def test_transient_maps_to_tempfail(self, run_cli, rclone):
        rclone.enqueue(shell.ShellError("rclone exited 5: busy", returncode=5))

        code, out, _err = run_cli(main, ["cleanup"])

        assert code == EXIT_TEMPFAIL
        assert out == ""

    def test_missing_binary_maps_to_usage(self, run_cli, rclone):
        rclone.enqueue(shell.CommandNotFound("command not found: rclone"))

        code, out, _err = run_cli(main, ["cleanup"])

        assert code == EXIT_USAGE
        assert out == ""
