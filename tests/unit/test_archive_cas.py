from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from pkit.archive.cas import (
    Hashes,
    cas_path,
    find_cas,
    hash_file,
    ingest,
    is_hash,
    link_hash,
    make_link,
    set_readonly,
    walk_files,
)
from pkit.archive.errors import InputError
from pkit.archive.remote import merge_locations, selects, split_wayback, wayback_location

PAYLOAD = b"pandora"
SHA256 = hashlib.sha256(PAYLOAD).hexdigest()


def test_hashes_tokens():
    hashes = Hashes(sha256="a" * 64, sha1="b" * 40, md5="c" * 32)

    assert hashes.record_id == f"sha256:{'a' * 64}"
    assert hashes.identifiers == (f"sha1:{'b' * 40}", f"md5:{'c' * 32}")
    assert hashes.tokens == (hashes.record_id, *hashes.identifiers)


def test_hashes_without_alternates():
    hashes = Hashes(sha256="a" * 64)

    assert hashes.identifiers == ()
    assert hashes.tokens == (hashes.record_id,)


def test_hash_file_computes_all_three(tmp_path):
    file = tmp_path / "f"
    file.write_bytes(PAYLOAD)

    hashes = hash_file(file)

    assert hashes.sha256 == SHA256
    assert hashes.sha1 == hashlib.sha1(PAYLOAD).hexdigest()
    assert hashes.md5 == hashlib.md5(PAYLOAD).hexdigest()


def test_is_hash():
    assert is_hash("a" * 64)
    assert not is_hash("A" * 64)
    assert not is_hash("a" * 63)
    assert not is_hash("z" * 64)
    assert not is_hash("")


def test_cas_path_fans_out():
    digest = "ab" + "c" * 62

    assert cas_path(Path("/cas"), digest) == Path("/cas/ab") / digest


def test_make_link_is_relative_and_idempotent(tmp_path):
    obj = tmp_path / "obj"
    obj.write_bytes(b"x")
    link = tmp_path / "nested" / "link"

    assert make_link(link, obj) is True
    assert make_link(link, obj) is False
    assert not os.path.isabs(os.readlink(link))
    assert link.read_bytes() == b"x"


def test_make_link_repoints_changed_target(tmp_path):
    obj = tmp_path / "obj"
    obj.write_bytes(b"x")
    link = tmp_path / "link"
    make_link(link, tmp_path / "elsewhere")

    assert make_link(link, obj) is True
    assert os.readlink(link) == os.path.relpath(obj, tmp_path)


def test_make_link_rejects_regular_file(tmp_path):
    obj = tmp_path / "obj"
    obj.write_bytes(b"x")
    plain = tmp_path / "plain"
    plain.write_bytes(b"x")

    with pytest.raises(InputError, match="not a symlink"):
        make_link(plain, obj)


def test_link_hash_reads_target_basename(tmp_path):
    obj = tmp_path / "obj"
    obj.write_bytes(b"x")
    link = tmp_path / "link"
    make_link(link, obj)

    assert link_hash(link) is None  # target basename is "obj", not a hash
    make_link(link, tmp_path / SHA256)
    assert link_hash(link) == SHA256
    assert link_hash(obj) is None  # not a symlink


def test_set_readonly(tmp_path):
    obj = tmp_path / "obj"
    obj.write_bytes(b"x")
    obj.chmod(0o644)

    assert set_readonly(obj) is True
    assert obj.stat().st_mode & 0o777 == 0o444
    assert set_readonly(obj) is False


def test_ingest_moves_locks_and_dedupes(tmp_path):
    cas = tmp_path / "cas"
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.write_bytes(PAYLOAD)
    second.write_bytes(PAYLOAD)

    obj = ingest(first, cas, Hashes(sha256=SHA256))
    assert obj == cas_path(cas, SHA256)
    assert obj.read_bytes() == PAYLOAD
    assert obj.stat().st_mode & 0o777 == 0o444
    assert not first.exists()

    assert ingest(second, cas, Hashes(sha256=SHA256)) == obj
    assert not second.exists()
    assert len(list(cas.rglob(SHA256))) == 1


def test_ingest_of_the_object_itself(tmp_path):
    cas = tmp_path / "cas"
    obj = cas_path(cas, SHA256)
    obj.parent.mkdir(parents=True)
    obj.write_bytes(PAYLOAD)

    assert ingest(obj, cas, Hashes(sha256=SHA256)) == obj


def test_walk_files_skips_git_and_sorts(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("git")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "y").write_bytes(b"y")
    (tmp_path / "a").write_bytes(b"a")

    assert [item.name for item in walk_files(tmp_path)] == ["a", "y"]


def test_walk_files_yields_symlinks_without_following(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "target"
    target.write_bytes(b"x")
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "link").symlink_to(target)

    assert [item.name for item in walk_files(tree)] == ["link"]
    assert not (tree / "link").is_dir()


def test_walk_files_single_file_or_link_root(tmp_path):
    file = tmp_path / "plain"
    file.write_bytes(b"x")
    assert list(walk_files(file)) == [file]

    other = tmp_path / "other"
    other.write_bytes(b"y")
    link = tmp_path / "link"
    link.symlink_to(other)
    assert list(walk_files(link)) == [link]


def test_walk_files_rejects_missing_root(tmp_path):
    with pytest.raises(InputError, match="not a file or directory"):
        list(walk_files(tmp_path / "gone"))


def test_find_cas_walks_up_to_git(tmp_path):
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "deep" / "tree"
    nested.mkdir(parents=True)

    assert find_cas(nested) == tmp_path / ".git" / "pkit-archive"


def test_find_cas_explicit_override_wins(tmp_path):
    (tmp_path / ".git").mkdir()
    override = tmp_path / "elsewhere"

    assert find_cas(tmp_path, override) == override


def test_find_cas_requires_git(tmp_path):
    with pytest.raises(InputError, match="pass cas_dir explicitly"):
        find_cas(tmp_path)


def test_find_cas_rejects_git_file(tmp_path):
    (tmp_path / ".git").write_text("gitdir: elsewhere\n")

    with pytest.raises(InputError, match="not a directory"):
        find_cas(tmp_path)


def test_split_wayback():
    location = wayback_location("https://web.archive.org/web/20260101000000id_/https://host/a")

    assert split_wayback(location) == ("20260101000000", "https://host/a")


def test_split_wayback_rejects_non_capture():
    with pytest.raises(InputError, match="not a wayback capture url"):
        split_wayback("wayback:https://web.archive.org/web/https://host/a")


def test_merge_locations_unions_plain_locations():
    assert merge_locations(["ia:x", "s3:y"], ["s3:y", "gdrive:z"]) == [
        "gdrive:z",
        "ia:x",
        "s3:y",
    ]


def test_merge_locations_keeps_oldest_capture():
    older = wayback_location("https://web.archive.org/web/20250101id_/https://host/a")
    newer = wayback_location("https://web.archive.org/web/20260101id_/https://host/a")

    assert merge_locations([newer], [older]) == [older]
    assert merge_locations([older], [newer]) == [older]


def test_selects_matches_remote_prefixes():
    assert selects("ia:x/v1.cbz", "ia:x")
    assert selects("ia:x/v1.cbz", "ia:x/")
    assert selects("s3:bucket/f", "s3:bucket")
    assert not selects("s3:bucket/f", "ia:x")


def test_selects_maps_wayback_prefix_through_base_url():
    location = wayback_location("https://web.archive.org/web/1id_/https://files.host/manga/v1.cbz")
    base = "https://files.host"

    assert selects(location, "wayback:manga", base)
    assert selects(location, "wayback:", base)
    assert not selects(location, "wayback:other", base)
    assert not selects(location, "wayback:manga", "")
