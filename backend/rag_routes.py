"""Compatibility wrapper for the RAG API router."""

from app.api.dependencies import create_current_user_id_dependency
from app.api.routes.rag import create_rag_router
from app.core import security
from app.db.pool import database
from app.infrastructure.ai import RagEngine
from app.infrastructure.storage import FileStorage
from app.repositories.oltp import FileRepository, RagRouteRepository, UsageRepository, UserRepository
from app.services import UserService
from app.services.file_service import FileService
from app.services.rag_service import RagService
from app.services.usage_service import UsageService
from app.domain.subscriptions import DEFAULT_INTERNAL_TOKENS_PER_REQUEST

_user_repository = UserRepository(database)
_usage_repository = UsageRepository(database)
_usage_service = UsageService(_usage_repository, DEFAULT_INTERNAL_TOKENS_PER_REQUEST)
_current_user_id = create_current_user_id_dependency(
    token_dependency=security.oauth2,
    decode_token=security.decode_token,
    user_service=UserService(_user_repository, _usage_service),
)
rag_service = RagService(RagRouteRepository(database), RagEngine())
router = create_rag_router(
    current_user_id=_current_user_id,
    rag_service=rag_service,
    file_service=FileService(FileRepository(database), FileStorage()),
    usage_service=_usage_service,
)

__all__ = ["router", "rag_service"]
