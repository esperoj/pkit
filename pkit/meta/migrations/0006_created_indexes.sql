-- created_indexes.sql: created_at indexes so find(sort="created"/"-created") stays cheap.

CREATE INDEX IF NOT EXISTS files_created ON files(created_at);
CREATE INDEX IF NOT EXISTS persons_created ON persons(created_at);
CREATE INDEX IF NOT EXISTS texts_created ON texts(created_at);
CREATE INDEX IF NOT EXISTS software_created ON software(created_at);
CREATE INDEX IF NOT EXISTS videos_created ON videos(created_at);
CREATE INDEX IF NOT EXISTS sounds_created ON sounds(created_at);
CREATE INDEX IF NOT EXISTS notes_created ON notes(created_at);
