"""FastAPI dependency factories."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException

from app.services.user_service import UserService


def create_current_user_id_dependency(
    *,
    token_dependency: Callable,
    decode_token: Callable[[str], int | None],
    user_service: UserService,
) -> Callable:
    async def current_user_id(token: str = Depends(token_dependency)) -> int:
        """Resolve the JWT to a user id, then verify the user still exists."""
        if not token:
            raise HTTPException(status_code=401, detail="Not authenticated")
        uid = decode_token(token)
        if not uid:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        user = await user_service.get_by_id(uid)
        if not user:
            raise HTTPException(
                status_code=401,
                detail="User no longer exists — please log in again",
            )
        if user.get("is_blocked"):
            raise HTTPException(status_code=403, detail="Аккаунт заблокирован администратором")
        return uid

    return current_user_id
