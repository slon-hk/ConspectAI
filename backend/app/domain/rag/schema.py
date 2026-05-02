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

-- Chunk metadata for priority scoring (computed at ingestion time)
ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS token_count     INTEGER;
ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS importance_hint FLOAT DEFAULT 0.5;

-- Compressed chat history summaries (one active summary per chat)
CREATE TABLE IF NOT EXISTS chat_history_summaries (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_id         UUID        NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    summary_text    TEXT        NOT NULL,
    covers_up_to_id UUID        NOT NULL,  -- last message_id included in summary
    message_count   INTEGER     NOT NULL,
    token_count     INTEGER     NOT NULL,
    model_used      TEXT        NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS chat_history_summaries_chat_idx
    ON chat_history_summaries(chat_id);

-- Per-query pipeline traces for cost/latency dashboard
CREATE TABLE IF NOT EXISTS rag_pipeline_traces (
    id                    BIGSERIAL   PRIMARY KEY,
    user_id               INTEGER     NOT NULL,
    chat_id               UUID,
    course_id             UUID,
    model_tier            TEXT,
    history_tokens_raw    INTEGER,
    history_tokens_used   INTEGER,
    context_tokens        INTEGER,
    output_tokens         INTEGER,
    total_tokens          INTEGER,
    l1_hit                BOOLEAN     DEFAULT FALSE,
    l2_hit                BOOLEAN     DEFAULT FALSE,
    l3_hit                BOOLEAN     DEFAULT FALSE,
    retrieval_cache_hit   BOOLEAN     DEFAULT FALSE,
    latency_embed_ms      INTEGER,
    latency_retrieve_ms   INTEGER,
    latency_rerank_ms     INTEGER,
    latency_context_ms    INTEGER,
    latency_llm_ms        INTEGER,
    latency_total_ms      INTEGER,
    chunks_retrieved      INTEGER,
    chunks_used           INTEGER,
    chunks_compressed     INTEGER,
    context_reduction_pct FLOAT,
    cost_usd              FLOAT,
    created_at            TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS rag_pipeline_traces_user_created
    ON rag_pipeline_traces(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS rag_pipeline_traces_created
    ON rag_pipeline_traces(created_at DESC);

-- Backfill token_count for existing chunks
UPDATE rag_chunks SET token_count = length(content) / 4 WHERE token_count IS NULL;

-- Global Knowledge Base: allow course_id to be NULL for public documents
-- that don't belong to any specific course.
ALTER TABLE rag_documents ALTER COLUMN course_id DROP NOT NULL;
ALTER TABLE rag_documents DROP CONSTRAINT IF EXISTS rag_documents_course_id_fkey;
ALTER TABLE rag_documents ADD CONSTRAINT rag_documents_course_id_fkey
    FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE SET NULL;
"""
