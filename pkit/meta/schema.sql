-- schema.sql

CREATE TABLE IF NOT EXISTS sync_control (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    depth INTEGER NOT NULL DEFAULT 0
);

INSERT OR IGNORE INTO sync_control(id, depth) VALUES (1, 0);

-- Reset in case something manually left it dirty.
UPDATE sync_control
SET depth = 0
WHERE id = 1;


CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY CHECK (id <> ''),
    type TEXT NOT NULL,
    slug TEXT NOT NULL,
    payload TEXT NOT NULL CHECK (json_valid(payload) AND json_type(payload) = 'object'),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (type, slug)
);


CREATE TABLE IF NOT EXISTS books (
    id TEXT PRIMARY KEY CHECK (id <> ''),
    slug TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    publication_date TEXT NOT NULL,
    lang TEXT NOT NULL,
    description TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS persons (
    id TEXT PRIMARY KEY CHECK (id <> ''),
    slug TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    gender TEXT NOT NULL,
    birth_date TEXT NOT NULL,
    death_date TEXT NOT NULL,
    country TEXT NOT NULL
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


CREATE INDEX IF NOT EXISTS idx_entities_type
    ON entities(type);

CREATE INDEX IF NOT EXISTS idx_book_authors_person
    ON book_authors(person_id);


DROP TRIGGER IF EXISTS entities_keep_id;
DROP TRIGGER IF EXISTS entities_keep_type;

DROP TRIGGER IF EXISTS books_keep_id;
DROP TRIGGER IF EXISTS persons_keep_id;

DROP TRIGGER IF EXISTS entities_insert_book;
DROP TRIGGER IF EXISTS entities_update_book;

DROP TRIGGER IF EXISTS entities_insert_person;
DROP TRIGGER IF EXISTS entities_update_person;

DROP TRIGGER IF EXISTS entities_delete_sync;

DROP TRIGGER IF EXISTS books_insert_sync;
DROP TRIGGER IF EXISTS books_update_sync;
DROP TRIGGER IF EXISTS books_delete_sync;

DROP TRIGGER IF EXISTS persons_insert_sync;
DROP TRIGGER IF EXISTS persons_update_sync;
DROP TRIGGER IF EXISTS persons_delete_sync;

DROP TRIGGER IF EXISTS book_authors_insert_sync;
DROP TRIGGER IF EXISTS book_authors_update_sync;
DROP TRIGGER IF EXISTS book_authors_delete_sync;


-- Immutable keys.

CREATE TRIGGER entities_keep_id
BEFORE UPDATE ON entities
WHEN old.id IS NOT new.id
BEGIN
    SELECT RAISE(ABORT, 'entities.id is immutable');
END;


CREATE TRIGGER entities_keep_type
BEFORE UPDATE ON entities
WHEN old.type IS NOT new.type
BEGIN
    SELECT RAISE(ABORT, 'entities.type is immutable');
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


-- Entity -> Book

CREATE TRIGGER entities_insert_book
AFTER INSERT ON entities
WHEN new.type = 'book' AND (SELECT depth FROM sync_control) = 0
BEGIN
    UPDATE sync_control
    SET depth = depth + 1
    WHERE id = 1;

    INSERT INTO books (
        id,
        slug,
        title,
        publication_date,
        lang,
        description
    )
    VALUES (
        new.id,
        COALESCE(NULLIF(new.slug, ''), new.id),
        json_extract(new.payload, '$.title'),
        json_extract(new.payload, '$.publication_date'),
        json_extract(new.payload, '$.lang'),
        json_extract(new.payload, '$.description')
    )
    ON CONFLICT(id) DO UPDATE SET
        slug = excluded.slug,
        title = excluded.title,
        publication_date = excluded.publication_date,
        lang = excluded.lang,
        description = excluded.description;

    DELETE FROM book_authors
    WHERE book_id = new.id;

    INSERT INTO book_authors (book_id, person_id)
    SELECT new.id, j.value
    FROM json_each(COALESCE(json_extract(new.payload, '$.author_ids'), '[]')) AS j
    WHERE j.type = 'text' AND j.value <> ''
    ON CONFLICT(book_id, person_id) DO NOTHING;

    UPDATE sync_control
    SET depth = MAX(depth - 1, 0)
    WHERE id = 1;
END;


CREATE TRIGGER entities_update_book
AFTER UPDATE ON entities
WHEN new.type = 'book' AND (SELECT depth FROM sync_control) = 0
BEGIN
    UPDATE sync_control
    SET depth = depth + 1
    WHERE id = 1;

    INSERT INTO books (
        id,
        slug,
        title,
        publication_date,
        lang,
        description
    )
    VALUES (
        new.id,
        COALESCE(NULLIF(new.slug, ''), new.id),
        json_extract(new.payload, '$.title'),
        json_extract(new.payload, '$.publication_date'),
        json_extract(new.payload, '$.lang'),
        json_extract(new.payload, '$.description')
    )
    ON CONFLICT(id) DO UPDATE SET
        slug = excluded.slug,
        title = excluded.title,
        publication_date = excluded.publication_date,
        lang = excluded.lang,
        description = excluded.description;

    DELETE FROM book_authors
    WHERE book_id = new.id;

    INSERT INTO book_authors (book_id, person_id)
    SELECT new.id, j.value
    FROM json_each(COALESCE(json_extract(new.payload, '$.author_ids'), '[]')) AS j
    WHERE j.type = 'text' AND j.value <> ''
    ON CONFLICT(book_id, person_id) DO NOTHING;

    UPDATE sync_control
    SET depth = MAX(depth - 1, 0)
    WHERE id = 1;
END;


-- Entity -> Person

CREATE TRIGGER entities_insert_person
AFTER INSERT ON entities
WHEN new.type = 'person' AND (SELECT depth FROM sync_control) = 0
BEGIN
    UPDATE sync_control
    SET depth = depth + 1
    WHERE id = 1;

    INSERT INTO persons (
        id,
        slug,
        name,
        gender,
        birth_date,
        death_date,
        country
    )
    VALUES (
        new.id,
        COALESCE(NULLIF(new.slug, ''), new.id),
        json_extract(new.payload, '$.name'),
        json_extract(new.payload, '$.gender'),
        json_extract(new.payload, '$.birth_date'),
        json_extract(new.payload, '$.death_date'),
        json_extract(new.payload, '$.country')
    )
    ON CONFLICT(id) DO UPDATE SET
        slug = excluded.slug,
        name = excluded.name,
        gender = excluded.gender,
        birth_date = excluded.birth_date,
        death_date = excluded.death_date,
        country = excluded.country;

    UPDATE sync_control
    SET depth = MAX(depth - 1, 0)
    WHERE id = 1;
END;


CREATE TRIGGER entities_update_person
AFTER UPDATE ON entities
WHEN new.type = 'person' AND (SELECT depth FROM sync_control) = 0
BEGIN
    UPDATE sync_control
    SET depth = depth + 1
    WHERE id = 1;

    INSERT INTO persons (
        id,
        slug,
        name,
        gender,
        birth_date,
        death_date,
        country
    )
    VALUES (
        new.id,
        COALESCE(NULLIF(new.slug, ''), new.id),
        json_extract(new.payload, '$.name'),
        json_extract(new.payload, '$.gender'),
        json_extract(new.payload, '$.birth_date'),
        json_extract(new.payload, '$.death_date'),
        json_extract(new.payload, '$.country')
    )
    ON CONFLICT(id) DO UPDATE SET
        slug = excluded.slug,
        name = excluded.name,
        gender = excluded.gender,
        birth_date = excluded.birth_date,
        death_date = excluded.death_date,
        country = excluded.country;

    UPDATE sync_control
    SET depth = MAX(depth - 1, 0)
    WHERE id = 1;
END;


-- Entity -> delete real rows

CREATE TRIGGER entities_delete_sync
AFTER DELETE ON entities
WHEN (SELECT depth FROM sync_control) = 0
BEGIN
    UPDATE sync_control
    SET depth = depth + 1
    WHERE id = 1;

    DELETE FROM books
    WHERE id = old.id;

    DELETE FROM persons
    WHERE id = old.id;

    UPDATE sync_control
    SET depth = MAX(depth - 1, 0)
    WHERE id = 1;
END;


-- Book -> Entity

CREATE TRIGGER books_insert_sync
AFTER INSERT ON books
WHEN (SELECT depth FROM sync_control) = 0
BEGIN
    UPDATE sync_control
    SET depth = depth + 1
    WHERE id = 1;

    UPDATE books
    SET slug = new.id
    WHERE id = new.id AND slug = '';

    INSERT INTO entities (
        id,
        type,
        slug,
        payload
    )
    VALUES (
        new.id,
        'book',
        COALESCE(NULLIF((SELECT slug FROM books WHERE id = new.id), ''), new.id),
        json_object(
            'id', new.id,
            'type', 'book',
            'slug', COALESCE(NULLIF((SELECT slug FROM books WHERE id = new.id), ''), new.id),
            'identifiers', json_array(),
            'extra', json_object(),
            'title', new.title,
            'publication_date', new.publication_date,
            'lang', new.lang,
            'description', new.description,
            'author_ids', json(COALESCE(
                (
                    SELECT json_group_array(person_id)
                    FROM (
                        SELECT person_id
                        FROM book_authors
                        WHERE book_id = new.id
                        ORDER BY rowid
                    )
                ),
                '[]'
            )),
            'publishers', json_array()
        )
    )
    ON CONFLICT(id) DO UPDATE SET
        type = 'book',
        slug = excluded.slug,
        payload = json_set(
            COALESCE(
                entities.payload,
                json_object(
                    'id', new.id,
                    'type', 'book',
                    'slug', excluded.slug,
                    'identifiers', json_array(),
                    'extra', json_object(),
                    'title', new.title,
                    'publication_date', new.publication_date,
                    'lang', new.lang,
                    'description', new.description,
                    'author_ids', json_array(),
                    'publishers', json_array()
                )
            ),
            '$.id', new.id,
            '$.type', 'book',
            '$.slug', excluded.slug,
            '$.title', new.title,
            '$.publication_date', new.publication_date,
            '$.lang', new.lang,
            '$.description', new.description,
            '$.identifiers', json(COALESCE(json_extract(entities.payload, '$.identifiers'), '[]')),
            '$.extra', json(COALESCE(json_extract(entities.payload, '$.extra'), '{}')),
            '$.author_ids', json(COALESCE(
                (
                    SELECT json_group_array(person_id)
                    FROM (
                        SELECT person_id
                        FROM book_authors
                        WHERE book_id = new.id
                        ORDER BY rowid
                    )
                ),
                '[]'
            )),
            '$.publishers', json(COALESCE(json_extract(entities.payload, '$.publishers'), '[]'))
        ),
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now');

    UPDATE sync_control
    SET depth = MAX(depth - 1, 0)
    WHERE id = 1;
END;


CREATE TRIGGER books_update_sync
AFTER UPDATE ON books
WHEN (SELECT depth FROM sync_control) = 0
BEGIN
    UPDATE sync_control
    SET depth = depth + 1
    WHERE id = 1;

    UPDATE books
    SET slug = COALESCE(NULLIF(old.slug, ''), new.id)
    WHERE id = new.id AND new.slug = '';

    INSERT INTO entities (
        id,
        type,
        slug,
        payload
    )
    VALUES (
        new.id,
        'book',
        COALESCE(NULLIF((SELECT slug FROM books WHERE id = new.id), ''), new.id),
        json_object(
            'id', new.id,
            'type', 'book',
            'slug', COALESCE(NULLIF((SELECT slug FROM books WHERE id = new.id), ''), new.id),
            'identifiers', json_array(),
            'extra', json_object(),
            'title', new.title,
            'publication_date', new.publication_date,
            'lang', new.lang,
            'description', new.description,
            'author_ids', json(COALESCE(
                (
                    SELECT json_group_array(person_id)
                    FROM (
                        SELECT person_id
                        FROM book_authors
                        WHERE book_id = new.id
                        ORDER BY rowid
                    )
                ),
                '[]'
            )),
            'publishers', json_array()
        )
    )
    ON CONFLICT(id) DO UPDATE SET
        type = 'book',
        slug = excluded.slug,
        payload = json_set(
            COALESCE(
                entities.payload,
                json_object(
                    'id', new.id,
                    'type', 'book',
                    'slug', excluded.slug,
                    'identifiers', json_array(),
                    'extra', json_object(),
                    'title', new.title,
                    'publication_date', new.publication_date,
                    'lang', new.lang,
                    'description', new.description,
                    'author_ids', json_array(),
                    'publishers', json_array()
                )
            ),
            '$.id', new.id,
            '$.type', 'book',
            '$.slug', excluded.slug,
            '$.title', new.title,
            '$.publication_date', new.publication_date,
            '$.lang', new.lang,
            '$.description', new.description,
            '$.identifiers', json(COALESCE(json_extract(entities.payload, '$.identifiers'), '[]')),
            '$.extra', json(COALESCE(json_extract(entities.payload, '$.extra'), '{}')),
            '$.author_ids', json(COALESCE(
                (
                    SELECT json_group_array(person_id)
                    FROM (
                        SELECT person_id
                        FROM book_authors
                        WHERE book_id = new.id
                        ORDER BY rowid
                    )
                ),
                '[]'
            )),
            '$.publishers', json(COALESCE(json_extract(entities.payload, '$.publishers'), '[]'))
        ),
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now');

    UPDATE sync_control
    SET depth = MAX(depth - 1, 0)
    WHERE id = 1;
END;


CREATE TRIGGER books_delete_sync
AFTER DELETE ON books
WHEN (SELECT depth FROM sync_control) = 0
BEGIN
    UPDATE sync_control
    SET depth = depth + 1
    WHERE id = 1;

    DELETE FROM entities
    WHERE id = old.id AND type = 'book';

    UPDATE sync_control
    SET depth = MAX(depth - 1, 0)
    WHERE id = 1;
END;


-- Person -> Entity

CREATE TRIGGER persons_insert_sync
AFTER INSERT ON persons
WHEN (SELECT depth FROM sync_control) = 0
BEGIN
    UPDATE sync_control
    SET depth = depth + 1
    WHERE id = 1;

    UPDATE persons
    SET slug = new.id
    WHERE id = new.id AND slug = '';

    INSERT INTO entities (
        id,
        type,
        slug,
        payload
    )
    VALUES (
        new.id,
        'person',
        COALESCE(NULLIF((SELECT slug FROM persons WHERE id = new.id), ''), new.id),
        json_object(
            'id', new.id,
            'type', 'person',
            'slug', COALESCE(NULLIF((SELECT slug FROM persons WHERE id = new.id), ''), new.id),
            'identifiers', json_array(),
            'extra', json_object(),
            'name', new.name,
            'gender', new.gender,
            'birth_date', new.birth_date,
            'death_date', new.death_date,
            'country', new.country,
            'occupations', json_array()
        )
    )
    ON CONFLICT(id) DO UPDATE SET
        type = 'person',
        slug = excluded.slug,
        payload = json_set(
            COALESCE(
                entities.payload,
                json_object(
                    'id', new.id,
                    'type', 'person',
                    'slug', excluded.slug,
                    'identifiers', json_array(),
                    'extra', json_object(),
                    'name', new.name,
                    'gender', new.gender,
                    'birth_date', new.birth_date,
                    'death_date', new.death_date,
                    'country', new.country,
                    'occupations', json_array()
                )
            ),
            '$.id', new.id,
            '$.type', 'person',
            '$.slug', excluded.slug,
            '$.name', new.name,
            '$.gender', new.gender,
            '$.birth_date', new.birth_date,
            '$.death_date', new.death_date,
            '$.country', new.country,
            '$.identifiers', json(COALESCE(json_extract(entities.payload, '$.identifiers'), '[]')),
            '$.extra', json(COALESCE(json_extract(entities.payload, '$.extra'), '{}')),
            '$.occupations', json(COALESCE(json_extract(entities.payload, '$.occupations'), '[]'))
        ),
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now');

    UPDATE sync_control
    SET depth = MAX(depth - 1, 0)
    WHERE id = 1;
END;


CREATE TRIGGER persons_update_sync
AFTER UPDATE ON persons
WHEN (SELECT depth FROM sync_control) = 0
BEGIN
    UPDATE sync_control
    SET depth = depth + 1
    WHERE id = 1;

    UPDATE persons
    SET slug = COALESCE(NULLIF(old.slug, ''), new.id)
    WHERE id = new.id AND new.slug = '';

    INSERT INTO entities (
        id,
        type,
        slug,
        payload
    )
    VALUES (
        new.id,
        'person',
        COALESCE(NULLIF((SELECT slug FROM persons WHERE id = new.id), ''), new.id),
        json_object(
            'id', new.id,
            'type', 'person',
            'slug', COALESCE(NULLIF((SELECT slug FROM persons WHERE id = new.id), ''), new.id),
            'identifiers', json_array(),
            'extra', json_object(),
            'name', new.name,
            'gender', new.gender,
            'birth_date', new.birth_date,
            'death_date', new.death_date,
            'country', new.country,
            'occupations', json_array()
        )
    )
    ON CONFLICT(id) DO UPDATE SET
        type = 'person',
        slug = excluded.slug,
        payload = json_set(
            COALESCE(
                entities.payload,
                json_object(
                    'id', new.id,
                    'type', 'person',
                    'slug', excluded.slug,
                    'identifiers', json_array(),
                    'extra', json_object(),
                    'name', new.name,
                    'gender', new.gender,
                    'birth_date', new.birth_date,
                    'death_date', new.death_date,
                    'country', new.country,
                    'occupations', json_array()
                )
            ),
            '$.id', new.id,
            '$.type', 'person',
            '$.slug', excluded.slug,
            '$.name', new.name,
            '$.gender', new.gender,
            '$.birth_date', new.birth_date,
            '$.death_date', new.death_date,
            '$.country', new.country,
            '$.identifiers', json(COALESCE(json_extract(entities.payload, '$.identifiers'), '[]')),
            '$.extra', json(COALESCE(json_extract(entities.payload, '$.extra'), '{}')),
            '$.occupations', json(COALESCE(json_extract(entities.payload, '$.occupations'), '[]'))
        ),
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now');

    UPDATE sync_control
    SET depth = MAX(depth - 1, 0)
    WHERE id = 1;
END;


CREATE TRIGGER persons_delete_sync
AFTER DELETE ON persons
WHEN (SELECT depth FROM sync_control) = 0
BEGIN
    UPDATE sync_control
    SET depth = depth + 1
    WHERE id = 1;

    DELETE FROM entities
    WHERE id = old.id AND type = 'person';

    UPDATE sync_control
    SET depth = MAX(depth - 1, 0)
    WHERE id = 1;
END;


-- book_authors -> Entity.payload.author_ids

CREATE TRIGGER book_authors_insert_sync
AFTER INSERT ON book_authors
WHEN (SELECT depth FROM sync_control) = 0
BEGIN
    UPDATE sync_control
    SET depth = depth + 1
    WHERE id = 1;

    UPDATE entities
    SET
        payload = json_set(
            payload,
            '$.author_ids',
            json(COALESCE(
                (
                    SELECT json_group_array(person_id)
                    FROM (
                        SELECT person_id
                        FROM book_authors
                        WHERE book_id = new.book_id
                        ORDER BY rowid
                    )
                ),
                '[]'
            ))
        ),
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = new.book_id AND type = 'book';

    UPDATE sync_control
    SET depth = MAX(depth - 1, 0)
    WHERE id = 1;
END;


CREATE TRIGGER book_authors_update_sync
AFTER UPDATE ON book_authors
WHEN (SELECT depth FROM sync_control) = 0
BEGIN
    UPDATE sync_control
    SET depth = depth + 1
    WHERE id = 1;

    UPDATE entities
    SET
        payload = json_set(
            payload,
            '$.author_ids',
            json(COALESCE(
                (
                    SELECT json_group_array(person_id)
                    FROM (
                        SELECT person_id
                        FROM book_authors
                        WHERE book_id = old.book_id
                        ORDER BY rowid
                    )
                ),
                '[]'
            ))
        ),
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = old.book_id AND type = 'book';

    UPDATE entities
    SET
        payload = json_set(
            payload,
            '$.author_ids',
            json(COALESCE(
                (
                    SELECT json_group_array(person_id)
                    FROM (
                        SELECT person_id
                        FROM book_authors
                        WHERE book_id = new.book_id
                        ORDER BY rowid
                    )
                ),
                '[]'
            ))
        ),
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = new.book_id AND type = 'book';

    UPDATE sync_control
    SET depth = MAX(depth - 1, 0)
    WHERE id = 1;
END;


CREATE TRIGGER book_authors_delete_sync
AFTER DELETE ON book_authors
WHEN (SELECT depth FROM sync_control) = 0
BEGIN
    UPDATE sync_control
    SET depth = depth + 1
    WHERE id = 1;

    UPDATE entities
    SET
        payload = json_set(
            payload,
            '$.author_ids',
            json(COALESCE(
                (
                    SELECT json_group_array(person_id)
                    FROM (
                        SELECT person_id
                        FROM book_authors
                        WHERE book_id = old.book_id
                        ORDER BY rowid
                    )
                ),
                '[]'
            ))
        ),
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE id = old.book_id AND type = 'book';

    UPDATE sync_control
    SET depth = MAX(depth - 1, 0)
    WHERE id = 1;
END;
