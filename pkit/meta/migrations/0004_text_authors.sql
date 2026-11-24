-- text_authors: many-to-many links between texts and persons.
-- Intentionally no foreign-key constraints: this allows importing texts
-- before persons exist.

CREATE TABLE IF NOT EXISTS text_authors (
    text_id TEXT NOT NULL CHECK (text_id <> ''),
    person_id TEXT NOT NULL CHECK (person_id <> ''),
    PRIMARY KEY (text_id, person_id)
);

CREATE INDEX IF NOT EXISTS text_authors_person
ON text_authors(person_id);
