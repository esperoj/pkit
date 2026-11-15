-- note.sql: the personal layer annotating any other record.
-- subject_ids is a JSON array (any record id), not a link table:
-- one attachment mechanism for every present and future record type.

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

CREATE INDEX IF NOT EXISTS notes_created
ON notes(created_at);

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
