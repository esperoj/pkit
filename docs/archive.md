# pkit archive

Personal archive manager, inspired by git-annex. A library and a thin CLI.

## Model

| Layer | Holds |
|---|---|
| git | names, folders, history (symlinks are committed) |
| `.git/pkit-archive/` | local content, named by sha256, mode `0444` |
| meta `files` table | content identity + **remote** locations |

Tree entries are **relative symlinks** into the CAS. The target basename is the
hash, so renames, moves and a changed CAS root never lose identity. Local paths
are never stored in the database.

Objects are read-only from creation. Permissions stop mutation; the location
log stops deletion.

## Example

```python
from pkit import archive

manga = "/mnt/archive/pandora-hearts"

archive.add(manga)  # move into CAS, leave symlinks
archive.copy(manga, "ia:pandora-hearts")  # check dest, upload missing, register
archive.match("ia:pandora-hearts")  # register existing copies, no transfer
archive.verify(manga)  # report local + remote integrity
archive.drop(manga)  # delete local copy after proving a remote
archive.get(manga)  # fetch it back, verified
```

## CLI

```
pkit archive add PATH...               # ingest: CAS, symlinks, records
pkit archive fix PATH                  # re-point links, re-assert perms
pkit archive copy PATH REMOTE          # transfer missing, register
pkit archive match REMOTE              # register known content, no transfer
pkit archive verify FILE [REMOTE...]   # report local + remote integrity
pkit archive drop PATH                 # delete local copy once a remote is proven
pkit archive get FILE [REMOTE]         # restore a verified copy
pkit archive forget PATH               # delete records for dangling links
pkit archive remove-location FILE LOCATION
```

Every command takes `--json` for structured output. Plain output is one
summary line (plus data lines: checks for `verify`, missing paths for
`fix`, registered locations for `copy`). `verify` prints
`location<TAB>status` per check and exits 1 when any check is not `ok` —
that is the alert a sweep looks for. The database comes from `[meta]`
config; the CAS root is found by walking up to `.git` (override with
`cas_dir=` in the library, or place a `.git` directory above the tree).

## Functions

| Function | Purpose |
|---|---|
| `add(path)` | hash, move into CAS, symlink, register. Never follows foreign symlinks. |
| `fix(path)` | re-point links at the current CAS, re-assert `0444`. No DB, no network. |
| `copy(path, remote)` | list dest once, transfer only missing, register. Meaningful names. |
| `match(remote)` | list dest once, register hashes already known. No transfer. |
| `verify(file, remotes=())` | report `ok` / `mismatch` / `missing` / `unknown`. Never prunes. |
| `drop(path)` | verify one remote live, then delete the object. Link stays dangling. |
| `get(file, remote=None)` | restore an object; the download is verified before it enters the CAS. |
| `forget(path)` | delete records for dangling links. Refuses while the object exists. |
| `remove_location(file, location)` | manual cleanup after a verify report. |

`file` accepts a tree path (even dangling), a CAS path, or a `sha256:<hex>` id.
`verify` treats `"local"` as a pseudo-location, so remote-only checks need no
local content.

All functions accept `db_path=` and `cas_dir=` overrides; remote ones also accept
`config_path=`.

## Backends

| Scheme | Mechanism |
|---|---|
| `name:path` | rclone (`ia:`, `s3:`, `gdrive:`, …). Listing, copy, hash check. |
| `wayback:prefix` | CDX listing, staging upload, SPN2 capture. |

`wayback:` is reserved; do not name an rclone remote that.

Wayback locations are full capture URLs with a timestamp, because one URL can
hold different content over time:

```
wayback:https://web.archive.org/web/20260101120000id_/https://files.example.com/manga/v1.cbz
```

CDX digests are base32 SHA1, converted to hex and matched against the `sha1:`
identifier. Only `200` captures are used, and only the oldest capture per
(content, URL) is kept, so re-matching is idempotent.

## Config

```toml
[archive]
wayback_url = "https://files.example.com"
```

`PKIT_ARCHIVE_WAYBACK_URL` overrides it. Staging uses the rclone remote
`pkit-archive:`. Staging is **temporary and never registered as a location** —
only the Wayback capture is.

Wayback captures are registered unverified because CDX indexing lags. Run
`verify` later (a sharded monthly sweep works well). `drop` always verifies one
location live, so an unverified registration can never authorize a deletion.

## Git workflow

```sh
git mv old new && python -c "from pkit import archive; archive.fix('.')"
```

`git mv` renames a link without rewriting its target, so run `fix` afterwards.
Keep the repository and CAS on a filesystem that supports symlinks (Termux home
does; `/sdcard` does not).

## Errors

`InputError` (bad input, missing prerequisite), `TransientError` (retry later),
`ArchiveError` (everything else). Results are frozen dataclasses.

## Sync between machines

```python
from pkit.meta import dump, load

dump.dump("~/records")  # records -> JSON repo (commit it)
load.load("~/records")  # JSON repo -> records on the other machine
```

Or from the shell: `pkit meta dump` / `pkit meta load`
(see docs/meta.md).

The CAS is per-machine; the record repo is shared. Wayback locations can be
rebuilt from CDX alone if the database is lost.

