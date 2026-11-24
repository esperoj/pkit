-- files: one content-addressed file per row. name is the tree name add
-- first recorded for these bytes (first add wins).

CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY NOT NULL CHECK (id LIKE 'sha256:%'),
    slug TEXT NOT NULL CHECK (slug <> '') UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0 CHECK (size >= 0),
    sha256 TEXT NOT NULL DEFAULT '',
    sha1 TEXT NOT NULL DEFAULT '',
    md5 TEXT NOT NULL DEFAULT '',
    identifiers TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(identifiers) AND json_type(identifiers) = 'array'),
    extra TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(extra) AND json_type(extra) = 'object'),
    locations TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(locations) AND json_type(locations) = 'array'),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS files_updated
    ON files(updated_at);

CREATE INDEX IF NOT EXISTS files_created
    ON files(created_at);

-- Partial indexes: only rows that actually carry a hash are indexed.
CREATE INDEX IF NOT EXISTS files_sha256
    ON files(sha256) WHERE sha256 <> '';

CREATE INDEX IF NOT EXISTS files_sha1
    ON files(sha1) WHERE sha1 <> '';

CREATE INDEX IF NOT EXISTS files_md5
    ON files(md5) WHERE md5 <> '';

CREATE TRIGGER IF NOT EXISTS files_touch_updated
AFTER UPDATE ON files
WHEN old.updated_at IS new.updated_at
BEGIN
    UPDATE files
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = new.id;
END;

CREATE TRIGGER IF NOT EXISTS files_keep_id
BEFORE UPDATE ON files
WHEN old.id IS NOT new.id
BEGIN
    SELECT RAISE(ABORT, 'files.id is immutable');
END;
