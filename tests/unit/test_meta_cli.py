import json

import pytest

from pkit.lib.cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE
from pkit.meta import config as meta_config
from pkit.meta.cli import main
from pkit.meta.db import MetaDB
from pkit.meta.models import File, Person, Text

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


def test_load_passes_lang_flag(run_cli, db_path, monkeypatch):
    seen: list[tuple[str | None, bool, str | None]] = []

    def fake_load(source=None, *, refresh=False, lang=None):
        seen.append((source, refresh, lang))
        return 1

    monkeypatch.setattr("pkit.meta.cli.load.load", fake_load)

    code, out, err = run_cli(main, ["load", "--lang", "zh", "wikidata:Q42"])

    assert code == EXIT_OK, err
    assert out.strip() == "1"
    assert seen == [("wikidata:Q42", False, "zh")]


def test_load_invalid_lang_flag_is_usage_error(run_cli, db_path):
    code, out, err = run_cli(main, ["load", "--lang", "fr", "wikidata:Q42"])

    assert code == EXIT_USAGE
    assert out == ""
    assert "invalid choice" in err


def test_config_lang_toml_and_env_precedence(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[meta]\nlang = "vi"\n', encoding="utf-8")
    monkeypatch.setenv("PKIT_CONFIG", str(cfg_file))
    monkeypatch.delenv("PKIT_META_LANG", raising=False)

    assert meta_config.load().lang == "vi"

    monkeypatch.setenv("PKIT_META_LANG", "zh")
    assert meta_config.load().lang == "zh"


def test_load_invalid_toml_lang_is_usage_error(run_cli, db_path, tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[meta]\nlang = "bogus"\n', encoding="utf-8")
    monkeypatch.setenv("PKIT_CONFIG", str(cfg_file))

    code, out, err = run_cli(main, ["load", "wikidata:Q42"])

    assert code == EXIT_USAGE
    assert "unsupported language" in err
    assert out == ""


def test_validate_clean_database_is_silent(run_cli, db_path):
    with MetaDB(db_path) as meta:
        meta.put(File(id=ID, size=3))

    code, out, err = run_cli(main, ["validate"])

    assert code == EXIT_OK, err
    assert out == ""


def test_validate_reports_orphans(run_cli, db_path):
    with MetaDB(db_path) as meta:
        meta.put(Text(id="t1", title="x", author_ids=["ghost"]))

    code, out, _err = run_cli(main, ["validate"])

    assert code == EXIT_ERROR
    assert "text_authors.person_id 'ghost' does not exist" in out


def test_unexpected_error_is_one(run_cli, db_path, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("pkit.meta.cli.MetaDB", explode)

    code, _out, err = run_cli(main, ["validate"])

    assert code == EXIT_ERROR
    assert "Error: boom" in err
