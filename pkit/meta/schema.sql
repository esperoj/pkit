-- schema.sql

DROP VIEW IF EXISTS entities;

DROP TRIGGER IF EXISTS books_fill_slug_insert;
DROP TRIGGER IF EXISTS books_fill_slug_update;
DROP TRIGGER IF EXISTS persons_fill_slug_insert;
DROP TRIGGER IF EXISTS persons_fill_slug_update;

DROP TRIGGER IF EXISTS books_touch_updated;
DROP TRIGGER IF EXISTS persons_touch_updated;

DROP TRIGGER IF EXISTS books_keep_id;
DROP TRIGGER IF EXISTS persons_keep_id;

DROP TRIGGER IF EXISTS entities_insert_invalid;
DROP TRIGGER IF EXISTS entities_insert_book;
DROP TRIGGER IF EXISTS entities_insert_person;

DROP TRIGGER IF EXISTS entities_update_invalid;
DROP TRIGGER IF EXISTS entities_update_book;
DROP TRIGGER IF EXISTS entities_update_person;

DROP TRIGGER IF EXISTS entities_delete_invalid;
DROP TRIGGER IF EXISTS entities_delete_book;
DROP TRIGGER IF EXISTS entities_delete_person;

DROP VIEW IF EXISTS new_uuid;


-- Helper view to generate a UUID-style id.
-- Each SELECT from this view produces a new random id.
CREATE VIEW new_uuid AS
SELECT lower(
    hex(randomblob(4)) || '-' ||
    hex(randomblob(2)) || '-' ||
    '4' || substr(hex(randomblob(2)), 2) || '-' ||
    substr('89ab', 1 + abs(random() % 4), 1) || substr(hex(randomblob(2)), 2) || '-' ||
    hex(randomblob(6))
) AS id;


CREATE TABLE IF NOT EXISTS books (
    id TEXT PRIMARY KEY NOT NULL DEFAULT (
        lower(
            hex(randomblob(4)) || '-' ||
            hex(randomblob(2)) || '-' ||
            '4' || substr(hex(randomblob(2)), 2) || '-' ||
            substr('89ab', 1 + abs(random() % 4), 1) || substr(hex(randomblob(2)), 2) || '-' ||
            hex(randomblob(6))
        )
    ),

    slug TEXT NOT NULL DEFAULT '',
    archived_at TEXT NOT NULL DEFAULT '',

    title TEXT NOT NULL,
    publication_date TEXT NOT NULL,
    lang TEXT NOT NULL,
    description TEXT NOT NULL,

    identifiers TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(identifiers) AND json_type(identifiers) = 'array'),

    extra TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(extra) AND json_type(extra) = 'object'),

    publishers TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(publishers) AND json_type(publishers) = 'array'),

    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);


CREATE TABLE IF NOT EXISTS persons (
    id TEXT PRIMARY KEY NOT NULL DEFAULT (
        lower(
            hex(randomblob(4)) || '-' ||
            hex(randomblob(2)) || '-' ||
            '4' || substr(hex(randomblob(2)), 2) || '-' ||
            substr('89ab', 1 + abs(random() % 4), 1) || substr(hex(randomblob(2)), 2) || '-' ||
            hex(randomblob(6))
        )
    ),

    slug TEXT NOT NULL DEFAULT '',
    archived_at TEXT NOT NULL DEFAULT '',

    name TEXT NOT NULL,
    gender TEXT NOT NULL,
    birth_date TEXT NOT NULL,
    death_date TEXT NOT NULL,
    country TEXT NOT NULL,

    identifiers TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(identifiers) AND json_type(identifiers) = 'array'),

    extra TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(extra) AND json_type(extra) = 'object'),

    occupations TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(occupations) AND json_type(occupations) = 'array'),

    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);


CREATE TABLE IF NOT EXISTS book_authors (
    book_id TEXT NOT NULL CHECK (book_id <> '')
        REFERENCES books(id)
        ON DELETE CASCADE
        DEFERRABLE INITIALLY DEFERRED,

    person_id TEXT NOT NULL CHECK (person_id <> '')
        REFERENCES persons(id)
        ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED,

    PRIMARY KEY (book_id, person_id)
);


-- Active slugs must be unique, but empty slugs are allowed temporarily
-- because triggers may fill them from id.
CREATE UNIQUE INDEX IF NOT EXISTS idx_books_active_slug
    ON books(slug)
    WHERE archived_at = '' AND slug <> '';

CREATE UNIQUE INDEX IF NOT EXISTS idx_persons_active_slug
    ON persons(slug)
    WHERE archived_at = '' AND slug <> '';


CREATE INDEX IF NOT EXISTS idx_books_archived
    ON books(archived_at);

CREATE INDEX IF NOT EXISTS idx_persons_archived
    ON persons(archived_at);

CREATE INDEX IF NOT EXISTS idx_book_authors_person
    ON book_authors(person_id);


-- Fill slug from id when direct real-table inserts leave slug empty.

CREATE TRIGGER books_fill_slug_insert
AFTER INSERT ON books
WHEN new.slug = ''
BEGIN
    UPDATE books
    SET slug = id
    WHERE id = new.id AND slug = '';
END;


CREATE TRIGGER persons_fill_slug_insert
AFTER INSERT ON persons
WHEN new.slug = ''
BEGIN
    UPDATE persons
    SET slug = id
    WHERE id = new.id AND slug = '';
END;


-- If slug is explicitly emptied by a direct real-table update,
-- restore it from old slug or id.

CREATE TRIGGER books_fill_slug_update
AFTER UPDATE ON books
WHEN new.slug = ''
BEGIN
    UPDATE books
    SET slug = COALESCE(NULLIF(old.slug, ''), new.id)
    WHERE id = new.id AND slug = '';
END;


CREATE TRIGGER persons_fill_slug_update
AFTER UPDATE ON persons
WHEN new.slug = ''
BEGIN
    UPDATE persons
    SET slug = COALESCE(NULLIF(old.slug, ''), new.id)
    WHERE id = new.id AND slug = '';
END;


-- Keep updated_at fresh when real tables are updated directly.
-- For example, when Django admin updates a book row.

CREATE TRIGGER books_touch_updated
AFTER UPDATE ON books
WHEN old.updated_at IS new.updated_at
BEGIN
    UPDATE books
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = new.id;
END;


CREATE TRIGGER persons_touch_updated
AFTER UPDATE ON persons
WHEN old.updated_at IS new.updated_at
BEGIN
    UPDATE persons
    SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = new.id;
END;


CREATE TRIGGER books_keep_id
BEFORE UPDATE ON books
WHEN old.id IS NOT new.id
BEGIN
    SELECT RAISE(ABORT, 'books.id is immutable');
END;


CREATE TRIGGER persons_keep_id
BEFORE UPDATE ON persons
WHEN old.id IS NOT new.id
BEGIN
    SELECT RAISE(ABORT, 'persons.id is immutable');
END;


-- The entities view is a lazy JSON API over the real tables.

CREATE VIEW entities AS
SELECT
    b.id AS id,
    'book' AS type,
    b.slug AS slug,
    b.archived_at AS archived_at,
    b.created_at AS created_at,
    b.updated_at AS updated_at,
    json_object(
        'id', b.id,
        'type', 'book',
        'slug', b.slug,
        'identifiers', json(b.identifiers),
        'extra', json(b.extra),
        'title', b.title,
        'publication_date', b.publication_date,
        'lang', b.lang,
        'description', b.description,
        'author_ids', json(COALESCE(
            (
                SELECT json_group_array(x.person_id)
                FROM (
                    SELECT person_id
                    FROM book_authors
                    WHERE book_id = b.id
                    ORDER BY rowid
                ) AS x
            ),
            '[]'
        )),
        'publishers', json(b.publishers)
    ) AS payload
FROM books b

UNION ALL

SELECT
    p.id AS id,
    'person' AS type,
    p.slug AS slug,
    p.archived_at AS archived_at,
    p.created_at AS created_at,
    p.updated_at AS updated_at,
    json_object(
        'id', p.id,
        'type', 'person',
        'slug', p.slug,
        'identifiers', json(p.identifiers),
        'extra', json(p.extra),
        'name', p.name,
        'gender', p.gender,
        'birth_date', p.birth_date,
        'death_date', p.death_date,
        'country', p.country,
        'occupations', json(p.occupations)
    ) AS payload
FROM persons p;


-- Insert into entities view.

CREATE TRIGGER entities_insert_invalid
INSTEAD OF INSERT ON entities
WHEN NEW.type IS NOT 'book' AND NEW.type IS NOT 'person'
BEGIN
    SELECT RAISE(ABORT, 'unknown entities.type');
END;


CREATE TRIGGER entities_insert_book
INSTEAD OF INSERT ON entities
WHEN NEW.type = 'book'
BEGIN
    SELECT RAISE(ABORT, 'entities.payload must be valid JSON')
    WHERE NOT json_valid(NEW.payload);

    SELECT RAISE(ABORT, 'entities.payload must be a JSON object')
    WHERE json_type(NEW.payload) <> 'object';

    INSERT INTO books (
        id,
        slug,
        archived_at,
        title,
        publication_date,
        lang,
        description,
        identifiers,
        extra,
        publishers
    )
    VALUES (
        COALESCE(
            NULLIF(NEW.id, ''),
            NULLIF(json_extract(NEW.payload, '$.id'), ''),
            (SELECT id FROM new_uuid)
        ),
        COALESCE(
            NULLIF(NEW.slug, ''),
            NULLIF(json_extract(NEW.payload, '$.slug'), ''),
            ''
        ),
        COALESCE(NEW.archived_at, ''),
        json_extract(NEW.payload, '$.title'),
        json_extract(NEW.payload, '$.publication_date'),
        json_extract(NEW.payload, '$.lang'),
        json_extract(NEW.payload, '$.description'),
        json(COALESCE(json_extract(NEW.payload, '$.identifiers'), '[]')),
        json(COALESCE(json_extract(NEW.payload, '$.extra'), '{}')),
        json(COALESCE(json_extract(NEW.payload, '$.publishers'), '[]'))
    );

    UPDATE books
    SET slug = id
    WHERE rowid = last_insert_rowid() AND slug = '';

    DELETE FROM book_authors
    WHERE book_id = (
        SELECT id
        FROM books
        WHERE rowid = last_insert_rowid()
    );

    INSERT INTO book_authors (book_id, person_id)
    SELECT
        (
            SELECT id
            FROM books
            WHERE rowid = last_insert_rowid()
        ),
        j.value
    FROM json_each(COALESCE(json_extract(NEW.payload, '$.author_ids'), '[]')) AS j
    WHERE j.type = 'text' AND j.value <> ''
    ON CONFLICT(book_id, person_id) DO NOTHING;
END;


CREATE TRIGGER entities_insert_person
INSTEAD OF INSERT ON entities
WHEN NEW.type = 'person'
BEGIN
    SELECT RAISE(ABORT, 'entities.payload must be valid JSON')
    WHERE NOT json_valid(NEW.payload);

    SELECT RAISE(ABORT, 'entities.payload must be a JSON object')
    WHERE json_type(NEW.payload) <> 'object';

    INSERT INTO persons (
        id,
        slug,
        archived_at,
        name,
        gender,
        birth_date,
        death_date,
        country,
        identifiers,
        extra,
        occupations
    )
    VALUES (
        COALESCE(
            NULLIF(NEW.id, ''),
            NULLIF(json_extract(NEW.payload, '$.id'), ''),
            (SELECT id FROM new_uuid)
        ),
        COALESCE(
            NULLIF(NEW.slug, ''),
            NULLIF(json_extract(NEW.payload, '$.slug'), ''),
            ''
        ),
        COALESCE(NEW.archived_at, ''),
        json_extract(NEW.payload, '$.name'),
        json_extract(NEW.payload, '$.gender'),
        json_extract(NEW.payload, '$.birth_date'),
        json_extract(NEW.payload, '$.death_date'),
        json_extract(NEW.payload, '$.country'),
        json(COALESCE(json_extract(NEW.payload, '$.identifiers'), '[]')),
        json(COALESCE(json_extract(NEW.payload, '$.extra'), '{}')),
        json(COALESCE(json_extract(NEW.payload, '$.occupations'), '[]'))
    );

    UPDATE persons
    SET slug = id
    WHERE rowid = last_insert_rowid() AND slug = '';
END;


-- Update entities view.

CREATE TRIGGER entities_update_invalid
INSTEAD OF UPDATE ON entities
WHEN OLD.id IS NOT NEW.id
    OR OLD.type IS NOT NEW.type
    OR (NEW.type IS NOT 'book' AND NEW.type IS NOT 'person')
BEGIN
    SELECT RAISE(ABORT, 'entities.id and entities.type are immutable');
END;


CREATE TRIGGER entities_update_book
INSTEAD OF UPDATE ON entities
WHEN OLD.type = 'book' AND NEW.type = 'book'
BEGIN
    SELECT RAISE(ABORT, 'entities.payload must be valid JSON')
    WHERE NOT json_valid(NEW.payload);

    SELECT RAISE(ABORT, 'entities.payload must be a JSON object')
    WHERE json_type(NEW.payload) <> 'object';

    UPDATE books
    SET
        slug = COALESCE(
            NULLIF(NEW.slug, ''),
            NULLIF(json_extract(NEW.payload, '$.slug'), ''),
            NULLIF(OLD.slug, ''),
            OLD.id
        ),
        archived_at = COALESCE(NEW.archived_at, OLD.archived_at, ''),
        title = json_extract(NEW.payload, '$.title'),
        publication_date = json_extract(NEW.payload, '$.publication_date'),
        lang = json_extract(NEW.payload, '$.lang'),
        description = json_extract(NEW.payload, '$.description'),
        identifiers = json(COALESCE(json_extract(NEW.payload, '$.identifiers'), '[]')),
        extra = json(COALESCE(json_extract(NEW.payload, '$.extra'), '{}')),
        publishers = json(COALESCE(json_extract(NEW.payload, '$.publishers'), '[]')),
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = OLD.id;

    DELETE FROM book_authors
    WHERE book_id = OLD.id;

    INSERT INTO book_authors (book_id, person_id)
    SELECT OLD.id, j.value
    FROM json_each(COALESCE(json_extract(NEW.payload, '$.author_ids'), '[]')) AS j
    WHERE j.type = 'text' AND j.value <> ''
    ON CONFLICT(book_id, person_id) DO NOTHING;
END;


CREATE TRIGGER entities_update_person
INSTEAD OF UPDATE ON entities
WHEN OLD.type = 'person' AND NEW.type = 'person'
BEGIN
    SELECT RAISE(ABORT, 'entities.payload must be valid JSON')
    WHERE NOT json_valid(NEW.payload);

    SELECT RAISE(ABORT, 'entities.payload must be a JSON object')
    WHERE json_type(NEW.payload) <> 'object';

    UPDATE persons
    SET
        slug = COALESCE(
            NULLIF(NEW.slug, ''),
            NULLIF(json_extract(NEW.payload, '$.slug'), ''),
            NULLIF(OLD.slug, ''),
            OLD.id
        ),
        archived_at = COALESCE(NEW.archived_at, OLD.archived_at, ''),
        name = json_extract(NEW.payload, '$.name'),
        gender = json_extract(NEW.payload, '$.gender'),
        birth_date = json_extract(NEW.payload, '$.birth_date'),
        death_date = json_extract(NEW.payload, '$.death_date'),
        country = json_extract(NEW.payload, '$.country'),
        identifiers = json(COALESCE(json_extract(NEW.payload, '$.identifiers'), '[]')),
        extra = json(COALESCE(json_extract(NEW.payload, '$.extra'), '{}')),
        occupations = json(COALESCE(json_extract(NEW.payload, '$.occupations'), '[]')),
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = OLD.id;
END;


-- Delete from entities view.
-- Normal Python code should prefer archive, but hard delete is available.

CREATE TRIGGER entities_delete_invalid
INSTEAD OF DELETE ON entities
WHEN OLD.type IS NOT 'book' AND OLD.type IS NOT 'person'
BEGIN
    SELECT RAISE(ABORT, 'unknown entities.type');
END;


CREATE TRIGGER entities_delete_book
INSTEAD OF DELETE ON entities
WHEN OLD.type = 'book'
BEGIN
    DELETE FROM books
    WHERE id = OLD.id;
END;


CREATE TRIGGER entities_delete_person
INSTEAD OF DELETE ON entities
WHEN OLD.type = 'person'
BEGIN
    DELETE FROM persons
    WHERE id = OLD.id;
END;
