-- sounds: songs, albums, singles, podcasts.

CREATE TABLE IF NOT EXISTS sounds (
    id TEXT PRIMARY KEY NOT NULL CHECK (id <> ''),
    slug TEXT NOT NULL CHECK (slug <> '') UNIQUE,

    title TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT '',
    publication_date TEXT NOT NULL DEFAULT '',
    lang TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    duration INTEGER NOT NULL DEFAULT 0 CHECK (duration >= 0),

    identifiers TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(identifiers) AND json_type(identifiers) = 'array'),

    extra TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(extra) AND json_type(extra) = 'object'),

    series TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(series) AND json_type(series) = 'array'),

    performers TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(performers) AND json_type(performers) = 'array'),

    publishers TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(publishers) AND json_type(publishers) = 'array'),

    genres TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(genres) AND json_type(genres) = 'array'),

    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS sounds_updated
ON sounds(updated_at);

CREATE INDEX IF NOT EXISTS sounds_created
ON sounds(created_at);

CREATE TRIGGER IF NOT EXISTS sounds_touch_updated
AFTER UPDATE ON sounds
WHEN old.updated_at IS new.updated_at
BEGIN
    UPDATE sounds
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = new.id;
END;

CREATE TRIGGER IF NOT EXISTS sounds_keep_id
BEFORE UPDATE ON sounds
WHEN old.id IS NOT new.id
BEGIN
    SELECT RAISE(ABORT, 'sounds.id is immutable');
END;
