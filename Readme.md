# ConspectAI — AI Study Platform for STEM

**Upload your documents. Ask questions. Get answers grounded in your own materials.**

ConspectAI is an AI-powered study platform that turns uploaded documents into a searchable, interactive knowledge base. It uses a Retrieval-Augmented Generation (RAG) pipeline to answer questions using the user's own materials, and automatically generates mind maps to visualize concepts.

Fully containerized stack: **PostgreSQL + FastAPI + Caddy** with automatic HTTPS.

![Landing page](./static/docs/main.png)

---

## Features

- **RAG-based Q&A** — Ask questions in natural language and get answers grounded in your uploaded documents, not generic model output.
- **Semantic search over your files** — Documents are chunked and embedded; only the most relevant context is passed to the LLM for accurate, focused answers.
- **Automatic mind-map generation** — Background jobs turn document content into visual concept maps.
- **User accounts & chat history** — JWT authentication with persistent chats, messages, and files per user.
- **Content-addressed file storage** — Uploaded files are deduplicated via SHA-256 hashing and gzip-compressed to save space.
- **Admin panel & analytics** — Usage tracking and administration tools.
- **Subscription & billing** — Built-in billing module for paid plans.
- **Production-ready deployment** — One-command Docker deploy with automatic TLS via Let's Encrypt.


![Chat interface](./static/docs/chat.png)


---

## Tech Stack

| Layer          | Technology                                                        |
| -------------- | ----------------------------------------------------------------- |
| **Backend**    | FastAPI, Uvicorn (async, multi-worker)                            |
| **Database**   | PostgreSQL 16, asyncpg                                            |
| **AI / RAG**   | LLM API integration, vector embeddings, semantic retrieval        |
| **Auth**       | JWT (access + refresh)                                            |
| **Storage**    | Content-addressed file store (SHA-256 dedup + gzip)              |
| **Proxy / TLS**| Caddy — automatic HTTPS, gzip/zstd compression, security headers  |
| **Infra**      | Docker & Docker Compose (dev + prod overlays)                     |

---

## Architecture

```
┌─────────────────────────────────────────────┐
│  Caddy (prod overlay)                        │
│  • TLS termination via Let's Encrypt         │
│  • gzip/zstd compression                     │
│  • Security headers                          │
│  Listens: 80, 443                            │
└──────────────┬───────────────────────────────┘
               │ proxy → app:8000
┌──────────────▼───────────────────────────────┐
│  app (FastAPI + Uvicorn, 2 workers)          │
│  • REST API + JWT auth                        │
│  • RAG pipeline (embeddings + retrieval)      │
│  • LLM integration                            │
│  • Background mind-map generation             │
│  Volume: uploads_data → /app/uploads          │
│         (content-addressed file storage)      │
└──────────────┬───────────────────────────────┘
               │ asyncpg
┌──────────────▼───────────────────────────────┐
│  db (PostgreSQL 16)                           │
│  • Users, chats, messages, files, mindmaps    │
│  Volume: postgres_data                        │
│  NOT exposed on host network                  │
└───────────────────────────────────────────────┘
```

### How the RAG pipeline works

1. A user uploads a document.
2. The file is deduplicated (SHA-256), compressed, and stored.
3. Content is split into chunks and converted into vector embeddings.
4. On each question, the system runs a semantic search and selects the **best-matching** context.
5. Only that focused context is passed to the LLM — producing accurate, grounded answers instead of hallucinations.

![Admin panel](./static/docs/admin.png)


---

## Quick Start (local)

**Prerequisites:** [Docker](https://www.docker.com/get-started) & Docker Compose

```bash
# 1. Copy the config and fill in the required variables
cp .env.example .env
# Open .env and set: LLM_API_KEY, SECRET_KEY, POSTGRES_PASSWORD
# Generate SECRET_KEY:  openssl rand -hex 32

# 2. Build and run
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

# 3. Open in your browser
open http://localhost:8000
```

Application logs: `docker compose logs -f app`
Database logs: `docker compose logs -f db`

---

## Configuration

| Variable            | Description                                        |
| ------------------- | -------------------------------------------------- |
| `LLM_API_KEY`       | API key for the language model provider            |
| `SECRET_KEY`        | JWT signing key — generate with `openssl rand -hex 32` |
| `POSTGRES_PASSWORD` | Database password (min. 24 characters in prod)     |

---

## Persistent Volumes

| Volume                | Stores                                       |
| --------------------- | -------------------------------------------- |
| `postgres_data`       | PostgreSQL database                          |
| `uploads_data`        | Uploaded files (SHA-256 dedup + gzip)        |
| `caddy_data`          | Let's Encrypt SSL certificates (prod only)   |
| `caddy_config`        | Caddy internal config (prod only)            |

---

## Security Notes

For production:

1. **Strong `POSTGRES_PASSWORD`** — minimum 24 characters.
2. **`SECRET_KEY` via `openssl rand -hex 32`** — without it, JWT is vulnerable.
3. **Firewall** — expose only ports `80` and `443` (plus `22` for SSH).
4. **Database is not exposed** — `db` is reachable only from the internal Docker network.
5. **Regular backups** — schedule a cron job for `pg_dump`.

---

## Roadmap

- [ ] Subscription system
- [ ] Desmos API integration
- [ ] Anki card export

---

## License

MIT