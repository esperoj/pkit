-- person.sql

CREATE TABLE IF NOT EXISTS persons (
    id TEXT PRIMARY KEY NOT NULL CHECK (id <> ''),
    slug TEXT NOT NULL CHECK (slug <> '') UNIQUE,

    name TEXT NOT NULL DEFAULT '',
    gender TEXT NOT NULL DEFAULT '',
    birth_date TEXT NOT NULL DEFAULT '',
    death_date TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT '',

    identifiers TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(identifiers) AND json_type(identifiers) = 'array'),

    extra TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(extra) AND json_type(extra) = 'object'),

    occupations TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(occupations) AND json_type(occupations) = 'array'),

    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS persons_updated
ON persons(updated_at);

CREATE TRIGGER IF NOT EXISTS persons_touch_updated
AFTER UPDATE ON persons
WHEN old.updated_at IS new.updated_at
BEGIN
    UPDATE persons
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = new.id;
END;

CREATE TRIGGER IF NOT EXISTS persons_keep_id
BEFORE UPDATE ON persons
WHEN old.id IS NOT new.id
BEGIN
    SELECT RAISE(ABORT, 'persons.id is immutable');
END;
