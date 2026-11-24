-- texts: written works.

CREATE TABLE IF NOT EXISTS texts (
    id TEXT PRIMARY KEY NOT NULL CHECK (id <> ''),
    slug TEXT NOT NULL CHECK (slug <> '') UNIQUE,

    title TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT '',
    publication_date TEXT NOT NULL DEFAULT '',
    lang TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',

    identifiers TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(identifiers) AND json_type(identifiers) = 'array'),

    extra TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(extra) AND json_type(extra) = 'object'),

    series TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(series) AND json_type(series) = 'array'),

    publishers TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(publishers) AND json_type(publishers) = 'array'),

    genres TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(genres) AND json_type(genres) = 'array'),

    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS texts_updated
ON texts(updated_at);

CREATE INDEX IF NOT EXISTS texts_created
ON texts(created_at);

CREATE TRIGGER IF NOT EXISTS texts_touch_updated
AFTER UPDATE ON texts
WHEN old.updated_at IS new.updated_at
BEGIN
    UPDATE texts
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = new.id;
END;

CREATE TRIGGER IF NOT EXISTS texts_keep_id
BEFORE UPDATE ON texts
WHEN old.id IS NOT new.id
BEGIN
    SELECT RAISE(ABORT, 'texts.id is immutable');
END;