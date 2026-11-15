-- media.sql: videos, sounds, and the notes that annotate any record.
-- notes.subject_ids is a JSON array (any record id), not a link table:
-- one attachment mechanism for every present and future record type.
-- created_at indexes live in 0006_created_indexes.sql with every other table.

CREATE TABLE IF NOT EXISTS videos (
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

    directors TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(directors) AND json_type(directors) = 'array'),

    publishers TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(publishers) AND json_type(publishers) = 'array'),

    genres TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(genres) AND json_type(genres) = 'array'),

    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS videos_updated
ON videos(updated_at);

CREATE TRIGGER IF NOT EXISTS videos_touch_updated
AFTER UPDATE ON videos
WHEN old.updated_at IS new.updated_at
BEGIN
    UPDATE videos
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = new.id;
END;

CREATE TRIGGER IF NOT EXISTS videos_keep_id
BEFORE UPDATE ON videos
WHEN old.id IS NOT new.id
BEGIN
    SELECT RAISE(ABORT, 'videos.id is immutable');
END;

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

CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY NOT NULL CHECK (id <> ''),
    slug TEXT NOT NULL CHECK (slug <> '') UNIQUE,

    title TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    rating TEXT NOT NULL DEFAULT '',
    start_date TEXT NOT NULL DEFAULT '',
    end_date TEXT NOT NULL DEFAULT '',
    duration INTEGER NOT NULL DEFAULT 0 CHECK (duration >= 0),

    identifiers TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(identifiers) AND json_type(identifiers) = 'array'),

    extra TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(extra) AND json_type(extra) = 'object'),

    subject_ids TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(subject_ids) AND json_type(subject_ids) = 'array'),

    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS notes_updated
ON notes(updated_at);

CREATE TRIGGER IF NOT EXISTS notes_touch_updated
AFTER UPDATE ON notes
WHEN old.updated_at IS new.updated_at
BEGIN
    UPDATE notes
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = new.id;
END;

CREATE TRIGGER IF NOT EXISTS notes_keep_id
BEFORE UPDATE ON notes
WHEN old.id IS NOT new.id
BEGIN
    SELECT RAISE(ABORT, 'notes.id is immutable');
END;

