-- book.sql

CREATE TABLE IF NOT EXISTS books (
    id TEXT PRIMARY KEY NOT NULL CHECK (id <> ''),
    slug TEXT NOT NULL CHECK (slug <> '') UNIQUE,

    title TEXT NOT NULL DEFAULT '',
    publication_date TEXT NOT NULL DEFAULT '',
    lang TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',

    identifiers TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(identifiers) AND json_type(identifiers) = 'array'),

    extra TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(extra) AND json_type(extra) = 'object'),

    publishers TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(publishers) AND json_type(publishers) = 'array'),

    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS books_updated
ON books(updated_at);

-- Intentionally no foreign-key constraints.
-- This allows importing books before persons exist.
CREATE TABLE IF NOT EXISTS book_authors (
    book_id TEXT NOT NULL CHECK (book_id <> ''),
    person_id TEXT NOT NULL CHECK (person_id <> ''),
    PRIMARY KEY (book_id, person_id)
);

CREATE INDEX IF NOT EXISTS book_authors_person
ON book_authors(person_id);

CREATE TRIGGER IF NOT EXISTS books_touch_updated
AFTER UPDATE ON books
WHEN old.updated_at IS new.updated_at
BEGIN
    UPDATE books
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = new.id;
END;

CREATE TRIGGER IF NOT EXISTS books_keep_id
BEFORE UPDATE ON books
WHEN old.id IS NOT new.id
BEGIN
    SELECT RAISE(ABORT, 'books.id is immutable');
END;
