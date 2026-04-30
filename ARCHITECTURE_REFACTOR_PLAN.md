# Clean Architecture Refactor Plan

This document records the current backend shape and the safe migration path
after Stage 1-3. It is intentionally narrow: behavior, public API contracts,
database schema, Docker entrypoint, and billing semantics are unchanged.

## Current Entry Points

- Runtime entrypoint: `uvicorn main:app`.
- Docker copies `backend/` into `/app`, so existing modules import each other
  with flat imports such as `import db`, `import admin`, and `import rag_routes`.
- `main.py` creates the FastAPI app, owns lifespan startup/shutdown, installs
  middleware, includes admin and RAG routers, and defines most public routes.

## DB Access Pattern

- `db.py` remains the compatibility data access module for existing code.
- Pool lifecycle is now owned by `app.db.pool.Database`.
- `db.create_pool()`, `db.close_pool()`, and `db.pool()` delegate to the new DB
  infrastructure and keep existing callers working.
- Schema initialization and most legacy SQL functions still live in `db.py`
  while repository extraction proceeds incrementally.
- User, chat, message, file metadata, mindmap, and admin user-management SQL now
  lives in OLTP repositories under `app.repositories.oltp`; `db.py` keeps
  compatibility wrappers for existing imports and call sites.

## `db.py` Function Map

- Pool/schema: `create_pool`, `close_pool`, `pool`, `init_schema`.
- Users/auth: `create_user`, `get_user_by_email`, `get_user_by_id`,
  `get_user_by_username`.
- Chats/messages: `get_chats`, `create_chat`, `update_chat_settings`,
  `delete_chat`, `get_chat`, `get_messages`, `save_message`.
- Files/storage metadata: `register_file`, `release_file`, `get_file_meta`.
- Mindmaps: `get_mindmap`, `save_mindmap`.
- Usage/quota/billing facts: `check_limits`, `check_and_consume_limit`,
  `get_user_usage_snapshot`, `finalize_request_usage`,
  `fail_and_refund_request`.
- Admin OLTP actions: `list_users`, `count_users`, `admin_set_user_field`,
  `admin_set_user_plan`, `admin_delete_user`.
- OLAP/reporting writes and reads: `get_platform_stats`, `get_recent_activity`,
  `get_model_usage`, `get_admin_metrics`, `insert_rag_metric`,
  `insert_funnel_event`, `log_request_metrics`, `admin_metrics_overview`,
  `admin_metrics_rag`, `admin_metrics_usage`, `admin_metrics_marketing`.

## Raw SQL Outside Repository Packages

- `rag.py`: query cache, document ingestion, retrieval, image resolution,
  course/document helpers, answer cache cleanup.
- `rag_routes.py`: course CRUD, document validation/creation/deletion, image
  lookup, chat-course linking.

## Hot Request Paths

- `POST /api/chats/{chat_id}/messages`: quota reservation, message writes,
  file metadata reads, optional RAG retrieval/ingestion, Gemini call, assistant
  persistence, analytics, request metrics, and RAG metrics.
- `POST /api/courses/{course_id}/ingest`: upload validation, DB writes, file
  extraction, embedding, and ingestion scheduling.
- `POST /api/courses/{course_id}/ingest-url`: URL validation, DB writes, remote
  extraction, embedding, and ingestion scheduling.

## Risky Transaction Areas

- `check_and_consume_limit`: quota reservation and request log creation in one
  transaction.
- `fail_and_refund_request`: usage refund and request status update in one
  transaction.
- `finalize_request_usage` and `log_request_metrics`: request accounting,
  efficiency metrics, daily aggregates, and system metrics.
- `rag.py` ingestion flow: DB transactions are mixed with expensive extraction,
  embedding, and image processing work.

## Migration Rules

- Each following stage should be a small commit after compile/import checks.
- Keep `db.py`, `main.py`, `admin.py`, `rag.py`, `rag_routes.py`, and `billing.py`
  as compatibility wrappers until the replacement path for each function is in
  place and verified.
- New SQL belongs in repositories only. Services may orchestrate repositories,
  transactions, external clients, and events, but must not contain raw SQL.
- Event-driven analytics should be introduced before removing current analytics
  writes from hot request paths.
- Admin reporting SQL now lives behind `app.repositories.olap.AdminReportRepository`,
  even though it still runs on the same Postgres database for now.
- Analytics event SQL now lives behind `app.repositories.olap.AnalyticsEventRepository`;
  `analytics.py` remains the compatibility API for current callers.
- Usage and quota SQL now lives behind `app.repositories.oltp.UsageRepository`;
  `QuotaService` and `UsageService` preserve the current `db.py` wrapper API.
- RAG, funnel, request, user activity, and system metric writes now live behind
  OLAP repositories while existing `db.py` metric functions remain compatible.
- An in-process event bus now provides the first `publish`/`subscribe` boundary;
  `analytics.track()` publishes an analytics event handled by an OLAP handler.
- Route-level RAG SQL for courses, document records, image access, and chat
  course linking now lives behind `app.repositories.oltp.RagRouteRepository`.
  RAG helper SQL for course listing, document listing, course deletion, and
  chat-file auto-course creation uses the same repository.
- RAG query/answer cache SQL and cached image resolution now live behind
  `app.repositories.oltp.RagCacheRepository`.
- RAG hybrid retrieval SQL now lives behind
  `app.repositories.oltp.RagRetrievalRepository`.
- RAG ingestion write SQL for document status, chunk dedupe/upsert, image
  upsert, chunk-image links, and ready finalization now lives behind
  `app.repositories.oltp.RagIngestionRepository`. `rag.py` still owns extraction,
  embedding, captioning, and ingestion orchestration.
- RAG chat-file auto-ingestion now uses `ChatRepository` and `FileRepository`
  directly instead of the legacy `db.py` compatibility wrappers.
- `app.services.RagService` now owns course/document/image/chat-course
  orchestration for the RAG API, including upload/Youtube ingestion setup.
  `rag_routes.py` still owns HTTP parsing, response serialization, file read,
  and HTTP error mapping.
- `rag_routes.py` auth dependency now verifies users through `UserRepository`
  instead of the legacy `db.py` wrapper.
- `app.services.ChatService` now owns chat CRUD and message-list orchestration
  for non-send chat endpoints in `main.py`. The hot `send_message` path remains
  a separate migration block because it combines quota context, file handling,
  Gemini/RAG orchestration, billing, analytics, and mindmap refresh scheduling.
- `send_message` now uses `ChatService` for chat ownership lookup, history
  loading, user/assistant message persistence, and title refresh. The route still
  owns Gemini/RAG orchestration, file attachment preparation, billing math, and
  request-state usage metadata until the next hot-path service extraction.
- `app.services.MindmapService` now owns mindmap access checks, conversation
  digest construction, Gemini mindmap regeneration, and mindmap persistence.
  `main.py` keeps endpoint error mapping, analytics counters, and background
  task scheduling for the mindmap feature.
- `app.services.UserService` now owns the safe user profile payload and usage
  snapshot composition used by auth responses and `/api/user`.
- `current_user_id` in `main.py` now verifies user existence/block status through
  `UserService`; JWT extraction/HTTP error mapping remains in the dependency.
- `app.services.AuthService` now owns register/login validation, uniqueness
  checks, password hashing/verification, token creation, and auth response
  payload composition. `main.py` keeps HTTP status mapping and signup/login
  analytics side effects.
- Usage endpoints in `main.py` now read snapshots through `UsageService`
  instead of the legacy `db.py` wrapper.
- Quota middleware now reserves quota through `QuotaService` and refunds
  failed/blocked requests through `UsageService`; OLAP request/RAG metric writes
  remain a separate service extraction block.
- `app.services.RequestMetricsService` now owns request metric logging and RAG
  metric logging from middleware usage payloads. It now publishes in-process
  metric events so the middleware no longer awaits OLAP writes in the
  user-facing request path. The handlers still persist through OLAP repositories
  on the same Postgres database until durable outbox/batch workers are added.
- `app.services.AdminMetricsService` now owns admin metrics endpoint orchestration
  over `AdminReportRepository`, removing live admin metric endpoints in `main.py`
  and `admin.py` from the legacy `db.py` wrapper path.
- `app.services.FunnelService` now owns landing/signup funnel event writes over
  `FunnelMetricRepository`, removing those OLAP writes in `main.py` from the
  legacy `db.py` wrapper path. It now publishes funnel events in the background
  so landing/register routes no longer wait on OLAP inserts.
- `app.workers` now provides the first background-worker boundary:
  `analytics_worker` owns analytics cleanup task startup, and `worker_app` can
  run that maintenance loop as a standalone process without changing the current
  FastAPI/Docker entrypoint.
- `app.services.FileService` now owns file storage/metadata registration and raw
  file lookup. `main.py` keeps HTTP upload reading, response serving, and upload
  analytics side effects.
- `app.services.ai_chat_service.AiChatService` now owns the core chat turn
  orchestration for `send_message`: file attachment preparation, Gemini/RAG
  branching, assistant persistence, billing usage payload construction, and chat
  title refresh. `main.py` keeps HTTP error mapping, `request.state` assignment,
  and background mindmap scheduling.
- `app.services.billing_service.BillingService` now owns per-turn internal cost
  calculation and request billing usage payload construction. `billing.py`
  remains the legacy pricing function module underneath this service.

## Next Stages

- Stage 4: continue extracting OLTP repositories after users, chats, messages,
  file metadata, mindmaps, and admin user-management; next likely candidate is
  usage/quota.
- Stage 5: introduce auth, user, chat, and message services while preserving
  route behavior.
- Stage 6: move quota reservation, commit, and refund behind `QuotaService` and
  `UsageService`; keep middleware thin.
- Stage 7: move billing calculations and subscription budget decisions behind
  `BillingService`.
- Stage 8-10: add event bus, OLAP repositories, batchable workers, and move
  analytics/RAG metrics/admin aggregates out of the user-facing request path.
- Stage 11-12: thin API routers and reduce legacy modules to small wrappers.
