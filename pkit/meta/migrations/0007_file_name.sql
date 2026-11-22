-- name: the tree name add first recorded for these bytes (first add wins).

ALTER TABLE files ADD COLUMN name TEXT NOT NULL DEFAULT '';
