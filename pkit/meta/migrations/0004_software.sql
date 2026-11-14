-- software.sql

CREATE TABLE IF NOT EXISTS software (
    id TEXT PRIMARY KEY NOT NULL CHECK (id <> ''),
    slug TEXT NOT NULL CHECK (slug <> '') UNIQUE,

    title TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT '',
    publication_date TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    website TEXT NOT NULL DEFAULT '',

    identifiers TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(identifiers) AND json_type(identifiers) = 'array'),

    extra TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(extra) AND json_type(extra) = 'object'),

    series TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(series) AND json_type(series) = 'array'),

    developers TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(developers) AND json_type(developers) = 'array'),

    publishers TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(publishers) AND json_type(publishers) = 'array'),

    platforms TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(platforms) AND json_type(platforms) = 'array'),

    genres TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(genres) AND json_type(genres) = 'array'),

    licenses TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(licenses) AND json_type(licenses) = 'array'),

    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS software_updated
ON software(updated_at);

CREATE TRIGGER IF NOT EXISTS software_touch_updated
AFTER UPDATE ON software
WHEN old.updated_at IS new.updated_at
BEGIN
    UPDATE software
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = new.id;
END;

CREATE TRIGGER IF NOT EXISTS software_keep_id
BEFORE UPDATE ON software
WHEN old.id IS NOT new.id
BEGIN
    SELECT RAISE(ABORT, 'software.id is immutable');
END;
