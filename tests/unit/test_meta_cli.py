from __future__ import annotations

import json

import pytest

from pkit.lib.cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE
from pkit.meta.cli import main
from pkit.meta.db import MetaDB
from pkit.meta.models import File, Person

ID = "sha256:" + "a" * 64


def test_no_args_is_help(capsys):
    assert main([]) == EXIT_USAGE
    assert "dump" in capsys.readouterr().out


def test_help_lists_commands(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])

    assert exc.value.code == EXIT_OK
    out = capsys.readouterr().out
    assert "dump" in out and "load" in out and "validate" in out


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])

    assert exc.value.code == EXIT_OK
    assert "pkit meta" in capsys.readouterr().out


def test_dump_prints_count(run_cli, db_path, tmp_path):
    repo = tmp_path / "repo"
    with MetaDB(db_path) as meta:
        meta.put(File(id=ID, size=3, locations=["ia:item/a"]))

    code, out, err = run_cli(main, ["dump", str(repo)])

    assert code == EXIT_OK, err
    assert out.strip() == "1"
    assert json.loads((repo / "files.json").read_text())[0]["id"] == ID


def test_load_prints_count_and_restores(run_cli, db_path, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "persons.json").write_text(
        json.dumps([{"id": "p1", "type": "person", "name": "Jun"}]) + "\n",
        encoding="utf-8",
    )

    code, out, err = run_cli(main, ["load", str(repo)])

    assert code == EXIT_OK, err
    assert out.strip() == "1"
    with MetaDB(db_path) as meta:
        record = meta.get("p1")
    assert isinstance(record, Person)
    assert record.name == "Jun"


def test_dump_load_round_trip(run_cli, db_path, tmp_path):
    repo = tmp_path / "repo"
    with MetaDB(db_path) as meta:
        meta.put(File(id=ID, size=1, locations=["ia:item/a"]))

    code, out, err = run_cli(main, ["dump", str(repo)])
    assert code == EXIT_OK, err
    assert out.strip() == "1"

    db_path.unlink()
    code, out, err = run_cli(main, ["load", str(repo)])
    assert code == EXIT_OK, err
    assert out.strip() == "1"

    with MetaDB(db_path) as meta:
        record = meta.get(ID)
    assert isinstance(record, File)
    assert record.locations == ["ia:item/a"]


def test_load_missing_repo_is_usage_error(run_cli, db_path, tmp_path):
    code, out, err = run_cli(main, ["load", str(tmp_path / "nope")])

    assert code == EXIT_USAGE
    assert "no record repo" in err
    assert out == ""


def test_validate_clean_database_is_silent(run_cli, db_path):
    with MetaDB(db_path) as meta:
        meta.put(File(id=ID, size=3))

    code, out, err = run_cli(main, ["validate"])

    assert code == EXIT_OK, err
    assert out == ""


def test_validate_reports_orphans(run_cli, db_path):
    from pkit.meta.models import Book

    with MetaDB(db_path) as meta:
        meta.put(Book(id="b1", title="x", author_ids=["ghost"]))

    code, out, _err = run_cli(main, ["validate"])

    assert code == EXIT_ERROR
    assert "book_authors.person_id 'ghost' does not exist" in out


def test_unexpected_error_is_one(run_cli, db_path, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("pkit.meta.cli.MetaDB", explode)

    code, _out, err = run_cli(main, ["validate"])

    assert code == EXIT_ERROR
    assert "Error: boom" in err
