-- file.sql
CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY NOT NULL CHECK (id LIKE 'sha256:%'),
    slug TEXT NOT NULL CHECK (slug <> '') UNIQUE,
    size INTEGER NOT NULL DEFAULT 0 CHECK (size >= 0),
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
