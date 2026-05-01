"""RAG database schema DDL."""

RAG_SCHEMA = """
-- pgvector extension (must be enabled once per DB)
CREATE EXTENSION IF NOT EXISTS vector;

-- Courses: user-created knowledge collections
CREATE TABLE IF NOT EXISTS courses (
    id          UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    scope       TEXT    NOT NULL DEFAULT 'private',  -- 'private' | 'public'
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS courses_user_idx ON courses(user_id);

-- Source documents tracked per course
CREATE TABLE IF NOT EXISTS rag_documents (
    id          UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id   UUID    NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename    TEXT    NOT NULL,
    source_type TEXT    NOT NULL,  -- 'pdf' | 'txt' | 'md' | 'docx' | 'youtube' | 'url'
    source_ref  TEXT    NOT NULL,  -- original path or URL
    sha256      TEXT    NOT NULL,  -- hash of raw source for dedup at doc level
    status      TEXT    NOT NULL DEFAULT 'pending',  -- pending|indexing|ready|error
    error_msg   TEXT,
    chunk_count INTEGER DEFAULT 0,
    is_public   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS rag_doc_course_idx ON rag_documents(course_id);
CREATE INDEX IF NOT EXISTS rag_doc_sha256_idx ON rag_documents(sha256);
CREATE INDEX IF NOT EXISTS rag_doc_public_idx ON rag_documents(is_public, status, created_at DESC);

ALTER TABLE rag_documents ADD COLUMN IF NOT EXISTS is_public BOOLEAN NOT NULL DEFAULT FALSE;

-- Text chunks with embeddings (shared across users via content_hash)
CREATE TABLE IF NOT EXISTS rag_chunks (
    id           UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id  UUID    REFERENCES rag_documents(id) ON DELETE CASCADE,
    content      TEXT    NOT NULL,
    content_hash TEXT    NOT NULL,       -- SHA-256 of content: dedup key
    embedding    vector(1536),            -- Reduced embedding dimension to 1536 due to pgvector index limit
    tsv          tsvector,               -- for BM25 full-text search
    chunk_index  INTEGER NOT NULL,       -- position within document
    char_start   INTEGER,                -- character offset in source
    char_end     INTEGER,
    source_count INTEGER NOT NULL DEFAULT 1,  -- how many docs reference this content
    created_at   TIMESTAMPTZ DEFAULT now()
);
-- Unique on content so duplicates are merged, not inserted
CREATE UNIQUE INDEX IF NOT EXISTS rag_chunks_hash_idx ON rag_chunks(content_hash);
CREATE INDEX IF NOT EXISTS rag_chunks_doc_idx   ON rag_chunks(document_id);
CREATE INDEX IF NOT EXISTS rag_chunks_tsv_idx   ON rag_chunks USING GIN(tsv);
CREATE INDEX IF NOT EXISTS rag_chunks_emb_idx   ON rag_chunks
    USING hnsw (embedding vector_cosine_ops);

-- Images extracted from documents
CREATE TABLE IF NOT EXISTS rag_images (
    id          UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID    NOT NULL REFERENCES rag_documents(id) ON DELETE CASCADE,
    sha256      TEXT    NOT NULL UNIQUE,  -- dedup raw image bytes
    file_path   TEXT    NOT NULL,
    mime_type   TEXT    NOT NULL DEFAULT 'image/png',
    caption     TEXT    NOT NULL DEFAULT '',
    embedding   vector(1536),
    page_num    INTEGER,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS rag_img_doc_idx  ON rag_images(document_id);
CREATE INDEX IF NOT EXISTS rag_img_hash_idx ON rag_images(sha256);

-- Many-to-many: which images belong to which chunk (by proximity in document)
CREATE TABLE IF NOT EXISTS rag_chunk_images (
    chunk_id  UUID REFERENCES rag_chunks(id) ON DELETE CASCADE,
    image_id  UUID REFERENCES rag_images(id) ON DELETE CASCADE,
    PRIMARY KEY (chunk_id, image_id)
);

-- Embedding cache: avoid re-calling Gemini embed for seen query strings
CREATE TABLE IF NOT EXISTS rag_query_cache (
    query_hash TEXT    PRIMARY KEY,  -- SHA-256 of query text
    embedding  vector(1536),
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Answer cache: same query against same context → instant return
CREATE TABLE IF NOT EXISTS rag_answer_cache (
    cache_key   TEXT PRIMARY KEY,   -- SHA-256 of (query_hash + sorted chunk ids)
    answer      TEXT NOT NULL,
    image_ids   TEXT NOT NULL DEFAULT '[]',  -- JSON array of rag_image UUIDs
    hit_count   INTEGER NOT NULL DEFAULT 1,
    created_at  TIMESTAMPTZ DEFAULT now(),
    last_used   TIMESTAMPTZ DEFAULT now()
);

-- Chats can optionally be linked to a course
ALTER TABLE chats ADD COLUMN IF NOT EXISTS course_id UUID REFERENCES courses(id) ON DELETE SET NULL;
"""
